"""ORM models for the document metadata store (source of truth in Postgres).

PostgreSQL holds the authoritative metadata; the OpenSearch index is a
derived, rebuildable search index on top of it. ``users`` and ``verfahren``
are owned by another bounded context and are represented here only as minimal
placeholder tables so the metadata models can reference them via real foreign
keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Placeholder for the ``users`` table owned by another bounded context.

    Only the columns needed so the metadata models can reference a user via a
    real foreign key are defined here; the full user schema lives elsewhere.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    orgeinheit: Mapped[str | None] = mapped_column(
        String(32), nullable=True, doc="Organizational unit (short code)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User id={self.id}>"


class Verfahren(Base):
    """Placeholder for the ``verfahren`` table owned by another bounded context."""

    __tablename__ = "verfahren"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Verfahren id={self.id}>"
