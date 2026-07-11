"""Rebuild the OpenSearch index from PostgreSQL (the source of truth).

OpenSearch is a derived index, so it can be thrown away and rebuilt at any time
from ``documents`` / ``document_versions``. Embeddings dominate the cost, so
chunks are embedded in batches across documents and indexed with
``refresh=False``, refreshing the index once at the end. Run with
``python -m app.reindex``.
"""

from __future__ import annotations

from opensearchpy.helpers import bulk

from app.chunking import chunk_text
from app.config import get_settings
from app.db import session_scope
from app.embedding import embed_passages
from app.ingestion import chunk_action
from app.models import Document, DocumentVersion
from app.opensearch_store import ensure_hybrid_pipeline, get_client, recreate_index

# How many chunks to embed + bulk-index per flush. Larger batches mean fewer
# (more efficient) embedding calls at the cost of more memory.
_EMBED_BATCH = 256


def _current_version(session, document: Document) -> DocumentVersion | None:
    """The document's current DocumentVersion, or ``None`` if missing."""
    return (
        session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_number == document.current_version,
        )
        .one_or_none()
    )


def _reindex_documents(client, session, documents, batch_size) -> int:
    """Embed and index the current version of each document, batched.

    Chunks are accumulated across documents and flushed in ``batch_size`` groups
    with ``refresh=False``. The caller refreshes the index once afterwards.
    Returns how many documents were indexed.
    """
    pending: list[tuple[Document, int, object]] = []

    def flush() -> None:
        if not pending:
            return
        vectors = embed_passages([c.text for (_, _, c) in pending])
        actions = [
            chunk_action(doc, vnum, chunk, vector)
            for (doc, vnum, chunk), vector in zip(pending, vectors)
        ]
        bulk(client, actions, refresh=False)
        pending.clear()

    count = 0
    for document in documents:
        version = _current_version(session, document)
        if version is None:
            continue
        chunks = chunk_text(version.body_text)
        if not chunks:
            continue
        for chunk in chunks:
            pending.append((document, version.version_number, chunk))
            if len(pending) >= batch_size:
                flush()
        count += 1
    flush()
    return count


def rebuild_index(batch_size: int = _EMBED_BATCH) -> int:
    """Drop, recreate and repopulate the whole index. Returns the doc count."""
    settings = get_settings()
    client = get_client()
    recreate_index(client)
    ensure_hybrid_pipeline(client)

    with session_scope() as session:
        documents = (
            session.query(Document)
            .filter(Document.deleted_at.is_(None))
            .order_by(Document.created_at)
            .all()
        )
        count = _reindex_documents(client, session, documents, batch_size)
    client.indices.refresh(index=settings.opensearch_index)
    return count


def main() -> None:
    """CLI entry point: rebuild the index and report how many documents."""
    total = rebuild_index()
    print(f"Reindexed {total} document(s) into OpenSearch.")


if __name__ == "__main__":
    main()
