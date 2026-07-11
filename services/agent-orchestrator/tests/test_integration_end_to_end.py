import json

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeAnthropicClient, FakeResponse, FakeTextBlock, FakeToolUseBlock

URLS = dict(
    ticket_url="http://ticket-service:8001",
    equipment_url="http://equipment:8002",
    knowledge_url="http://knowledge:8003",
    recommendation_url="http://recommendation:8004",
)


def _build_app(tmp_path, anthropic_client):
    app = create_app(
        anthropic_client=anthropic_client,
        planner_model="claude-haiku-4-5-20251001",
        synthesis_model="claude-sonnet-5",
        audit_db_path=str(tmp_path / "audit.db"),
        static_dir=None,
        **URLS,
    )
    return TestClient(app)


@respx.mock
def test_end_to_end_prioritisation_query_sufficient_in_one_pass(tmp_path):
    """Plan call plans score_priority; synthesis call is sufficient on the first
    try. Exactly 2 Claude calls total — the common-case bounded-hybrid path."""
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[
            {
                "ticket_id": "TCK-002", "tool_id": "ETCH-07", "line": "Line-A",
                "process_area": "Etch", "title": "Repeat RF reflection alarm",
                "description": "second RF over-reflection alarm", "severity": "critical",
                "status": "open", "downtime_impact_hours": 3.0, "reported_by": "M. Lee",
                "created_at": "2026-07-11T06:40:00+00:00",
            },
        ])
    )
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        return_value=httpx.Response(200, json=[
            {"record_id": "HIST-012", "tool_id": "ETCH-07", "event_type": "alarm",
             "code": "RF-OVR-REFL", "description": "third occurrence",
             "date": "2026-07-10", "resolution": "escalated", "parts_replaced": "none"},
        ])
    )
    respx.post("http://recommendation:8004/priority-score").mock(
        return_value=httpx.Response(200, json=[
            {"ticket_id": "TCK-002", "score": 0.91, "breakdown": {}, "recurrence_count": 3},
        ])
    )

    plan_response = FakeResponse(content=[
        FakeToolUseBlock(name="score_priority", input={}, id="tu_1"),
    ])
    synthesis_json = json.dumps({
        "answer": {
            "recommendation": "Prioritise TCK-002 (ETCH-07) first: recurring RF alarm, high score.",
            "evidence": [
                {"source": "ticket-service", "record_id": "TCK-002", "detail": "critical, open"},
                {"source": "equipment-history-service", "record_id": "HIST-012", "detail": "3rd RF-OVR-REFL"},
            ],
            "assumptions": [],
            "confidence": "high",
            "next_action": "Dispatch RF engineer to ETCH-07 today.",
        },
        "sufficient": True,
        "additional_tool_request": None,
    })
    synthesis_response = FakeResponse(content=[FakeTextBlock(text=synthesis_json)])
    anthropic_client = FakeAnthropicClient([plan_response, synthesis_response])

    client = _build_app(tmp_path, anthropic_client)

    resp = client.post("/chat", json={"query": "Which open tickets should I prioritise today?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    assert answer["confidence"] == "high"
    assert all(e["verified"] for e in answer["evidence"])
    assert len(anthropic_client.calls) == 2

    request_id = resp.json()["request_id"]
    audit = client.get(f"/audit/{request_id}").json()
    assert audit["tool_calls"][0]["tool_name"] == "score_priority"


@respx.mock
def test_end_to_end_continues_when_a_downstream_service_is_down(tmp_path):
    """A planned tool call fails; execution still proceeds to synthesis with
    partial evidence rather than erroring out. Still 2 Claude calls."""
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    plan_response = FakeResponse(content=[
        FakeToolUseBlock(name="get_equipment_history", input={"tool_id": "ETCH-07"}, id="tu_1"),
    ])
    synthesis_json = json.dumps({
        "answer": {
            "recommendation": "Unable to review history; ticket data alone suggests escalation.",
            "evidence": [],
            "assumptions": ["equipment-history-service was unreachable"],
            "confidence": "low",
            "next_action": "Retry once the service is back.",
        },
        "sufficient": True,
        "additional_tool_request": None,
    })
    synthesis_response = FakeResponse(content=[FakeTextBlock(text=synthesis_json)])
    anthropic_client = FakeAnthropicClient([plan_response, synthesis_response])

    client = _build_app(tmp_path, anthropic_client)

    resp = client.post("/chat", json={"query": "Summarise ETCH-07 alarm history."})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    assert answer["confidence"] == "low"
    assert "unreachable" in answer["assumptions"][0]
    assert len(anthropic_client.calls) == 2


@respx.mock
def test_end_to_end_one_revision_round_when_synthesis_flags_insufficient(tmp_path):
    """Plan call only fetches tickets; synthesis judges that insufficient and
    requests one more tool call (equipment history); executor fetches it;
    revision synthesis returns the final answer. Exactly 3 Claude calls —
    the bounded hybrid's capped-revision path, end-to-end."""
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[
            {
                "ticket_id": "TCK-002", "tool_id": "ETCH-07", "line": "Line-A",
                "process_area": "Etch", "title": "Repeat RF reflection alarm",
                "description": "second RF over-reflection alarm", "severity": "critical",
                "status": "open", "downtime_impact_hours": 3.0, "reported_by": "M. Lee",
                "created_at": "2026-07-11T06:40:00+00:00",
            },
        ])
    )
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        return_value=httpx.Response(200, json=[
            {"record_id": "HIST-012", "tool_id": "ETCH-07", "event_type": "alarm",
             "code": "RF-OVR-REFL", "description": "third occurrence",
             "date": "2026-07-10", "resolution": "escalated", "parts_replaced": "none"},
        ])
    )

    plan_response = FakeResponse(content=[
        FakeToolUseBlock(name="get_tickets", input={}, id="tu_1"),
    ])
    insufficient_json = json.dumps({
        "answer": {
            "recommendation": "Need alarm history to confirm before recommending.",
            "evidence": [],
            "assumptions": [],
            "confidence": "low",
            "next_action": "Pending more evidence.",
        },
        "sufficient": False,
        "additional_tool_request": {
            "tool_name": "get_equipment_history",
            "input": {"tool_id": "ETCH-07"},
        },
    })
    insufficient_response = FakeResponse(content=[FakeTextBlock(text=insufficient_json)])
    revision_json = json.dumps({
        "recommendation": "Prioritise TCK-002 (ETCH-07): recurring RF alarm confirmed by history.",
        "evidence": [
            {"source": "ticket-service", "record_id": "TCK-002", "detail": "critical, open"},
            {"source": "equipment-history-service", "record_id": "HIST-012", "detail": "3rd RF-OVR-REFL"},
        ],
        "assumptions": [],
        "confidence": "high",
        "next_action": "Dispatch RF engineer to ETCH-07 today.",
    })
    revision_response = FakeResponse(content=[FakeTextBlock(text=revision_json)])
    anthropic_client = FakeAnthropicClient([plan_response, insufficient_response, revision_response])

    client = _build_app(tmp_path, anthropic_client)

    resp = client.post("/chat", json={"query": "Which open tickets should I prioritise today?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    assert answer["confidence"] == "high"
    assert all(e["verified"] for e in answer["evidence"])
    assert len(anthropic_client.calls) == 3

    request_id = resp.json()["request_id"]
    audit = client.get(f"/audit/{request_id}").json()
    tool_names = [tc["tool_name"] for tc in audit["tool_calls"]]
    assert tool_names == ["get_tickets", "get_equipment_history"]
