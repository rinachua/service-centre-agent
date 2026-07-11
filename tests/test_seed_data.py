import json
from pathlib import Path

SEED_DIR = Path(__file__).parent.parent / "data" / "seed"


def test_assets_seed_has_at_least_five_with_required_fields():
    assets = json.loads((SEED_DIR / "assets.json").read_text())
    assert len(assets) >= 5
    required = {"tool_id", "line", "process_area", "status", "recent_downtime_hours_7d"}
    for asset in assets:
        assert required.issubset(asset.keys())


def test_tickets_seed_has_at_least_ten_with_required_fields():
    tickets = json.loads((SEED_DIR / "tickets.json").read_text())
    assert len(tickets) >= 10
    required = {
        "ticket_id", "tool_id", "line", "process_area", "title", "description",
        "severity", "status", "downtime_impact_hours", "reported_by", "created_at",
    }
    for ticket in tickets:
        assert required.issubset(ticket.keys())


def test_history_seed_has_at_least_ten_with_required_fields():
    history = json.loads((SEED_DIR / "history.json").read_text())
    assert len(history) >= 10
    required = {
        "record_id", "tool_id", "event_type", "code", "description",
        "date", "resolution", "parts_replaced",
    }
    for record in history:
        assert required.issubset(record.keys())


def test_tickets_reference_known_assets():
    assets = json.loads((SEED_DIR / "assets.json").read_text())
    tickets = json.loads((SEED_DIR / "tickets.json").read_text())
    known_tool_ids = {a["tool_id"] for a in assets}
    for ticket in tickets:
        assert ticket["tool_id"] in known_tool_ids


def test_history_references_known_assets():
    assets = json.loads((SEED_DIR / "assets.json").read_text())
    history = json.loads((SEED_DIR / "history.json").read_text())
    known_tool_ids = {a["tool_id"] for a in assets}
    for record in history:
        assert record["tool_id"] in known_tool_ids


def test_at_least_three_knowledge_documents():
    docs_dir = SEED_DIR / "docs"
    md_files = list(docs_dir.glob("*.md"))
    assert len(md_files) >= 3
