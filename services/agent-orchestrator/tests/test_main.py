import json

import anthropic
import httpx
import respx
from app.main import _build_anthropic_client, create_app
from app.offline_responder import OfflineResponder
from fastapi.testclient import TestClient

from tests.fakes import FakeAnthropicClient, FakeResponse, FakeTextBlock, FakeToolUseBlock

URLS = {
    "ticket_url": "http://ticket-service:8001",
    "equipment_url": "http://equipment:8002",
    "knowledge_url": "http://knowledge:8003",
    "recommendation_url": "http://recommendation:8004",
}


def _build_client(tmp_path, anthropic_client):
    app = create_app(
        anthropic_client=anthropic_client,
        planner_model="claude-haiku-4-5-20251001",
        synthesis_model="claude-sonnet-5",
        audit_db_path=str(tmp_path / "audit.db"),
        static_dir=None,
        **URLS,
    )
    return TestClient(app)


def test_health(tmp_path):
    client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_returns_503_when_audit_database_unavailable(tmp_path):
    """/health must reflect a genuinely broken audit DB connection, not just process
    liveness — regression test for the health-check-always-returns-ok gap."""
    from unittest.mock import MagicMock, patch

    broken_conn = MagicMock()
    broken_conn.execute.side_effect = Exception("database is locked")
    with patch("app.audit.connect", return_value=broken_conn):
        client = _build_client(tmp_path, FakeAnthropicClient([]))
        resp = client.get("/health")
    assert resp.status_code == 503
    assert "audit database unavailable" in resp.json()["detail"]


def _plan_and_synthesis(recommendation="x"):
    plan = FakeResponse(content=[FakeToolUseBlock(name="get_tickets", input={}, id="tu_1")])
    synthesis = FakeResponse(content=[FakeTextBlock(text=json.dumps({
        "answer": {
            "recommendation": recommendation, "evidence": [], "assumptions": [],
            "confidence": "low", "next_action": "y",
        },
        "sufficient": True, "additional_tool_request": None,
    }))])
    return [plan, synthesis]


def test_chat_passes_x_user_role_header_through_to_the_synthesis_prompt(tmp_path):
    client_double = FakeAnthropicClient(_plan_and_synthesis())
    client = _build_client(tmp_path, client_double)

    resp = client.post(
        "/chat", json={"query": "prioritise tickets"}, headers={"X-User-Role": "manager"},
    )

    assert resp.status_code == 200
    synthesis_call = client_double.calls[1]
    assert "Audience: a manager" in synthesis_call["system"]


def test_chat_defaults_to_engineer_role_when_header_is_missing_or_unknown(tmp_path):
    client_double = FakeAnthropicClient(_plan_and_synthesis())
    client = _build_client(tmp_path, client_double)

    resp = client.post(
        "/chat", json={"query": "prioritise tickets"}, headers={"X-User-Role": "director"},
    )

    assert resp.status_code == 200
    synthesis_call = client_double.calls[1]
    assert "Audience: an engineer" in synthesis_call["system"]


@respx.mock
def test_chat_endpoint_returns_structured_answer_and_persists_audit(tmp_path):
    plan_response = FakeResponse(content=[])
    synthesis_json = json.dumps({
        "answer": {
            "recommendation": "Prioritise TCK-002.",
            "evidence": [],
            "assumptions": [],
            "confidence": "medium",
            "next_action": "Investigate ETCH-07.",
        },
        "sufficient": True,
        "additional_tool_request": None,
    })
    fake_client = FakeAnthropicClient([
        plan_response,
        FakeResponse(content=[FakeTextBlock(text=synthesis_json)]),
    ])
    test_client = _build_client(tmp_path, fake_client)

    resp = test_client.post("/chat", json={"query": "which tickets first?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"]["recommendation"] == "Prioritise TCK-002."
    request_id = body["request_id"]

    audit_resp = test_client.get(f"/audit/{request_id}")
    assert audit_resp.status_code == 200
    assert audit_resp.json()["user_query"] == "which tickets first?"


def test_audit_endpoint_404_for_unknown_request_id(tmp_path):
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/audit/REQ-does-not-exist")
    assert resp.status_code == 404


@respx.mock
def test_save_followup_proxies_to_ticket_service(tmp_path):
    respx.post("http://ticket-service:8001/tickets/TCK-001/followups").mock(
        return_value=httpx.Response(201, json={
            "followup_id": "FUP-1", "ticket_id": "TCK-001", "summary": "s",
            "root_cause": "r", "next_action": "n", "created_at": "2026-07-11T00:00:00+00:00",
        })
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.post(
        "/tickets/TCK-001/followups",
        json={"summary": "s", "root_cause": "r", "next_action": "n"},
    )
    assert resp.status_code == 201
    assert resp.json()["followup_id"] == "FUP-1"


@respx.mock
def test_save_followup_returns_502_when_ticket_service_unreachable(tmp_path):
    respx.post("http://ticket-service:8001/tickets/TCK-001/followups").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.post(
        "/tickets/TCK-001/followups",
        json={"summary": "s", "root_cause": "r", "next_action": "n"},
    )
    assert resp.status_code == 502


@respx.mock
def test_save_followup_passes_through_404_for_unknown_ticket_id(tmp_path):
    """Regression test for a real bug caught live: a followup_note.ticket_id that
    doesn't match a real ticket (e.g. a history record_id like HIST-012 mistaken for
    a ticket_id) makes ticket-service itself return 404. That used to get collapsed
    into a generic 502 by save_followup's blanket httpx.HTTPError handler, hiding a
    legitimate, actionable 404 behind an opaque orchestrator-level failure code."""
    respx.post("http://ticket-service:8001/tickets/HIST-012/followups").mock(
        return_value=httpx.Response(404, json={"detail": "Ticket HIST-012 not found"})
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.post(
        "/tickets/HIST-012/followups",
        json={"summary": "s", "root_cause": "r", "next_action": "n"},
    )
    assert resp.status_code == 404


def test_list_audit_returns_empty_list_when_nothing_logged_yet(tmp_path):
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/audit")
    assert resp.status_code == 200
    assert resp.json() == []


@respx.mock
def test_list_audit_returns_recent_entries_after_a_chat_request(tmp_path):
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "tool_id": "ETCH-07"}])
    )
    fake_client = FakeAnthropicClient(_plan_and_synthesis("Prioritise TCK-002."))
    test_client = _build_client(tmp_path, fake_client)
    test_client.post("/chat", json={"query": "which tickets first?"})

    resp = test_client.get("/audit?limit=5")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["user_query"] == "which tickets first?"
    assert entries[0]["confidence"] == "low"


@respx.mock
def test_dashboard_tickets_proxies_to_ticket_service(tmp_path):
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "status": "open"}])
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/dashboard/tickets")
    assert resp.status_code == 200
    assert resp.json() == [{"ticket_id": "TCK-001", "status": "open"}]


@respx.mock
def test_dashboard_tickets_returns_502_when_ticket_service_unreachable(tmp_path):
    respx.get("http://ticket-service:8001/tickets").mock(side_effect=httpx.ConnectError("refused"))
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/dashboard/tickets")
    assert resp.status_code == 502


@respx.mock
def test_dashboard_tickets_recovers_from_one_transient_failure(tmp_path):
    """Regression test: dashboard endpoints previously made one-shot httpx calls with
    no retry, unlike every other downstream call in the codebase (ToolExecutor has
    always retried once). A single connection blip used to surface as a 502 to the
    dashboard; it should now recover transparently, same as a /chat tool call would."""
    respx.get("http://ticket-service:8001/tickets").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=[{"ticket_id": "TCK-001"}])]
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/dashboard/tickets")
    assert resp.status_code == 200
    assert resp.json() == [{"ticket_id": "TCK-001"}]


@respx.mock
def test_dashboard_assets_proxies_to_equipment_history_service(tmp_path):
    respx.get("http://equipment:8002/assets").mock(
        return_value=httpx.Response(200, json=[{"tool_id": "ETCH-07", "status": "in_use"}])
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/dashboard/assets")
    assert resp.status_code == 200
    assert resp.json() == [{"tool_id": "ETCH-07", "status": "in_use"}]


@respx.mock
def test_dashboard_priority_reuses_score_priority_tool(tmp_path):
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "tool_id": "ETCH-07"}])
    )
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("http://recommendation:8004/priority-score").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "score": 0.5}])
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/dashboard/priority")
    assert resp.status_code == 200
    assert resp.json() == [{"ticket_id": "TCK-001", "score": 0.5}]


@respx.mock
def test_dashboard_priority_returns_502_when_recommendation_service_unreachable(tmp_path):
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "tool_id": "ETCH-07"}])
    )
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("http://recommendation:8004/priority-score").mock(
        side_effect=httpx.ConnectError("refused")
    )
    test_client = _build_client(tmp_path, FakeAnthropicClient([]))
    resp = test_client.get("/dashboard/priority")
    assert resp.status_code == 502


def test_build_anthropic_client_returns_offline_responder_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(_build_anthropic_client(), OfflineResponder)


def test_build_anthropic_client_returns_real_client_when_api_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert isinstance(_build_anthropic_client(), anthropic.Anthropic)
