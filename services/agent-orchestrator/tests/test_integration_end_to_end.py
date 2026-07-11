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


@respx.mock
def test_end_to_end_prioritisation_query(tmp_path):
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

    tool_call_response = FakeResponse(content=[
        FakeToolUseBlock(name="score_priority", input={}, id="tu_1"),
    ])
    final_json = json.dumps({
        "recommendation": "Prioritise TCK-002 (ETCH-07) first: recurring RF alarm, high score.",
        "evidence": [
            {"source": "ticket-service", "record_id": "TCK-002", "detail": "critical, open"},
            {"source": "equipment-history-service", "record_id": "HIST-012", "detail": "3rd RF-OVR-REFL"},
        ],
        "assumptions": [],
        "confidence": "high",
        "next_action": "Dispatch RF engineer to ETCH-07 today.",
    })
    final_response = FakeResponse(content=[FakeTextBlock(text=final_json)])
    anthropic_client = FakeAnthropicClient([tool_call_response, final_response])

    app = create_app(
        anthropic_client=anthropic_client,
        model="claude-sonnet-5",
        audit_db_path=str(tmp_path / "audit.db"),
        static_dir=None,
        **URLS,
    )
    client = TestClient(app)

    resp = client.post("/chat", json={"query": "Which open tickets should I prioritise today?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    assert answer["confidence"] == "high"
    assert all(e["verified"] for e in answer["evidence"])

    request_id = resp.json()["request_id"]
    audit = client.get(f"/audit/{request_id}").json()
    assert audit["tool_calls"][0]["tool_name"] == "score_priority"


@respx.mock
def test_end_to_end_continues_when_a_downstream_service_is_down(tmp_path):
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    tool_call_response = FakeResponse(content=[
        FakeToolUseBlock(name="get_equipment_history", input={"tool_id": "ETCH-07"}, id="tu_1"),
    ])
    final_json = json.dumps({
        "recommendation": "Unable to review history; ticket data alone suggests escalation.",
        "evidence": [],
        "assumptions": ["equipment-history-service was unreachable"],
        "confidence": "low",
        "next_action": "Retry once the service is back.",
    })
    final_response = FakeResponse(content=[FakeTextBlock(text=final_json)])
    anthropic_client = FakeAnthropicClient([tool_call_response, final_response])

    app = create_app(
        anthropic_client=anthropic_client,
        model="claude-sonnet-5",
        audit_db_path=str(tmp_path / "audit.db"),
        static_dir=None,
        **URLS,
    )
    client = TestClient(app)

    resp = client.post("/chat", json={"query": "Summarise ETCH-07 alarm history."})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    assert answer["confidence"] == "low"
    assert "unreachable" in answer["assumptions"][0]
