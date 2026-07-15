from app import audit


def test_record_and_get_round_trip_includes_schema_validation_failures():
    conn = audit.connect(":memory:")
    audit.record(
        conn, "REQ-001", "which tickets?",
        tool_calls=[{"tool_name": "get_tickets", "input": {}, "result": [], "error": None}],
        injection_flags=[],
        final_answer={"recommendation": "x", "confidence": "low"},
        schema_validation_failures=[{"stage": "synthesis", "reason": "response was not valid JSON"}],
    )
    entry = audit.get(conn, "REQ-001")
    assert entry["schema_validation_failures"] == [
        {"stage": "synthesis", "reason": "response was not valid JSON"}
    ]


def test_list_recent_returns_summary_rows_newest_first():
    conn = audit.connect(":memory:")
    audit.record(
        conn, "REQ-001", "first query", tool_calls=[], injection_flags=[],
        final_answer={"recommendation": "x", "confidence": "low"},
    )
    audit.record(
        conn, "REQ-002", "second query", tool_calls=[], injection_flags=["suspicious text"],
        final_answer={"recommendation": "y", "confidence": "high"},
    )
    entries = audit.list_recent(conn, limit=10)
    assert [e["request_id"] for e in entries] == ["REQ-002", "REQ-001"]
    assert entries[0]["confidence"] == "high"
    assert entries[0]["had_injection_flags"] is True
    assert entries[1]["had_injection_flags"] is False


def test_list_recent_respects_limit():
    conn = audit.connect(":memory:")
    for i in range(5):
        audit.record(
            conn, f"REQ-{i}", "q", tool_calls=[], injection_flags=[],
            final_answer={"recommendation": "x", "confidence": "low"},
        )
    assert len(audit.list_recent(conn, limit=3)) == 3


def test_record_defaults_schema_validation_failures_to_empty_list():
    """Callers that don't pass schema_validation_failures (the pre-existing call
    shape) must not break — defaults to an empty list, not a missing key."""
    conn = audit.connect(":memory:")
    audit.record(
        conn, "REQ-002", "which tickets?",
        tool_calls=[], injection_flags=[], final_answer={"recommendation": "x"},
    )
    entry = audit.get(conn, "REQ-002")
    assert entry["schema_validation_failures"] == []
