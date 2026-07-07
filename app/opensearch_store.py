"""OpenSearch integration: client, index setup, field names and the hybrid
search pipeline.

Unlike the Qdrant sibling project, OpenSearch is the *single* store: every
chunk becomes one OpenSearch document that carries the analyzed text (for BM25
lexical search), the embedding (for kNN semantic search) and the denormalized
document metadata (for filtering and for rendering results). There is no
separate metadata database.

The setup (index + hybrid pipeline) is idempotent and runs on startup.
"""

from __future__ import annotations

from functools import lru_cache

from opensearchpy import OpenSearch

from app.config import get_settings

# --- Document field names (one OpenSearch document == one chunk) ---
FIELD_DOC_ID = "doc_id"  # sha256 content hash; groups the chunks of a document
FIELD_CHUNK_INDEX = "chunk_index"
FIELD_TEXT = "text"  # analyzed -> BM25 lexical search + highlighting
FIELD_EMBEDDING = "embedding"  # knn_vector -> semantic search
# Character offsets of the chunk into the original document text, so a hit can
# be traced back to its exact passage (original[start_char:end_char] == text).
FIELD_START_CHAR = "start_char"
FIELD_END_CHAR = "end_char"
# Denormalized document metadata (identical on every chunk of a document).
FIELD_FILENAME = "filename"
FIELD_TITLE = "title"
FIELD_MIME_TYPE = "mime_type"
FIELD_SIZE_BYTES = "size_bytes"
FIELD_LANGUAGE = "language"
FIELD_DOC_TYPE = "doc_type"
FIELD_CLASSIFICATION = "classification"
FIELD_CREATED_AT = "created_at"
FIELD_INGESTED_AT = "ingested_at"
FIELD_SOURCE = "source"
FIELD_EXTRA = "extra"

# Field of the sibling "documents" index that stores the full original text
# once per document (the source for passage extraction).
FIELD_BODY = "body"


def documents_index() -> str:
    """Name of the sibling index holding one full-text body per document.

    Derived from the chunks index so no extra configuration is needed
    (e.g. ``chunks`` -> ``chunks_documents``).
    """
    return f"{get_settings().opensearch_index}_documents"


@lru_cache(maxsize=1)
def get_client() -> OpenSearch:
    """Create (and cache) an OpenSearch client from the configuration."""
    settings = get_settings()
    http_auth = None
    if settings.opensearch_user and settings.opensearch_password:
        http_auth = (settings.opensearch_user, settings.opensearch_password)
    return OpenSearch(
        hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
        http_auth=http_auth,
        use_ssl=settings.opensearch_use_ssl,
        verify_certs=settings.opensearch_verify_certs,
        ssl_show_warn=False,
        timeout=60,
    )


def _index_body() -> dict:
    """Index settings + mappings: kNN enabled, cosine HNSW vectors, keyword
    metadata for exact filtering."""
    settings = get_settings()
    return {
        "settings": {
            "index": {
                "knn": True,  # enable approximate kNN on this index
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }
        },
        "mappings": {
            "properties": {
                FIELD_DOC_ID: {"type": "keyword"},
                FIELD_CHUNK_INDEX: {"type": "integer"},
                FIELD_START_CHAR: {"type": "integer"},
                FIELD_END_CHAR: {"type": "integer"},
                FIELD_TEXT: {"type": "text"},
                FIELD_EMBEDDING: {
                    "type": "knn_vector",
                    "dimension": settings.vector_size,
                    "method": {
                        "name": "hnsw",
                        "engine": "lucene",
                        "space_type": "cosinesimil",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
                FIELD_FILENAME: {"type": "keyword"},
                FIELD_TITLE: {"type": "text"},
                FIELD_MIME_TYPE: {"type": "keyword"},
                FIELD_SIZE_BYTES: {"type": "long"},
                FIELD_LANGUAGE: {"type": "keyword"},
                FIELD_DOC_TYPE: {"type": "keyword"},
                FIELD_CLASSIFICATION: {"type": "keyword"},
                FIELD_CREATED_AT: {"type": "date"},
                FIELD_INGESTED_AT: {"type": "date"},
                FIELD_SOURCE: {"type": "keyword"},
                FIELD_EXTRA: {"type": "object", "enabled": True},
            }
        },
    }


def _hybrid_pipeline_body() -> dict:
    """Search pipeline that min-max normalizes the lexical and semantic scores
    and combines them as a weighted arithmetic mean.

    The weights are ordered to match the sub-queries submitted by the hybrid
    search: ``[lexical, semantic]``.
    """
    settings = get_settings()
    return {
        "description": "Normalize + weight lexical (BM25) and semantic (kNN) scores",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        "parameters": {
                            "weights": [
                                settings.hybrid_lexical_weight,
                                settings.hybrid_semantic_weight,
                            ]
                        },
                    },
                }
            }
        ],
    }


def ensure_index(client: OpenSearch | None = None) -> None:
    """Create the chunks index if missing, and ensure the char-offset fields
    exist on an already-created index (idempotent).

    The additive ``_mapping`` update lets an index created before the offset
    fields existed pick them up; it does not backfill existing documents.
    """
    settings = get_settings()
    client = client or get_client()
    if not client.indices.exists(index=settings.opensearch_index):
        client.indices.create(
            index=settings.opensearch_index, body=_index_body()
        )
    client.indices.put_mapping(
        index=settings.opensearch_index,
        body={
            "properties": {
                FIELD_START_CHAR: {"type": "integer"},
                FIELD_END_CHAR: {"type": "integer"},
            }
        },
    )


def ensure_documents_index(client: OpenSearch | None = None) -> None:
    """Create the sibling documents index (one stored body per document).

    The ``body`` is stored but not indexed: it is only sliced in Python for
    passage extraction, never searched.
    """
    client = client or get_client()
    name = documents_index()
    if not client.indices.exists(index=name):
        client.indices.create(
            index=name,
            body={
                "settings": {
                    "index": {"number_of_shards": 1, "number_of_replicas": 0}
                },
                "mappings": {
                    "properties": {FIELD_BODY: {"type": "text", "index": False}}
                },
            },
        )


def ensure_hybrid_pipeline(client: OpenSearch | None = None) -> None:
    """Create/update the hybrid search pipeline (idempotent, cheap to re-put)."""
    settings = get_settings()
    client = client or get_client()
    client.transport.perform_request(
        "PUT",
        f"/_search/pipeline/{settings.opensearch_hybrid_pipeline}",
        body=_hybrid_pipeline_body(),
    )


def ensure_setup(client: OpenSearch | None = None) -> None:
    """Ensure the chunks index, the documents index and the hybrid pipeline."""
    client = client or get_client()
    ensure_index(client)
    ensure_documents_index(client)
    ensure_hybrid_pipeline(client)
