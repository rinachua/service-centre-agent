from pathlib import Path


def load_documents(docs_path: Path) -> dict[str, dict]:
    files = sorted(docs_path.glob("*.md"))
    documents = {}
    for i, file_path in enumerate(files, start=1):
        doc_id = f"DOC-{i:03d}"
        text = file_path.read_text()
        lines = text.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else file_path.stem
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else text
        documents[doc_id] = {"title": title, "body": body}
    return documents
