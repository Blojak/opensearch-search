"""Local embedding model (sentence-transformers).

Wraps ``intfloat/multilingual-e5-large``. The e5 family requires input
prefixes: ``passage:`` for documents to be indexed and ``query:`` for search
queries. Embeddings are L2-normalized so cosine similarity in OpenSearch is
meaningful. The model is loaded lazily and reused (singleton).

In a corporate environment the HuggingFace Hub is typically reached through an
internal mirror/proxy that performs TLS interception, so downloads must trust
the organization's CA certificates. ``_configure_hf_environment`` wires the
endpoint, cache and CA bundle from the settings/environment. It runs at import
time, *before* ``sentence_transformers`` (and thus ``huggingface_hub``) is
imported, because ``huggingface_hub`` reads ``HF_ENDPOINT`` at import time.
"""

from __future__ import annotations

import os
from functools import lru_cache

from app.config import Settings, get_settings
from app.tls import configure_ca_env

_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


def _configure_hf_environment(settings: Settings) -> None:
    """Export the HuggingFace/TLS environment before the Hub client is imported.

    Explicit settings override; unset ones leave any existing environment
    values untouched, so the deployment environment stays authoritative.
    """
    if settings.hf_endpoint:
        os.environ["HF_ENDPOINT"] = settings.hf_endpoint
    if settings.hf_home:
        os.environ["HF_HOME"] = settings.hf_home
    if settings.hf_offline:
        os.environ["HF_HUB_OFFLINE"] = "1"

    # Trust the corporate CA for the Hub download (see app/tls.py).
    configure_ca_env(settings.ca_bundle)


# Configure the environment before importing sentence_transformers below.
_configure_hf_environment(get_settings())

from sentence_transformers import SentenceTransformer  # noqa: E402


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load the embedding model once and cache it."""
    settings = get_settings()
    return SentenceTransformer(
        settings.embedding_model,
        token=settings.hf_token or None,
    )


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embed document chunks (adds the ``passage:`` prefix)."""
    if not texts:
        return []
    model = get_model()
    prefixed = [_PASSAGE_PREFIX + t for t in texts]
    vectors = model.encode(
        prefixed,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def embed_query(text: str) -> list[float]:
    """Embed a search query (adds the ``query:`` prefix)."""
    model = get_model()
    vector = model.encode(
        _QUERY_PREFIX + text,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vector.tolist()
