import json

import anthropic
import httpx
import respx
from app.main import _build_anthropic_client, create_app
from app.offline_responder import OfflineResponder
from fastapi.testclient import TestClient

from tests.fakes import FakeAnthropicClient, FakeResponse, FakeTextBlock

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


def test_build_anthropic_client_returns_offline_responder_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(_build_anthropic_client(), OfflineResponder)


def test_build_anthropic_client_returns_real_client_when_api_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert isinstance(_build_anthropic_client(), anthropic.Anthropic)
