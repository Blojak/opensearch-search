"""Central configuration via pydantic-settings.

All runtime parameters are read from the environment or the ``.env`` file.
Nothing is hardcoded in the source; changes are made solely through the
environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenSearch (single store: full text, vectors and metadata) ---
    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_use_ssl: bool = False
    opensearch_verify_certs: bool = False
    opensearch_user: str | None = None
    opensearch_password: str | None = None
    opensearch_index: str = "chunks"
    # Name of the search pipeline that normalizes+combines lexical and vector
    # scores for hybrid search (created on startup).
    opensearch_hybrid_pipeline: str = "hybrid-search-pipeline"

    # --- Embedding model ---
    embedding_model: str = "intfloat/multilingual-e5-large"
    vector_size: int = 1024

    # --- Chunking ---
    chunk_size: int = 512
    chunk_overlap: int = 64

    # --- Search ---
    # Relative weights of the lexical (BM25) and semantic (kNN) sub-queries in
    # hybrid search; combined by the normalization pipeline (should sum to 1.0).
    hybrid_lexical_weight: float = 0.5
    hybrid_semantic_weight: float = 0.5

    # --- Flask API ---
    api_host: str = "0.0.0.0"
    api_port: int = 5002


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton)."""
    return Settings()
