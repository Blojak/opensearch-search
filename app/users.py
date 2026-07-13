"""Just-in-time projection of IdP identities into the local ``users`` table.

The IdP owns the identity; this table is only a local projection so foreign keys
resolve and the claims we need (email for notifications, orgeinheit) are
available without calling the IdP on every query.

There is no provisioning job: the row is upserted on the first authenticated
request and its claims are refreshed on every subsequent one, so a changed email
or a moved organizational unit propagates by itself.
"""

from __future__ import annotations

import uuid

from app.auth import Principal
from app.db import session_scope
from app.models import User


def upsert_user(principal: Principal) -> uuid.UUID:
    """Return the internal user id for ``principal``, creating the row if new.

    Looked up by ``(issuer, subject)`` — the stable identity from the token —
    never by email, which is mutable and can be reassigned. The returned UUID is
    what ``documents.created_by`` and friends reference, so it stays stable even
    if the IdP or the user's email changes.
    """
    with session_scope() as session:
        user = (
            session.query(User)
            .filter(
                User.issuer == principal.issuer,
                User.subject == principal.subject,
            )
            .one_or_none()
        )
        if user is None:
            user = User(
                issuer=principal.issuer,
                subject=principal.subject,
                email=principal.email,
                orgeinheit=principal.orgeinheit,
            )
            session.add(user)
            session.flush()
        else:
            # Refresh the projected claims; they are the IdP's to change.
            user.email = principal.email
            user.orgeinheit = principal.orgeinheit
        return user.id
