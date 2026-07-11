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

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        order_by="DocumentVersion.version_number",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Document id={self.id} aktenzeichen={self.aktenzeichen!r}>"


class DocumentVersion(Base):
    """An append-only revision of a document's body text.

    Each row is an immutable snapshot; new content produces a new version
    rather than mutating an existing one. ``(document_id, version_number)`` is
    unique, and ``Document.current_version`` selects the active revision.
    """

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_version_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    document: Mapped["Document"] = relationship(back_populates="versions")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<DocumentVersion id={self.id} document_id={self.document_id} "
            f"version={self.version_number}>"
        )


class SearchQuery(Base):
    """A search query issued by a user, recorded for analytics/deduplication.

    ``query_hash`` is a stable fingerprint of the normalized query used to
    detect duplicate searches; ``filters`` stores the structured filter payload
    as JSONB and ``result_count`` the number of hits returned.
    """

    __tablename__ = "search_queries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    filters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<SearchQuery id={self.id} user_id={self.user_id}>"
