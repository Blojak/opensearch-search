"""Passage extraction: a search hit shown in its surrounding context.

A search hit already carries its ``chunk_text`` — enough for a result list. For
a **detail view** the UI needs more: the hit embedded in the text around it, so
the reader sees the finding in context. That is what this module produces.

The body text lives in PostgreSQL (``document_versions.body_text``, the source
of truth), so the window is sliced from there using the character offsets the
search returned. ``hit_start`` / ``hit_end`` locate the hit *inside* the
returned window, which is what lets the UI highlight it.

Computing this for every search result would mean one extra database read per
hit, so it is deliberately not part of the search response — the UI fetches it
on demand when a hit is opened.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.db import session_scope
from app.models import DocumentVersion

# Characters of context to include on each side of the hit by default.
DEFAULT_CONTEXT_CHARS = 200


@dataclass(frozen=True)
class Passage:
    """A hit together with the text surrounding it."""

    text: str  # the context window: text before + the hit + text after
    hit_start: int  # offset of the hit within ``text`` (not within the body)
    hit_end: int  # exclusive end offset of the hit within ``text``


def extract_passage(
    document_id: uuid.UUID,
    version_number: int,
    start_char: int,
    end_char: int,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> Passage | None:
    """Return the hit ``[start_char:end_char]`` with context around it.

    The offsets are the ones the search returned for a chunk. They are clamped
    to the body, so stale or out-of-range offsets shorten the window instead of
    raising. Returns ``None`` if the document version does not exist.
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

        body = version.body_text
        # Clamp the hit into the body, then widen it by the context on each side.
        hit_from = max(0, min(start_char, len(body)))
        hit_to = max(hit_from, min(end_char, len(body)))
        window_from = max(0, hit_from - context_chars)
        window_to = min(len(body), hit_to + context_chars)

        return Passage(
            text=body[window_from:window_to],
            hit_start=hit_from - window_from,
            hit_end=hit_to - window_from,
        )
