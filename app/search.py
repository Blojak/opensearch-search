"""Search over the indexed chunks in three modes:

* ``lexical``  – BM25 full-text ``match`` on the analyzed chunk text.
* ``semantic`` – approximate kNN over the chunk embeddings.
* ``hybrid``   – both of the above combined by the normalization search
  pipeline (min-max normalize each score list, then weighted arithmetic mean).

Every mode supports the same metadata filters and asks OpenSearch for native
highlighting: the ``highlights`` field holds ``<em>``-wrapped fragments of the
matching text. Pure semantic hits have no query terms, so their highlight list
is usually empty (the full chunk text is always returned as well).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from app.config import get_settings
from app.embedding import embed_query
from app.enums import Classification, DocType, Language
from app.opensearch_store import (
    FIELD_CHUNK_INDEX,
    FIELD_CLASSIFICATION,
    FIELD_CREATED_AT,
    FIELD_DOC_ID,
    FIELD_DOC_TYPE,
    FIELD_EMBEDDING,
    FIELD_LANGUAGE,
    FIELD_TEXT,
    get_client,
)


class SearchMode(str, enum.Enum):
    """Which retrieval strategy to use."""

    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass
class SearchFilters:
    """Optional metadata filters applied to the search."""

    doc_type: DocType | None = None
    language: Language | None = None
    classification: Classification | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


@dataclass
class SearchHit:
    """A single chunk hit enriched with its document metadata."""

    score: float
    doc_id: str
    chunk_index: int
    chunk_text: str
    highlights: list[str]  # <em>-wrapped fragments (native OpenSearch highlight)
    document: dict


# Metadata fields returned to the caller (everything except the heavy vector
# and the per-chunk text, which are surfaced separately).
_META_FIELDS = (
    "filename",
    "title",
    "mime_type",
    "size_bytes",
    "language",
    "doc_type",
    "classification",
    "source",
    "created_at",
    "ingested_at",
    "extra",
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
    if filters.doc_type is not None:
        clauses.append({"term": {FIELD_DOC_TYPE: filters.doc_type.value}})
    if filters.language is not None:
        clauses.append({"term": {FIELD_LANGUAGE: filters.language.value}})
    if filters.classification is not None:
        clauses.append({"term": {FIELD_CLASSIFICATION: filters.classification.value}})
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
    doc = {"id": source.get(FIELD_DOC_ID)}
    for field in _META_FIELDS:
        doc[field] = source.get(field)
    return doc


def _hit_from_response(raw: dict) -> SearchHit:
    """Build a ``SearchHit`` from one raw OpenSearch hit."""
    source = raw["_source"]
    highlight = raw.get("highlight", {})
    return SearchHit(
        score=raw["_score"],
        doc_id=source.get(FIELD_DOC_ID),
        chunk_index=source.get(FIELD_CHUNK_INDEX),
        chunk_text=source.get(FIELD_TEXT),
        highlights=highlight.get(FIELD_TEXT, []),
        document=_document_from_source(source),
    )


def search(
    query: str,
    mode: SearchMode = SearchMode.HYBRID,
    filters: SearchFilters | None = None,
    limit: int = 10,
) -> list[SearchHit]:
    """Run a search in the requested mode and return enriched chunk hits."""
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
    return [_hit_from_response(h) for h in response["hits"]["hits"]]


def get_document(doc_id: str) -> dict | None:
    """Load a document's metadata plus its ordered chunk texts, or ``None``."""
    settings = get_settings()
    client = get_client()
    response = client.search(
        index=settings.opensearch_index,
        body={
            "size": 10_000,
            "query": {"term": {FIELD_DOC_ID: doc_id}},
            "sort": [{FIELD_CHUNK_INDEX: "asc"}],
            "_source": {"excludes": [FIELD_EMBEDDING]},
        },
    )
    hits = response["hits"]["hits"]
    if not hits:
        return None

    doc = _document_from_source(hits[0]["_source"])
    doc["num_chunks"] = len(hits)
    doc["chunks"] = [
        {
            "chunk_index": h["_source"].get(FIELD_CHUNK_INDEX),
            "text": h["_source"].get(FIELD_TEXT),
        }
        for h in hits
    ]
    return doc
