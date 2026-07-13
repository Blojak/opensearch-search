"""Language detection at ingest.

Detection is best-effort, so the tests pin the *contract* rather than the
detector's cleverness: a confident guess maps onto the controlled vocabulary,
and anything undetectable or outside it becomes ``unknown`` instead of raising
or leaking a foreign code into the database.
"""

from __future__ import annotations

import pytest

from app.enums import Language
from app.language import detect_language


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "Der Zeuge schildert einen Einbruchdiebstahl in der Hauptstrasse "
            "am Montagabend und nennt mehrere Verdächtige.",
            Language.DE,
        ),
        (
            "The quarterly security report summarizes the incidents that were "
            "detected during the first quarter of the year.",
            Language.EN,
        ),
    ],
)
def test_detects_the_supported_languages(text: str, expected: Language) -> None:
    assert detect_language(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "12345 67890", "!!! ??? ..."])
def test_undetectable_text_becomes_unknown(text: str) -> None:
    """Must never raise — an unusable document would otherwise fail to ingest."""
    assert detect_language(text) == Language.UNKNOWN


def test_result_is_always_within_the_controlled_vocabulary() -> None:
    """A language outside our enum (here: Japanese) must not leak through."""
    assert detect_language("これは日本語のテキストです。" * 5) == Language.UNKNOWN


def test_detection_is_deterministic() -> None:
    """langdetect is probabilistic; a fixed seed keeps ingest reproducible."""
    text = "Der Ermittlungsbericht beschreibt einen Verkehrsunfall mit Personenschaden."
    assert {detect_language(text) for _ in range(5)} == {Language.DE}
