"""Ingestion pipeline.

Flow: read text -> compute content hash (dedup) -> split into chunks -> embed
-> bulk-index one OpenSearch document per chunk, each carrying the chunk text,
its embedding and the denormalized document metadata.

OpenSearch is the single store, so there is no separate metadata database. The
sha256 content hash doubles as the document id; each chunk's OpenSearch ``_id``
is ``"{doc_id}-{chunk_index}"`` so re-ingesting a document is idempotent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from opensearchpy.helpers import bulk

from app.chunking import chunk_text
from app.embedding import embed_passages
from app.enums import Classification, DocType, Language
from app.opensearch_store import (
    FIELD_CHUNK_INDEX,
    FIELD_CLASSIFICATION,
    FIELD_CREATED_AT,
    FIELD_DOC_ID,
    FIELD_DOC_TYPE,
    FIELD_EMBEDDING,
    FIELD_EXTRA,
    FIELD_FILENAME,
    FIELD_INGESTED_AT,
    FIELD_LANGUAGE,
    FIELD_MIME_TYPE,
    FIELD_SIZE_BYTES,
    FIELD_SOURCE,
    FIELD_TEXT,
    FIELD_TITLE,
    get_client,
)
from app.config import get_settings


@dataclass
class DocumentMeta:
    """Caller-supplied metadata for a document to ingest."""

    filename: str
    mime_type: str = "text/plain"
    title: str | None = None
    language: Language = Language.UNKNOWN
    doc_type: DocType = DocType.OTHER
    classification: Classification = Classification.INTERNAL
    created_at: datetime | None = None
    source: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class IngestResult:
    """Outcome of an ingestion call."""

    document_id: str  # sha256 content hash
    filename: str
    num_chunks: int
    deduplicated: bool  # True if the document already existed (by hash)


def compute_hash(content: str) -> str:
    """sha256 over the UTF-8 bytes of the raw content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_document_file(path: str | Path) -> tuple[str, str]:
    """Read a supported file and return ``(text, mime_type)``.

    Supports ``.txt`` and (optionally) ``.pdf`` via pypdf. Everything else is
    treated as plain UTF-8 text.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text, "application/pdf"
    return path.read_text(encoding="utf-8"), "text/plain"


def _count_chunks(client, doc_id: str) -> int:
    """How many chunks of ``doc_id`` already exist in the index."""
    settings = get_settings()
    resp = client.count(
        index=settings.opensearch_index,
        body={"query": {"term": {FIELD_DOC_ID: doc_id}}},
    )
    return int(resp["count"])


def _base_metadata(meta: DocumentMeta, doc_id: str, size_bytes: int) -> dict:
    """Denormalized document metadata copied onto every chunk document."""
    data = {
        FIELD_DOC_ID: doc_id,
        FIELD_FILENAME: meta.filename,
        FIELD_TITLE: meta.title,
        FIELD_MIME_TYPE: meta.mime_type,
        FIELD_SIZE_BYTES: size_bytes,
        FIELD_LANGUAGE: meta.language.value,
        FIELD_DOC_TYPE: meta.doc_type.value,
        FIELD_CLASSIFICATION: meta.classification.value,
        FIELD_SOURCE: meta.source,
        FIELD_CREATED_AT: meta.created_at.isoformat() if meta.created_at else None,
        FIELD_INGESTED_AT: datetime.now(timezone.utc).isoformat(),
        FIELD_EXTRA: meta.extra or {},
    }
    return data


def ingest_text(content: str, meta: DocumentMeta) -> IngestResult:
    """Ingest raw text under the given metadata.

    Deduplicates by content hash: if a document with the same hash already has
    chunks in the index, nothing is written and the existing document is
    returned.
    """
    settings = get_settings()
    client = get_client()
    doc_id = compute_hash(content)

    existing = _count_chunks(client, doc_id)
    if existing:
        return IngestResult(
            document_id=doc_id,
            filename=meta.filename,
            num_chunks=existing,
            deduplicated=True,
        )

    chunks = chunk_text(content)
    if not chunks:
        raise ValueError("no content to ingest (empty after chunking)")

    vectors = embed_passages([c.text for c in chunks])
    base = _base_metadata(meta, doc_id, size_bytes=len(content.encode("utf-8")))

    actions = [
        {
            "_index": settings.opensearch_index,
            "_id": f"{doc_id}-{c.index}",
            "_source": {
                **base,
                FIELD_CHUNK_INDEX: c.index,
                FIELD_TEXT: c.text,
                FIELD_EMBEDDING: vector,
            },
        }
        for c, vector in zip(chunks, vectors)
    ]
    # refresh=True so the freshly ingested document is immediately searchable
    # (convenient for a PoC; drop for high-throughput bulk loads).
    bulk(client, actions, refresh=True)

    return IngestResult(
        document_id=doc_id,
        filename=meta.filename,
        num_chunks=len(chunks),
        deduplicated=False,
    )


def ingest_file(path: str | Path, meta: DocumentMeta | None = None) -> IngestResult:
    """Read a file from disk and ingest it. If ``meta`` is omitted, sensible
    defaults are derived from the filename."""
    path = Path(path)
    text, mime_type = read_document_file(path)
    if meta is None:
        meta = DocumentMeta(filename=path.name, mime_type=mime_type)
    else:
        meta.mime_type = mime_type
    return ingest_text(text, meta)


def delete_document(doc_id: str) -> bool:
    """Delete all chunks of a document. Returns ``True`` if anything was
    removed, ``False`` if no document with that id exists."""
    settings = get_settings()
    client = get_client()
    if not _count_chunks(client, doc_id):
        return False
    client.delete_by_query(
        index=settings.opensearch_index,
        body={"query": {"term": {FIELD_DOC_ID: doc_id}}},
        refresh=True,
    )
    return True
