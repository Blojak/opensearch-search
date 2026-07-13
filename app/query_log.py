"""Recording of search queries in ``search_queries``.

Every search is written down: who searched, what they typed, a fingerprint of
the normalized query, the filters they applied and how many hits came back. This
is the data basis for spotting that two people are researching the same thing
(``query_notifications``) — and, before that, for deciding *empirically* whether
an exact fingerprint is enough or semantic similarity is actually needed.

**The hash covers the query text only, not the filters.** The filters are stored
alongside in the JSONB column, so the matching rule can be tightened later
("same query *and* same verfahren") without a migration. The reverse would not
work: what was never hashed cannot be recovered.

**Normalization is deliberately conservative** (lowercase, trim, collapse
whitespace — word order preserved). Blurring more than that, e.g. by sorting
tokens, would smear the line between "identical" and "similar" and destroy the
ability to measure how often exact repeats actually occur.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from app.db import session_scope
from app.filters import SearchFilters
from app.models import SearchQuery

logger = logging.getLogger(__name__)


def normalize_query(text: str) -> str:
    """Canonical form of a query: lowercased, trimmed, whitespace collapsed."""
    return " ".join(text.lower().split())


def compute_query_hash(text: str) -> str:
    """sha256 fingerprint of the normalized query text."""
    return hashlib.sha256(normalize_query(text).encode("utf-8")).hexdigest()


def filters_to_json(filters: SearchFilters | None) -> dict | None:
    """Serialize the applied filters for the JSONB column (``None`` if empty)."""
    if filters is None:
        return None
    data = {
        "aktenzeichen": filters.aktenzeichen,
        "verfahren_id": filters.verfahren_id,
        "klassifizierung": filters.klassifizierung,
        "language": filters.language,
        "created_from": (
            filters.created_from.isoformat() if filters.created_from else None
        ),
        "created_to": filters.created_to.isoformat() if filters.created_to else None,
    }
    applied = {key: value for key, value in data.items() if value is not None}
    return applied or None


def log_search(
    user_id: uuid.UUID,
    query_text: str,
    filters: SearchFilters | None,
    result_count: int,
) -> uuid.UUID | None:
    """Record one search and return its id, or ``None`` if recording failed.

    Never raises: this is telemetry, and a failure to write it must not turn a
    working search into an error for the user. The failure is logged instead.
    """
    try:
        with session_scope() as session:
            record = SearchQuery(
                user_id=user_id,
                query_text=query_text,
                query_hash=compute_query_hash(query_text),
                filters=filters_to_json(filters),
                result_count=result_count,
            )
            session.add(record)
            session.flush()
            return record.id
    except Exception:  # pylint: disable=broad-except
        # Intentionally broad: the contract of this function is that recording a
        # search can never break the search itself. Narrowing this to
        # SQLAlchemyError would let a serialization bug, a TypeError or a driver
        # error that is not wrapped by SQLAlchemy surface as a 500 to the user.
        logger.exception("failed to record search query for user %s", user_id)
        return None
