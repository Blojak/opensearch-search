"""Controlled vocabularies for filterable metadata fields.

These enums define the allowed value range of the fields ``language``,
``doc_type`` and ``classification``. Free text is deliberately excluded for
these fields so that filters in Qdrant and Postgres work reliably.
"""

from __future__ import annotations

import enum


class Language(str, enum.Enum):
    """Language of a document (ISO-639-1 subset)."""

    DE = "de"
    EN = "en"
    FR = "fr"
    ES = "es"
    IT = "it"
    UNKNOWN = "unknown"


class DocType(str, enum.Enum):
    """Coarse functional document type."""

    REPORT = "report"
    CONTRACT = "contract"
    INVOICE = "invoice"
    EMAIL = "email"
    ARTICLE = "article"
    MANUAL = "manual"
    NOTE = "note"
    OTHER = "other"


class Classification(str, enum.Enum):
    """Administrative confidentiality level."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
