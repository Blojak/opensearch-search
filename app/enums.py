"""Controlled vocabulary for the filterable ``language`` field.

Free text is deliberately excluded here so that language filters work reliably.
``klassifizierung`` is *not* an enum: it is a free string that an ML classifier
will later fill from the police taxonomy.
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
