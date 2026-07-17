"""Search over the indexed chunks in three modes:

* ``lexical``  – BM25 full-text ``match`` on the analyzed chunk text.
* ``semantic`` – approximate kNN over the chunk embeddings.
* ``hybrid``   – both of the above combined by the normalization search
  pipeline (min-max normalize each score list, then weighted arithmetic mean).

Every mode supports the same metadata filters (mirrored from Postgres) and asks
OpenSearch for native highlighting: the ``highlights`` field holds
``<em>``-wrapped fragments of the matching text. Pure semantic hits have no
query terms, so their highlight list is usually empty (the full chunk text is
always returned as well). ``get_document`` reads document metadata straight from
Postgres, the source of truth.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import tuple_

from app.config import get_settings
from app.db import session_scope
from app.embedding import embed_query
from app.filters import SearchFilters
from app.models import Document, DocumentVersion
from app.passages import passage_window
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
    FIELD_S3_OBJECT_KEY,
    FIELD_START_CHAR,
    FIELD_TEXT,
    FIELD_VERFAHREN_ID,
    FIELD_VERSION_NUMBER,
    get_client,
)


class SearchMode(str, enum.Enum):
    """Which retrieval strategy to use."""

    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass
class SearchHit:
    """A single chunk hit enriched with its document metadata."""

    score: float
    document_id: str
    version_number: int
    chunk_index: int
    chunk_text: str
    start_char: int | None  # offset into the version body text (for extracting
    end_char: int | None  # / highlighting the exact passage)
    highlights: list[str]  # <em>-wrapped fragments (native OpenSearch highlight)
    document: dict
    # The chunk shown inside the surrounding body text, so a semantic hit (which
    # has no term highlights) can still be read in context. ``None`` unless the
    # caller asked for it. ``{"text", "hit_start", "hit_end"}`` — the last two
    # locate the chunk inside ``text``.
    context: dict | None = None


# Document-level metadata mirrored on every chunk (returned to the caller).
_META_FIELDS = (
    FIELD_AKTENZEICHEN,
    FIELD_VERFAHREN_ID,
    FIELD_KLASSIFIZIERUNG,
    FIELD_LANGUAGE,
    FIELD_MIME_TYPE,
    FIELD_CREATED_AT,
    FIELD_VERSION_NUMBER,
    FIELD_S3_OBJECT_KEY,
)

_HIGHLIGHT = {
    "fields": {FIELD_TEXT: {"fragment_size": 150, "number_of_fragments": 3}},
    "pre_tags": ["<em>"],
    "post_tags": ["</em>"],
}


def _filter_clauses(filters: SearchFilters | None) -> list[dict]:
    """Translate ``SearchFilters`` into OpenSearch bool ``filter`` clauses."""
    if filters is None:
        return []

    clauses: list[dict] = []
    if filters.aktenzeichen is not None:
        clauses.append({"term": {FIELD_AKTENZEICHEN: filters.aktenzeichen}})
    if filters.verfahren_id is not None:
        clauses.append({"term": {FIELD_VERFAHREN_ID: filters.verfahren_id}})
    if filters.klassifizierung is not None:
        clauses.append({"term": {FIELD_KLASSIFIZIERUNG: filters.klassifizierung}})
    if filters.language is not None:
        clauses.append({"term": {FIELD_LANGUAGE: filters.language}})
    if filters.mime_type:
        clauses.append({"terms": {FIELD_MIME_TYPE: filters.mime_type}})
    if filters.created_from is not None or filters.created_to is not None:
        rng: dict = {}
        if filters.created_from is not None:
            rng["gte"] = filters.created_from.isoformat()
        if filters.created_to is not None:
            rng["lte"] = filters.created_to.isoformat()
        clauses.append({"range": {FIELD_CREATED_AT: rng}})
    return clauses


def _lexical_query(query: str, clauses: list[dict]) -> dict:
    """BM25 match on the chunk text, AND-ed with the metadata filters."""
    return {"bool": {"must": [{"match": {FIELD_TEXT: query}}], "filter": clauses}}


def _semantic_query(vector: list[float], k: int, clauses: list[dict]) -> dict:
    """Approximate kNN over the embeddings, filtered by the metadata clauses."""
    knn: dict = {"vector": vector, "k": k}
    if clauses:
        knn["filter"] = {"bool": {"filter": clauses}}
    return {"knn": {FIELD_EMBEDDING: knn}}


def _document_from_source(source: dict) -> dict:
    """Extract the document-level metadata from a chunk's ``_source``."""
    doc = {"id": source.get(FIELD_DOCUMENT_ID)}
    for field in _META_FIELDS:
        doc[field] = source.get(field)
    return doc


def _hit_from_response(raw: dict) -> SearchHit:
    """Build a ``SearchHit`` from one raw OpenSearch hit."""
    source = raw["_source"]
    highlight = raw.get("highlight", {})
    return SearchHit(
        score=raw["_score"],
        document_id=source.get(FIELD_DOCUMENT_ID),
        version_number=source.get(FIELD_VERSION_NUMBER),
        chunk_index=source.get(FIELD_CHUNK_INDEX),
        chunk_text=source.get(FIELD_TEXT),
        start_char=source.get(FIELD_START_CHAR),
        end_char=source.get(FIELD_END_CHAR),
        highlights=highlight.get(FIELD_TEXT, []),
        document=_document_from_source(source),
    )


def _attach_context(hits: list[SearchHit], context_chars: int) -> None:
    """Attach a body-context window to each hit, in place.

    Reads the body once per distinct ``(document_id, version_number)`` — not per
    hit — and slices the window locally with ``passage_window``. That batched
    read is what keeps this cheap enough to do on every search: several hits from
    the same document share a single Postgres row.
    """
    pairs = {
        (uuid.UUID(h.document_id), h.version_number)
        for h in hits
        if h.start_char is not None and h.end_char is not None
    }
    if not pairs:
        return

    with session_scope() as session:
        rows = (
            session.query(
                DocumentVersion.document_id,
                DocumentVersion.version_number,
                DocumentVersion.body_text,
            )
            .filter(
                tuple_(
                    DocumentVersion.document_id, DocumentVersion.version_number
                ).in_(list(pairs))
            )
            .all()
        )
    bodies = {(str(doc_id), vnum): body for doc_id, vnum, body in rows}

    for hit in hits:
        if hit.start_char is None or hit.end_char is None:
            continue
        body = bodies.get((hit.document_id, hit.version_number))
        if body is None:
            continue
        passage = passage_window(body, hit.start_char, hit.end_char, context_chars)
        hit.context = {
            "text": passage.text,
            "hit_start": passage.hit_start,
            "hit_end": passage.hit_end,
        }


def search(
    query: str,
    mode: SearchMode = SearchMode.HYBRID,
    filters: SearchFilters | None = None,
    limit: int = 10,
    context_chars: int = 0,
) -> list[SearchHit]:
    """Run a search in the requested mode and return enriched chunk hits.

    When ``context_chars > 0`` each hit also carries a ``context`` window: the
    chunk shown inside that many characters of surrounding body text, so a
    semantic hit can be read in context even though it has no term highlights.
    """
    settings = get_settings()
    client = get_client()
    clauses = _filter_clauses(filters)

    body: dict = {
        "size": limit,
        "highlight": _HIGHLIGHT,
        # Exclude the bulky vector from the returned _source.
        "_source": {"excludes": [FIELD_EMBEDDING]},
    }
    params: dict = {}

    if mode is SearchMode.LEXICAL:
        body["query"] = _lexical_query(query, clauses)
    elif mode is SearchMode.SEMANTIC:
        vector = embed_query(query)
        body["query"] = _semantic_query(vector, limit, clauses)
    else:  # HYBRID
        vector = embed_query(query)
        body["query"] = {
            "hybrid": {
                "queries": [
                    _lexical_query(query, clauses),
                    _semantic_query(vector, limit, clauses),
                ]
            }
        }
        params["search_pipeline"] = settings.opensearch_hybrid_pipeline

    response = client.search(
        index=settings.opensearch_index, body=body, params=params
    )
    hits = [_hit_from_response(h) for h in response["hits"]["hits"]]
    if context_chars > 0:
        _attach_context(hits, context_chars)
    return hits


def _document_chunks(client, document_id: uuid.UUID, version_number: int) -> list[dict]:
    """Ordered chunk texts of a document version, read from OpenSearch."""
    settings = get_settings()
    response = client.search(
        index=settings.opensearch_index,
        body={
            "size": 10_000,
            "query": {
                "bool": {
                    "must": [
                        {"term": {FIELD_DOCUMENT_ID: str(document_id)}},
                        {"term": {FIELD_VERSION_NUMBER: version_number}},
                    ]
                }
            },
            "sort": [{FIELD_CHUNK_INDEX: "asc"}],
            "_source": {"excludes": [FIELD_EMBEDDING]},
        },
    )
    return [
        {
            "chunk_index": h["_source"].get(FIELD_CHUNK_INDEX),
            "text": h["_source"].get(FIELD_TEXT),
        }
        for h in response["hits"]["hits"]
    ]


def get_document(document_id: uuid.UUID) -> dict | None:
    """Load a document's metadata from Postgres plus its ordered chunks, or
    ``None`` if it does not exist. Soft-deleted documents are still returned
    (with ``deleted_at`` set)."""
    client = get_client()
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            return None
        version = (
            session.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == document.id,
                DocumentVersion.version_number == document.current_version,
            )
            .one_or_none()
        )
        chunks = _document_chunks(client, document.id, document.current_version)
        return {
            "id": str(document.id),
            "aktenzeichen": document.aktenzeichen,
            "verfahren_id": str(document.verfahren_id) if document.verfahren_id else None,
            "klassifizierung": document.klassifizierung,
            "s3_object_key": document.s3_object_key,
            "mime_type": document.mime_type,
            "language": document.language,
            "created_by": str(document.created_by),
            "created_at": document.created_at.isoformat(),
            "current_version": document.current_version,
            "deleted_at": document.deleted_at.isoformat() if document.deleted_at else None,
            "content_hash": version.content_hash if version else None,
            "num_chunks": len(chunks),
            "chunks": chunks,
        }
