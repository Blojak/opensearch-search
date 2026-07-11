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

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text
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


class Document(Base):
    """A document and its current metadata (authoritative record in Postgres).

    The body text itself is versioned in ``document_versions``; this row holds
    the stable identity and administrative metadata. ``current_version`` points
    at the active version number. Soft deletion is expressed via ``deleted_at``.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    aktenzeichen: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verfahren_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("verfahren.id"), nullable=True, index=True,
    )
    klassifizierung: Mapped[str] = mapped_column(String(32), nullable=False)
    s3_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        doc="Soft-delete timestamp; NULL means the document is active",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Document id={self.id} aktenzeichen={self.aktenzeichen!r}>"
