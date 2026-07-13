from app.main import create_app
from fastapi.testclient import TestClient


def test_search_endpoint_returns_results(tmp_path):
    (tmp_path / "a.md").write_text("# RF Guide\n\nRF generator troubleshooting.")
    app = create_app(docs_path=tmp_path)
    client = TestClient(app)
    resp = client.get("/search", params={"q": "RF generator", "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["doc_id"] == "DOC-001"
    assert "score" in body[0]


def test_get_document_by_id(tmp_path):
    (tmp_path / "a.md").write_text("# Doc\n\nBody text.")
    app = create_app(docs_path=tmp_path)
    client = TestClient(app)
    resp = client.get("/documents/DOC-001")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Doc"


def test_get_document_404_for_unknown_id(tmp_path):
    (tmp_path / "a.md").write_text("# Doc\n\nBody.")
    app = create_app(docs_path=tmp_path)
    client = TestClient(app)
    resp = client.get("/documents/DOC-999")
    assert resp.status_code == 404
