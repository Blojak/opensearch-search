"""Ingestion pipeline.

Flow: compute the content hash (dedup) -> write the document and its first
version to PostgreSQL (the source of truth) -> split into chunks -> embed ->
bulk-index one OpenSearch document per chunk, each carrying the chunk text, its
embedding and the denormalized metadata mirrored from Postgres.

PostgreSQL owns the metadata and the full body text (``document_versions``);
OpenSearch is the derived, rebuildable search index. A chunk's OpenSearch
``_id`` is ``"{document_id}-v{version_number}-{chunk_index}"``.

The document language is auto-detected from the content unless the caller
supplies it explicitly.

Versioning of an existing document (v2, v3, ...) is out of scope here: it needs
an explicit document reference from the caller. Re-ingesting identical content
is deduplicated by ``content_hash``; any other ingest creates a new document at
version 1.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from opensearchpy.helpers import bulk

from app.chunking import chunk_text
from app.config import get_settings
from app.db import session_scope
from app.embedding import embed_passages
from app.language import detect_language
from app.models import Document, DocumentVersion, User, Verfahren
from app.opensearch_store import (
    FIELD_AKTENZEICHEN,
    FIELD_CHUNK_INDEX,
    FIELD_CREATED_AT,
    FIELD_DOCUMENT_ID,
    FIELD_EMBEDDING,
    FIELD_END_CHAR,
    FIELD_KLASSIFIZIERUNG,
    FIELD_LANGUAGE,
    FIELD_MIME_TYPE,
    FIELD_START_CHAR,
    FIELD_TEXT,
    FIELD_VERFAHREN_ID,
    FIELD_VERSION_NUMBER,
    get_client,
)


@dataclass
class DocumentMeta:
    """Caller-supplied metadata for a document to ingest.

    All identity fields are supplied by the caller (later: a UI). ``created_by``
    and ``verfahren_id`` must reference existing rows in Postgres. ``language``
    is optional: when omitted it is auto-detected from the content.
    """

    aktenzeichen: str
    klassifizierung: str
    s3_object_key: str
    created_by: uuid.UUID
    verfahren_id: uuid.UUID | None = None
    mime_type: str = "text/plain"
    language: str | None = None  # ISO-639-1 code; auto-detected when omitted


@dataclass
class IngestResult:
    """Outcome of an ingestion call."""

    document_id: uuid.UUID
    version_number: int
    aktenzeichen: str
    num_chunks: int
    deduplicated: bool  # True if identical content already existed (by hash)


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


def _count_chunks(client, document_id: uuid.UUID, version_number: int) -> int:
    """How many chunks of a document version already exist in the index."""
    settings = get_settings()
    resp = client.count(
        index=settings.opensearch_index,
        body={
            "query": {
                "bool": {
                    "must": [
                        {"term": {FIELD_DOCUMENT_ID: str(document_id)}},
                        {"term": {FIELD_VERSION_NUMBER: version_number}},
                    ]
                }
            }
        },
    )
    return int(resp["count"])


def chunk_action(document: Document, version_number: int, chunk, vector) -> dict:
    """Build the OpenSearch bulk action for a single chunk of a document version."""
    settings = get_settings()
    return {
        "_index": settings.opensearch_index,
        "_id": f"{document.id}-v{version_number}-{chunk.index}",
        "_source": {
            FIELD_DOCUMENT_ID: str(document.id),
            FIELD_VERSION_NUMBER: version_number,
            FIELD_AKTENZEICHEN: document.aktenzeichen,
            FIELD_VERFAHREN_ID: (
                str(document.verfahren_id) if document.verfahren_id else None
            ),
            FIELD_KLASSIFIZIERUNG: document.klassifizierung,
            FIELD_LANGUAGE: document.language,
            FIELD_MIME_TYPE: document.mime_type,
            FIELD_CREATED_AT: document.created_at.isoformat(),
            FIELD_CHUNK_INDEX: chunk.index,
            FIELD_START_CHAR: chunk.start_char,
            FIELD_END_CHAR: chunk.end_char,
            FIELD_TEXT: chunk.text,
            FIELD_EMBEDDING: vector,
        },
    }


def index_chunks(
    client,
    document: Document,
    version_number: int,
    chunks,
    vectors,
    refresh: bool = True,
) -> None:
    """Index one OpenSearch document per chunk of a document version.

    ``refresh=True`` makes the document immediately searchable (right for a
    single ingest); bulk rebuilds pass ``refresh=False`` and refresh once at the
    end.
    """
    actions = [
        chunk_action(document, version_number, c, vector)
        for c, vector in zip(chunks, vectors)
    ]
    bulk(client, actions, refresh=refresh)


def ingest_text(content: str, meta: DocumentMeta) -> IngestResult:
    """Ingest raw text: persist it in Postgres, then index its chunks.

    Deduplicates by content hash: if a document version with the same hash
    already exists, nothing is written and that document is returned.
    """
    client = get_client()
    content_hash = compute_hash(content)

    with session_scope() as session:
        existing = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.content_hash == content_hash)
            .first()
        )
        if existing is not None:
            return IngestResult(
                document_id=existing.document_id,
                version_number=existing.version_number,
                aktenzeichen=existing.document.aktenzeichen,
                num_chunks=_count_chunks(
                    client, existing.document_id, existing.version_number
                ),
                deduplicated=True,
            )

        if session.get(User, meta.created_by) is None:
            raise ValueError(f"created_by user {meta.created_by} does not exist")
        if meta.verfahren_id is not None and session.get(Verfahren, meta.verfahren_id) is None:
            raise ValueError(f"verfahren {meta.verfahren_id} does not exist")

        chunks = chunk_text(content)
        if not chunks:
            raise ValueError("no content to ingest (empty after chunking)")

        # Caller-supplied language wins; otherwise detect it from the content.
        language = meta.language or detect_language(content).value

        document = Document(
            aktenzeichen=meta.aktenzeichen,
            verfahren_id=meta.verfahren_id,
            klassifizierung=meta.klassifizierung,
            s3_object_key=meta.s3_object_key,
            mime_type=meta.mime_type,
            language=language,
            created_by=meta.created_by,
            current_version=1,
        )
        session.add(document)
        session.flush()  # populate server-side defaults (id, created_at)
        session.refresh(document)

        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            body_text=content,
            content_hash=content_hash,
            created_by=meta.created_by,
        )
        session.add(version)

        vectors = embed_passages([c.text for c in chunks])
        index_chunks(client, document, version_number=1, chunks=chunks, vectors=vectors)

        return IngestResult(
            document_id=document.id,
            version_number=1,
            aktenzeichen=document.aktenzeichen,
            num_chunks=len(chunks),
            deduplicated=False,
        )


def ingest_file(path: str | Path, meta: DocumentMeta) -> IngestResult:
    """Read a file from disk and ingest it, deriving the mime type from it."""
    path = Path(path)
    text, mime_type = read_document_file(path)
    meta.mime_type = mime_type
    return ingest_text(text, meta)


def delete_document(document_id: uuid.UUID) -> bool:
    """Soft-delete a document in Postgres and remove its chunks from OpenSearch.

    Returns ``True`` if a live document was deleted, ``False`` if none with that
    id exists or it was already deleted.
    """
    settings = get_settings()
    client = get_client()

    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None or document.deleted_at is not None:
            return False
        document.deleted_at = datetime.now(timezone.utc)

        client.delete_by_query(
            index=settings.opensearch_index,
            body={"query": {"term": {FIELD_DOCUMENT_ID: str(document_id)}}},
            refresh=True,
        )
        return True
