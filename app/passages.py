"""Passage extraction from the stored document body.

Given a document version and a chunk's character offsets, load the full body
text from PostgreSQL (the source of truth) and return the exact passage by
slicing. This is what lets a semantic hit (which has no lexical highlight) be
traced back to its precise span in the source document.
"""

from __future__ import annotations

import uuid

from app.db import session_scope
from app.models import DocumentVersion


def extract_passage(
    document_id: uuid.UUID,
    version_number: int,
    start_char: int,
    end_char: int,
) -> str | None:
    """Return ``body_text[start_char:end_char]`` of a document version.

    Loads the body text of ``(document_id, version_number)`` from Postgres and
    slices it. The offsets follow Python slicing semantics (0-indexed,
    ``end_char`` exclusive), so the result equals the chunk text the offsets
    came from. Returns ``None`` if the version does not exist.
    """
    with session_scope() as session:
        version = (
            session.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_number == version_number,
            )
            .one_or_none()
        )
        if version is None:
            return None
        return version.body_text[start_char:end_char]
