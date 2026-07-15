import sqlite3

from app import audit


def test_connect_migrates_a_pre_existing_db_missing_a_newer_column(tmp_path):
    """Regression test for a real bug: CREATE TABLE IF NOT EXISTS only creates the
    table on a brand-new database — it silently does nothing to a table that already
    exists with an older schema (e.g. audit.db surviving in a Docker named volume
    across an image rebuild that added schema_validation_failures). Simulate that by
    creating the table with the pre-migration schema first, then connecting via
    audit.connect() the way the app does, and confirm it self-heals instead of
    raising sqlite3.OperationalError on the next record()."""
    db_path = str(tmp_path / "audit.db")
    old_schema_conn = sqlite3.connect(db_path)
    old_schema_conn.execute(
        """CREATE TABLE audit_log (
            request_id TEXT PRIMARY KEY,
            user_query TEXT NOT NULL,
            tool_calls TEXT NOT NULL,
            injection_flags TEXT NOT NULL,
            final_answer TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    old_schema_conn.commit()
    old_schema_conn.close()

    conn = audit.connect(db_path)
    audit.record(
        conn, "REQ-001", "which tickets?",
        tool_calls=[], injection_flags=[],
        final_answer={"recommendation": "x", "confidence": "low"},
        schema_validation_failures=[{"stage": "synthesis", "reason": "bad json"}],
    )
    entry = audit.get(conn, "REQ-001")
    assert entry["schema_validation_failures"] == [{"stage": "synthesis", "reason": "bad json"}]


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
