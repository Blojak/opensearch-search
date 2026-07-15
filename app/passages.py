"""Passage extraction: a search hit shown in its surrounding context.

A search hit already carries its ``chunk_text`` — enough for a result list. For
a **detail view** the UI needs more: the hit embedded in the text around it, so
the reader sees the finding in context. That is what this module produces.

The body text lives in PostgreSQL (``document_versions.body_text``, the source
of truth), so the window is sliced from there using the character offsets the
search returned. ``hit_start`` / ``hit_end`` locate the hit *inside* the
returned window, which is what lets the UI highlight it.

Two callers share the ``passage_window`` arithmetic: ``extract_passage`` reads a
single version for the detail view (``GET /documents/<id>/passage``), and the
search adds a smaller window (``SEARCH_CONTEXT_CHARS``) to every hit so a
semantic result — which has no term highlights — can still be read in context.
The naive worry was one extra read per hit; the search avoids it by reading each
distinct version body once and reusing it across its hits (see
``app.search._attach_context``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.db import session_scope
from app.models import DocumentVersion

# Characters of context to include on each side of the hit by default.
DEFAULT_CONTEXT_CHARS = 200

# A smaller window attached to every search hit, so a semantic hit can be read
# in context inline in the result list without opening the detail view.
SEARCH_CONTEXT_CHARS = 150


@dataclass(frozen=True)
class Passage:
    """A hit together with the text surrounding it."""

    text: str  # the context window: text before + the hit + text after
    hit_start: int  # offset of the hit within ``text`` (not within the body)
    hit_end: int  # exclusive end offset of the hit within ``text``


def passage_window(
    body: str,
    start_char: int,
    end_char: int,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> Passage:
    """Cut the hit ``[start_char:end_char]`` out of ``body`` with context around it.

    Pure text arithmetic, kept separate from the database access so it can be
    reasoned about (and tested) on its own. The offsets are clamped to the body,
    so stale or out-of-range offsets shorten the window instead of raising.
    """
    hit_from = max(0, min(start_char, len(body)))
    hit_to = max(hit_from, min(end_char, len(body)))
    window_from = max(0, hit_from - context_chars)
    window_to = min(len(body), hit_to + context_chars)

    return Passage(
        text=body[window_from:window_to],
        hit_start=hit_from - window_from,
        hit_end=hit_to - window_from,
    )


def extract_passage(
    document_id: uuid.UUID,
    version_number: int,
    start_char: int,
    end_char: int,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> Passage | None:
    """Return the hit with context, read from the version's body in Postgres.

    Returns ``None`` if the document version does not exist.
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
        return passage_window(
            version.body_text, start_char, end_char, context_chars
        )
