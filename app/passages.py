"""Passage extraction from the stored document body.

Given a document id and a chunk's character offsets, load the full original
text and return the exact passage by slicing. This is what lets a semantic hit
(which has no lexical highlight) be traced back to its precise span in the
source document.
"""

from __future__ import annotations

from app.opensearch_store import FIELD_BODY, documents_index, get_client


def extract_passage(doc_id: str, start_char: int, end_char: int) -> str:
    """Return ``body[start_char:end_char]`` of the document ``doc_id``.

    Loads the full original text stored at ingestion time and slices it. The
    offsets follow Python slicing semantics (0-indexed, ``end_char`` exclusive),
    so the result equals the chunk text the offsets came from.
    """
    client = get_client()
    doc = client.get(index=documents_index(), id=doc_id)
    body = doc["_source"][FIELD_BODY]
    return body[start_char:end_char]
