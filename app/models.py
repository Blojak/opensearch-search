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
    """Local projection of an identity owned by the IdP.

    The identity itself lives in the identity provider; this table exists so the
    metadata models can reference a user via a real foreign key and so claims we
    need (email for notifications, orgeinheit) are available without calling the
    IdP. Rows are upserted just-in-time from the token claims on each
    authenticated request.

    ``id`` stays an internal UUID on purpose: it is what every foreign key points
    at, so it must never change. ``(issuer, subject)`` is the stable lookup key
    from the token (OIDC guarantees ``sub`` is stable and never reassigned),
    which keeps the references intact across an IdP migration. ``email`` is a
    plain attribute, never an identifier: it is mutable and can be reassigned.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_user_issuer_subject"),
        UniqueConstraint("email", name="uq_user_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Nullable: rows seeded before the IdP existed carry no token identity.
    issuer: Mapped[str | None] = mapped_column(
        String(255), nullable=True, doc="OIDC issuer (iss claim)",
    )
    subject: Mapped[str | None] = mapped_column(
        String(255), nullable=True, doc="Stable IdP subject (sub claim)",
    )
    email: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        doc="Attribute, not an identifier; used for notifications",
    )
    orgeinheit: Mapped[str | None] = mapped_column(
        String(32), nullable=True, doc="Organizational unit (short code)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User id={self.id} subject={self.subject!r}>"


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
    language: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'unknown'"),
        doc="ISO-639-1 language code (auto-detected; controlled vocabulary)",
    )
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


class QueryNotification(Base):
    """Notification that a user's search duplicates an earlier one.

    Links the ``original`` query to the ``duplicate`` query (both in
    ``search_queries``) and records which user was notified plus a simple
    processing ``status`` (defaults to ``pending``).
    """

    __tablename__ = "query_notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    original_query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_queries.id"), nullable=False,
    )
    duplicate_query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_queries.id"), nullable=False,
    )
    notified_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<QueryNotification id={self.id} status={self.status!r}>"
