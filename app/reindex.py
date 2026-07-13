"""Rebuild or partially reindex the OpenSearch index from PostgreSQL.

OpenSearch is a derived index, so it can be thrown away and rebuilt at any time
from ``documents`` / ``document_versions`` (the source of truth). A full rebuild
is the exception (mapping change, disaster recovery); day to day, reindex only
the documents that changed with the partial helpers.

Embeddings dominate the cost, so chunks are embedded in batches across
documents and indexed with ``refresh=False``, refreshing the index once at the
end.

CLI::

    python -m app.reindex                     # full rebuild (drops + recreates)
    python -m app.reindex --document <uuid>    # one document's current version
    python -m app.reindex --verfahren <uuid>   # all live documents of a verfahren
"""

from __future__ import annotations

import argparse
import uuid

from opensearchpy.helpers import bulk

from app.chunking import chunk_text
from app.config import get_settings
from app.db import session_scope
from app.embedding import embed_passages
from app.ingestion import chunk_action
from app.models import Document, DocumentVersion
from app.opensearch_store import (
    FIELD_DOCUMENT_ID,
    ensure_hybrid_pipeline,
    ensure_index,
    get_client,
    recreate_index,
)

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


def _drop_document_chunks(client, document_id: uuid.UUID) -> None:
    """Remove all OpenSearch chunks of a document (used before re-indexing it)."""
    settings = get_settings()
    client.delete_by_query(
        index=settings.opensearch_index,
        body={"query": {"term": {FIELD_DOCUMENT_ID: str(document_id)}}},
        refresh=False,
    )


def _reindex_documents(client, session, documents, batch_size, drop_existing) -> int:
    """Embed and index the current version of each document, batched.

    Chunks are accumulated across documents and flushed in ``batch_size`` groups
    with ``refresh=False``. When ``drop_existing`` is set, each document's old
    chunks are removed first (for a partial reindex over a live index). The
    caller refreshes the index once afterwards. Returns how many documents were
    indexed.
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
        if drop_existing:
            _drop_document_chunks(client, document.id)
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
        count = _reindex_documents(
            client, session, documents, batch_size, drop_existing=False
        )
    client.indices.refresh(index=settings.opensearch_index)
    return count


def reindex_document(document_id: uuid.UUID) -> bool:
    """Reindex a single document's current version (drops its old chunks first).

    Returns ``False`` if the document does not exist or is soft-deleted.
    """
    settings = get_settings()
    client = get_client()
    ensure_index(client)

    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None or document.deleted_at is not None:
            return False
        count = _reindex_documents(
            client, session, [document], _EMBED_BATCH, drop_existing=True
        )
    client.indices.refresh(index=settings.opensearch_index)
    return count > 0


def reindex_verfahren(verfahren_id: uuid.UUID, batch_size: int = _EMBED_BATCH) -> int:
    """Reindex all live documents of a verfahren. Returns the doc count."""
    settings = get_settings()
    client = get_client()
    ensure_index(client)

    with session_scope() as session:
        documents = (
            session.query(Document)
            .filter(
                Document.verfahren_id == verfahren_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at)
            .all()
        )
        count = _reindex_documents(
            client, session, documents, batch_size, drop_existing=True
        )
    client.indices.refresh(index=settings.opensearch_index)
    return count


def main() -> None:
    """CLI: full rebuild, or a partial reindex by document / verfahren."""
    parser = argparse.ArgumentParser(
        description="Rebuild or partially reindex the OpenSearch index from Postgres.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--document", metavar="UUID", help="reindex one document by id")
    group.add_argument(
        "--verfahren", metavar="UUID", help="reindex all live documents of a verfahren"
    )
    args = parser.parse_args()

    if args.document:
        ok = reindex_document(uuid.UUID(args.document))
        print("Reindexed 1 document." if ok else "Document not found or deleted.")
    elif args.verfahren:
        total = reindex_verfahren(uuid.UUID(args.verfahren))
        print(f"Reindexed {total} document(s) of the verfahren.")
    else:
        total = rebuild_index()
        print(f"Reindexed {total} document(s) into OpenSearch.")


if __name__ == "__main__":
    main()
