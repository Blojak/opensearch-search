"""Detecting that several people research the same thing.

When a search repeats one that *somebody else* ran before — same ``query_hash``
— both sides should learn about each other, so that duplicate investigative work
surfaces instead of staying invisible. Each side gets its own row in
``query_notifications`` (``notified_user_id``), linking the earlier query
(``original_query_id``) to the new one (``duplicate_query_id``).

Nothing is delivered here: rows are created with ``status = 'pending'``. Actually
notifying the user (email, UI badge) is a separate concern and a later step —
this module only records *that* a notification is due.

Deliberate rules:

* **A user never matches themselves.** Repeating your own search is not a
  duplicate; only searches by *other* users count.
* **A pair is reported once.** For the same query hash and the same pair of
  users no second notification is created, however often either of them searches
  again — otherwise every repeat would spam both sides.
* **No visibility gate (yet).** Any two users match, regardless of orgeinheit.
  Who may be told about whom is a legal/organizational question that has to be
  settled before this goes anywhere near production.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import or_
from sqlalchemy.orm import aliased

from app.db import session_scope
from app.models import QueryNotification, SearchQuery, User

logger = logging.getLogger(__name__)


def _already_notified(
    session,
    notified_user_id: uuid.UUID,
    query_hash: str,
    counterpart_user_id: uuid.UUID,
) -> bool:
    """Has this user already been told about this counterpart for this query?"""
    original = aliased(SearchQuery)
    duplicate = aliased(SearchQuery)
    existing = (
        session.query(QueryNotification.id)
        .join(original, original.id == QueryNotification.original_query_id)
        .join(duplicate, duplicate.id == QueryNotification.duplicate_query_id)
        .filter(
            QueryNotification.notified_user_id == notified_user_id,
            original.query_hash == query_hash,
            or_(
                original.user_id == counterpart_user_id,
                duplicate.user_id == counterpart_user_id,
            ),
        )
        .first()
    )
    return existing is not None


def notify_duplicates(search_query_id: uuid.UUID) -> int:
    """Record notifications for a search that duplicates other users' searches.

    Returns how many notification rows were created. Never raises: like the
    query logging it feeds on, this is a side effect of searching and must not
    turn a working search into an error.
    """
    try:
        with session_scope() as session:
            current = session.get(SearchQuery, search_query_id)
            if current is None:
                return 0

            # Earlier searches of the same query by *other* people. One
            # counterpart per user: their first search is the "original".
            earlier = (
                session.query(SearchQuery)
                .filter(
                    SearchQuery.query_hash == current.query_hash,
                    SearchQuery.user_id != current.user_id,
                    SearchQuery.id != current.id,
                )
                .order_by(SearchQuery.created_at)
                .all()
            )
            originals: dict[uuid.UUID, SearchQuery] = {}
            for query in earlier:
                originals.setdefault(query.user_id, query)

            created = 0
            for other_user_id, original in originals.items():
                # Both sides learn about each other, so each gets its own row.
                recipients = (
                    (current.user_id, other_user_id),
                    (other_user_id, current.user_id),
                )
                for notified_user_id, counterpart_id in recipients:
                    if _already_notified(
                        session, notified_user_id, current.query_hash, counterpart_id
                    ):
                        continue
                    session.add(
                        QueryNotification(
                            original_query_id=original.id,
                            duplicate_query_id=current.id,
                            notified_user_id=notified_user_id,
                        )
                    )
                    created += 1
            return created
    except Exception:  # pylint: disable=broad-except
        # Same contract as the query logging: detecting a duplicate is a side
        # effect of searching and must never break the search itself.
        logger.exception("failed to record duplicate notifications for %s", search_query_id)
        return 0


def list_notifications(user_id: uuid.UUID) -> list[dict]:
    """The notifications addressed to one user, newest first.

    Each entry names the query both sides searched and who the counterpart is —
    which is the whole point: knowing *whom* to talk to.
    """
    with session_scope() as session:
        notifications = (
            session.query(QueryNotification)
            .filter(QueryNotification.notified_user_id == user_id)
            .order_by(QueryNotification.created_at.desc())
            .all()
        )

        entries = []
        for notification in notifications:
            original = session.get(SearchQuery, notification.original_query_id)
            duplicate = session.get(SearchQuery, notification.duplicate_query_id)
            # The counterpart is whichever of the two queries is not this user's.
            other = duplicate if original.user_id == user_id else original
            counterpart = session.get(User, other.user_id)

            entries.append(
                {
                    "id": str(notification.id),
                    "status": notification.status,
                    "created_at": notification.created_at.isoformat(),
                    "query_text": other.query_text,
                    "counterpart": {
                        "user_id": str(other.user_id),
                        "email": counterpart.email if counterpart else None,
                        "orgeinheit": counterpart.orgeinheit if counterpart else None,
                    },
                    "searched_at": other.created_at.isoformat(),
                }
            )
        return entries
