"""Search filters.

Deliberately its own module: the filters are a plain value object, but they are
needed both by the search itself and by the query logging. Keeping them in
``app.search`` would force every consumer to import the embedding stack
(sentence-transformers, torch) just to name a filter — which made importing
``app.query_log`` cost seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SearchFilters:
    """Optional metadata filters applied to a search (mirrored from Postgres)."""

    aktenzeichen: str | None = None
    verfahren_id: str | None = None
    klassifizierung: str | None = None
    language: str | None = None
    # Document type = MIME type. A list, because one user-facing type can map to
    # several MIME types (e.g. "Word" = the old and the OOXML format); matched
    # with an OR (``terms``).
    mime_type: list[str] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
