from app.documents import load_documents
from app.search import TfidfIndex


def test_load_documents_extracts_title_and_body(tmp_path):
    doc_file = tmp_path / "sample.md"
    doc_file.write_text("# Sample Title\n\nSome body text about RF generators.")
    documents = load_documents(tmp_path)
    assert documents["DOC-001"]["title"] == "Sample Title"
    assert "RF generators" in documents["DOC-001"]["body"]


def test_search_ranks_relevant_document_first(tmp_path):
    (tmp_path / "a.md").write_text("# RF Guide\n\nRF generator over-reflection troubleshooting steps.")
    (tmp_path / "b.md").write_text("# Unrelated\n\nWet clean particle count procedures.")
    documents = load_documents(tmp_path)
    index = TfidfIndex({doc_id: doc["body"] for doc_id, doc in documents.items()})
    results = index.search("RF over-reflection alarm", top_k=2)
    assert results[0][0] == "DOC-001"


def test_search_returns_empty_for_no_match(tmp_path):
    (tmp_path / "a.md").write_text("# Doc\n\nSome content about wafers.")
    documents = load_documents(tmp_path)
    index = TfidfIndex({doc_id: doc["body"] for doc_id, doc in documents.items()})
    results = index.search("zzz nonexistent term qqq", top_k=5)
    assert results == []
