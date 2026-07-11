from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app


def test_priority_score_endpoint_ranks_tickets():
    client = TestClient(create_app())
    now = datetime.now(timezone.utc).isoformat()
    resp = client.post(
        "/priority-score",
        json={
            "tickets": [
                {"ticket_id": "TCK-A", "tool_id": "ETCH-07", "severity": "critical",
                 "downtime_impact_hours": 10.0, "created_at": now, "description": "rf alarm"},
                {"ticket_id": "TCK-B", "tool_id": "CLEAN-11", "severity": "low",
                 "downtime_impact_hours": 0.5, "created_at": now, "description": "particle"},
            ],
            "history": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["ticket_id"] == "TCK-A"


def test_priority_score_endpoint_empty_tickets():
    client = TestClient(create_app())
    resp = client.post("/priority-score", json={"tickets": [], "history": []})
    assert resp.status_code == 200
    assert resp.json() == []
