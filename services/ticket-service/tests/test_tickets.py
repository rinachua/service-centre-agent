from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

SEED_PATH = Path(__file__).parent.parent.parent.parent / "data" / "seed"


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), seed_path=SEED_PATH)
    return TestClient(app)


def test_health():
    app = create_app(db_path=":memory:", seed_path=SEED_PATH)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_tickets_returns_seeded_data(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tickets")
    assert resp.status_code == 200
    tickets = resp.json()
    assert len(tickets) >= 10
    assert any(t["ticket_id"] == "TCK-001" for t in tickets)


def test_list_tickets_filters_by_status_and_tool_id(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tickets", params={"status": "open", "tool_id": "ETCH-07"})
    assert resp.status_code == 200
    tickets = resp.json()
    assert len(tickets) > 0
    assert all(t["status"] == "open" and t["tool_id"] == "ETCH-07" for t in tickets)


def test_get_ticket_by_id(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tickets/TCK-001")
    assert resp.status_code == 200
    assert resp.json()["ticket_id"] == "TCK-001"


def test_get_ticket_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tickets/TCK-999")
    assert resp.status_code == 404


def test_create_followup_then_list_it(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/tickets/TCK-001/followups",
        json={"summary": "Reseated RF cable", "root_cause": "Loose connector", "next_action": "Monitor for recurrence"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ticket_id"] == "TCK-001"
    assert body["followup_id"].startswith("FUP-")

    list_resp = client.get("/tickets/TCK-001/followups")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


def test_create_followup_404_for_unknown_ticket(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/tickets/TCK-999/followups",
        json={"summary": "x", "root_cause": "y", "next_action": "z"},
    )
    assert resp.status_code == 404
