"""Automatic language detection for ingested documents.

Detects the document language offline with ``langdetect`` and maps it onto the
controlled ``Language`` vocabulary; anything outside that set (or undetectable
text) becomes ``Language.UNKNOWN``. Detection is made deterministic via a fixed
seed (langdetect is randomized by default).
"""

from __future__ import annotations

from langdetect import DetectorFactory, detect
from langdetect.lang_detect_exception import LangDetectException

from app.enums import Language

DetectorFactory.seed = 0

_SUPPORTED = {member.value for member in Language}

# Only the leading slice of a document is inspected: it is plenty for language
# detection and bounds the work done on untrusted text (time + ReDoS surface).
_MAX_DETECT_CHARS = 2000


def detect_language(text: str) -> Language:
    """Best-effort detect the language of ``text`` as a ``Language`` value.

    Only the first ``_MAX_DETECT_CHARS`` characters are inspected. Returns
    ``Language.UNKNOWN`` when detection fails (e.g. empty/too-short text) or the
    detected language is outside the controlled vocabulary.
    """
    try:
        code = detect(text[:_MAX_DETECT_CHARS])
    except LangDetectException:
        return Language.UNKNOWN
    return Language(code) if code in _SUPPORTED else Language.UNKNOWN
