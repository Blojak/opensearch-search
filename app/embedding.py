"""Local embedding model (sentence-transformers).

Wraps ``intfloat/multilingual-e5-large``. The e5 family requires input
prefixes: ``passage:`` for documents to be indexed and ``query:`` for search
queries. Embeddings are L2-normalized so cosine similarity in OpenSearch is
meaningful. The model is loaded lazily and reused (singleton).
"""

from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings

_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load the embedding model once and cache it."""
    settings = get_settings()
    return SentenceTransformer(settings.embedding_model)


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
