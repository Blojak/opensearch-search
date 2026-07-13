"""OpenSearch integration: client, index setup, field names and the hybrid
search pipeline.

OpenSearch is the *derived* search index: PostgreSQL is the source of truth for
document metadata and the full body text, while every chunk becomes one
OpenSearch document carrying the analyzed text (for BM25 lexical search), the
embedding (for kNN semantic search) and a denormalized copy of the metadata
mirrored from Postgres (for filtering and rendering results). The index can be
rebuilt from Postgres at any time.

The setup (index + hybrid pipeline) is idempotent and runs on startup.
"""

from __future__ import annotations

from functools import lru_cache

from opensearchpy import OpenSearch

from app.config import get_settings

# --- Document field names (one OpenSearch document == one chunk) ---
# Identity: which Postgres document + version this chunk belongs to.
FIELD_DOCUMENT_ID = "document_id"  # Postgres documents.id (UUID)
FIELD_VERSION_NUMBER = "version_number"  # document_versions.version_number
FIELD_CHUNK_INDEX = "chunk_index"
FIELD_TEXT = "text"  # analyzed -> BM25 lexical search + highlighting
FIELD_EMBEDDING = "embedding"  # knn_vector -> semantic search
# Character offsets of the chunk into the version body text, so a hit can be
# traced back to its exact passage (body_text[start_char:end_char] == text).
FIELD_START_CHAR = "start_char"
FIELD_END_CHAR = "end_char"
# Denormalized document metadata, mirrored from Postgres (the source of truth);
# identical on every chunk of a document version.
FIELD_AKTENZEICHEN = "aktenzeichen"
FIELD_VERFAHREN_ID = "verfahren_id"
FIELD_KLASSIFIZIERUNG = "klassifizierung"
FIELD_LANGUAGE = "language"  # auto-detected at ingest (ISO-639-1 subset)
FIELD_MIME_TYPE = "mime_type"
FIELD_CREATED_AT = "created_at"  # documents.created_at


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
                FIELD_DOCUMENT_ID: {"type": "keyword"},
                FIELD_VERSION_NUMBER: {"type": "integer"},
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
                FIELD_AKTENZEICHEN: {"type": "keyword"},
                FIELD_VERFAHREN_ID: {"type": "keyword"},
                FIELD_KLASSIFIZIERUNG: {"type": "keyword"},
                FIELD_LANGUAGE: {"type": "keyword"},
                FIELD_MIME_TYPE: {"type": "keyword"},
                FIELD_CREATED_AT: {"type": "date"},
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
    """Create the chunks index with the current mapping if it is missing."""
    settings = get_settings()
    client = client or get_client()
    if not client.indices.exists(index=settings.opensearch_index):
        client.indices.create(
            index=settings.opensearch_index, body=_index_body()
        )


def delete_document_chunks(
    client: OpenSearch,
    document_id,
    refresh: bool = True,
) -> None:
    """Remove every chunk of a document from the index, whatever its version.

    Used before re-indexing a document and when deleting it. Only the current
    version is ever indexed, so this clears the way for the new one.
    """
    settings = get_settings()
    client.delete_by_query(
        index=settings.opensearch_index,
        body={"query": {"term": {FIELD_DOCUMENT_ID: str(document_id)}}},
        refresh=refresh,
    )


def recreate_index(client: OpenSearch | None = None) -> None:
    """Drop and recreate the chunks index (used when rebuilding from Postgres)."""
    settings = get_settings()
    client = client or get_client()
    client.indices.delete(index=settings.opensearch_index, ignore=[404])
    client.indices.create(index=settings.opensearch_index, body=_index_body())


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
    """Ensure the chunks index and the hybrid search pipeline exist."""
    client = client or get_client()
    ensure_index(client)
    ensure_hybrid_pipeline(client)
