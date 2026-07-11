from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

SEED_PATH = Path(__file__).parent.parent.parent.parent / "data" / "seed"


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), seed_path=SEED_PATH)
    return TestClient(app)


def test_health(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_assets_returns_at_least_five(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/assets")
    assert resp.status_code == 200
    assert len(resp.json()) >= 5


def test_get_asset_by_tool_id(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/assets/ETCH-07")
    assert resp.status_code == 200
    assert resp.json()["tool_id"] == "ETCH-07"


def test_get_asset_404_for_unknown_tool(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/assets/NOPE-99")
    assert resp.status_code == 404


def test_get_history_for_tool(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/assets/ETCH-07/history")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) >= 3
    assert all(r["tool_id"] == "ETCH-07" for r in records)


def test_get_history_404_for_unknown_tool(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/assets/NOPE-99/history")
    assert resp.status_code == 404


def test_search_history_by_keyword(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/history/search", params={"q": "RF-OVR-REFL"})
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) >= 3
    assert all("RF-OVR-REFL" in r["code"] for r in records)


def test_search_history_no_match_returns_empty_list(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/history/search", params={"q": "zzz-nonexistent"})
    assert resp.status_code == 200
    assert resp.json() == []
