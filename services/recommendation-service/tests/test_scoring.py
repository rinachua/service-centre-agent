from datetime import datetime, timezone

from app.scoring import rank_tickets, recurrence_count, score_ticket


def _ticket(ticket_id, tool_id, severity, downtime, created_at, description="issue"):
    return {
        "ticket_id": ticket_id,
        "tool_id": tool_id,
        "severity": severity,
        "downtime_impact_hours": downtime,
        "created_at": created_at,
        "description": description,
    }


def _history(record_id, tool_id, code, description):
    return {"record_id": record_id, "tool_id": tool_id, "code": code, "description": description}


def test_critical_high_downtime_ticket_outranks_low_severity_ticket():
    now = datetime.now(timezone.utc).isoformat()
    tickets = [
        _ticket("TCK-A", "ETCH-07", "critical", 10.0, now, "rf alarm"),
        _ticket("TCK-B", "CLEAN-11", "low", 0.5, now, "particle count"),
    ]
    ranked = rank_tickets(tickets, [])
    assert ranked[0]["ticket_id"] == "TCK-A"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_recurrence_count_matches_history_with_shared_keywords():
    ticket = _ticket("TCK-A", "ETCH-07", "critical", 3.0, datetime.now(timezone.utc).isoformat(), "rf alarm reflection")
    history = [
        _history("HIST-001", "ETCH-07", "RF-OVR-REFL", "rf alarm reflection issue"),
        _history("HIST-002", "CMP-02", "CMP-HEAD-PRESS", "unrelated"),
    ]
    count = recurrence_count(ticket, history)
    assert count == 1


def test_score_ticket_breakdown_sums_to_score():
    ticket = _ticket("TCK-A", "ETCH-07", "high", 5.0, datetime.now(timezone.utc).isoformat())
    result = score_ticket(ticket, [], max_downtime=10.0, max_age=30.0)
    breakdown = result["breakdown"]
    expected = round(
        0.4 * breakdown["severity"] + 0.3 * breakdown["downtime"]
        + 0.2 * breakdown["recurrence"] + 0.1 * breakdown["age"],
        4,
    )
    assert result["score"] == expected


def test_rank_tickets_empty_list_returns_empty():
    assert rank_tickets([], []) == []
