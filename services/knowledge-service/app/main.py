import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.documents import load_documents
from app.logging_middleware import RequestIDMiddleware, configure_logging
from app.search import TfidfIndex


def create_app(docs_path: Path) -> FastAPI:
    configure_logging("knowledge-service")
    app = FastAPI(title="knowledge-service")
    app.add_middleware(RequestIDMiddleware)
    documents = load_documents(docs_path)
    # Index title + body, not body alone: otherwise a query matching only words that
    # appear in a doc's title (e.g. "troubleshooting" in a title like "... Troubleshooting
    # Guide") scores 0 against every document, since the title is stripped out of `body`
    # by load_documents() and would never be indexed at all.
    index = TfidfIndex(
        {doc_id: f"{doc['title']}\n{doc['body']}" for doc_id, doc in documents.items()}
    )

    @app.get("/health")
    def health():
        if not documents:
            raise HTTPException(status_code=503, detail="no documents loaded")
        return {"status": "ok"}

    @app.get("/search")
    def search(q: str, top_k: int = 5):
        results = index.search(q, top_k)
        return [
            {
                "doc_id": doc_id,
                "title": documents[doc_id]["title"],
                "excerpt": documents[doc_id]["body"][:240].strip() + "...",
                "score": round(score, 4),
            }
            for doc_id, score in results
        ]

    @app.get("/documents/{doc_id}")
    def get_document(doc_id: str):
        if doc_id not in documents:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        return {"doc_id": doc_id, **documents[doc_id]}

    return app


app = create_app(docs_path=Path(os.environ.get("SEED_PATH", "/app/data/seed")) / "docs")
