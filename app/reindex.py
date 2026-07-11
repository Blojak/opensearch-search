"""Rebuild the OpenSearch index from PostgreSQL (the source of truth).

OpenSearch is a derived index, so it can be thrown away and rebuilt at any
time. This drops and recreates the chunks index and re-chunks/re-embeds every
live document's current version straight from ``documents`` /
``document_versions``. Run with ``python -m app.reindex``.
"""

from __future__ import annotations

from app.chunking import chunk_text
from app.db import session_scope
from app.embedding import embed_passages
from app.ingestion import index_chunks
from app.models import Document, DocumentVersion
from app.opensearch_store import ensure_hybrid_pipeline, get_client, recreate_index


def rebuild_index() -> int:
    """Recreate the OpenSearch index and reindex all live documents.

    Returns the number of document versions reindexed.
    """
    client = get_client()
    recreate_index(client)
    ensure_hybrid_pipeline(client)

    count = 0
    with session_scope() as session:
        documents = (
            session.query(Document)
            .filter(Document.deleted_at.is_(None))
            .order_by(Document.created_at)
            .all()
        )
        for document in documents:
            version = (
                session.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version_number == document.current_version,
                )
                .one_or_none()
            )
            if version is None:
                continue
            chunks = chunk_text(version.body_text)
            if not chunks:
                continue
            vectors = embed_passages([c.text for c in chunks])
            index_chunks(client, document, version.version_number, chunks, vectors)
            count += 1
    return count


def main() -> None:
    """CLI entry point: rebuild the index and report how many documents."""
    total = rebuild_index()
    print(f"Reindexed {total} document(s) into OpenSearch.")


if __name__ == "__main__":
    main()
