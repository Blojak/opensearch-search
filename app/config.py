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

    # --- PostgreSQL (metadata - single source of truth) ---
    postgres_user: str = "osearch"
    postgres_password: str = "osearch"
    postgres_db: str = "osearch"
    postgres_host: str = "localhost"
    postgres_port: int = 5433

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

    # --- HuggingFace Hub access (corporate environment) ---
    # Endpoint/mirror for the Hub (e.g. an internal proxy); exported as
    # HF_ENDPOINT before the model loads. Empty means the default public hub.
    hf_endpoint: str | None = None
    # Access token, if the internal mirror requires authentication.
    hf_token: str | None = None
    # Cache directory for downloaded models (exported as HF_HOME).
    hf_home: str | None = None
    # Serve the model only from the local cache, never hit the network.
    hf_offline: bool = False
    # Path to a CA bundle (PEM) for TLS verification behind a corporate proxy.
    # If unset, the standard REQUESTS_CA_BUNDLE/SSL_CERT_FILE env vars and the
    # system trust store are auto-detected (see app/embedding.py).
    ca_bundle: str | None = None

    # --- Chunking ---
    chunk_size: int = 512
    chunk_overlap: int = 64

    # --- Search ---
    # Relative weights of the lexical (BM25) and semantic (kNN) sub-queries in
    # hybrid search; combined by the normalization pipeline (should sum to 1.0).
    hybrid_lexical_weight: float = 0.5
    hybrid_semantic_weight: float = 0.5

    # --- OIDC (the API is a resource server: it validates bearer tokens) ---
    # Deliberately WITHOUT defaults: these are environment-specific and security
    # critical, so a missing OIDC_ISSUER / OIDC_AUDIENCE must fail loudly at
    # startup (e.g. a misspelled key in a Kubernetes ConfigMap) instead of
    # silently falling back to some local value. Set them via the environment;
    # the local development values live in .env.
    oidc_issuer: str
    oidc_audience: str
    # How long the JWKS is cached before it is refetched (seconds). A token
    # signed with an unknown key id forces an immediate refetch regardless.
    oidc_jwks_ttl: int = 300

    # --- Flask API ---
    api_host: str = "0.0.0.0"
    api_port: int = 5002

    @property
    def database_url(self) -> str:
        """SQLAlchemy connection URL for PostgreSQL (psycopg2 driver)."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton)."""
    return Settings()
