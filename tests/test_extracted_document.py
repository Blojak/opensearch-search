"""Taking in an extracted document — the rules, on pure logic.

No database, no OpenSearch, no Tika, no Docling: this is the seam where a
payload from the extraction team enters the pipeline, and it can be reasoned
about on its own.

What is pinned here are the decisions from ``docs/extraction-contract.md``:

* the canonical text is **derived by us**, so the offsets always address the text
  we actually store — ``body_text[start:end] == text`` for every block,
* table of contents, headers and footers are **classified by them, dropped by
  us**: they must not answer a search,
* a ``locator`` may be ``null`` — Word has no page numbers, and that is normal,
* a payload that violates the contract is **rejected**, not salvaged.

The two committed sample payloads are exercised as well, so the module is tested
against real extractor output and not only against what we imagined it to be.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extracted_document import (
    BLOCK_SEPARATOR,
    Block,
    ExtractionError,
    block_at,
    parse_payload,
)

SAMPLES = Path(__file__).resolve().parent.parent / "sample_docs"


def payload(*blocks: dict) -> dict:
    """A minimal valid payload around the given blocks."""
    return {
        "schema_version": 1,
        "source": {"tool": "docling", "mime_type": "application/pdf"},
        "blocks": list(blocks),
    }


def test_the_canonical_text_is_the_blocks_joined_in_reading_order() -> None:
    doc = parse_payload(
        payload(
            {"type": "heading", "level": 1, "text": "Spurenlage", "locator": None},
            {"type": "paragraph", "text": "Werkzeugspuren am Fenster.", "locator": None},
        )
    )

    assert doc.body_text == "Spurenlage" + BLOCK_SEPARATOR + "Werkzeugspuren am Fenster."


def test_every_offset_addresses_the_text_we_store() -> None:
    """The invariant the whole pipeline rests on: hash, chunking and /passage
    all slice this exact string with these exact offsets."""
    doc = parse_payload(
        payload(
            {"type": "heading", "level": 2, "text": "Sachverhalt", "locator": None},
            {"type": "paragraph", "text": "Einbruch am Montagabend.", "locator": None},
            {"type": "table", "text": "| a | b |\n|---|---|\n| 1 | 2 |", "locator": None},
        )
    )

    for block in doc.blocks:
        assert doc.body_text[block.start_char : block.end_char] == block.text


def test_toc_headers_and_footers_do_not_reach_the_index() -> None:
    """They are what the extraction marks and we throw away: a TOC entry is a
    near-duplicate of a heading and would answer a search with the contents page."""
    doc = parse_payload(
        payload(
            {"type": "header", "text": "VS-NfD", "locator": {"page": 1}},
            {"type": "toc", "text": "Spurenlage\t2", "locator": {"page": 1}},
            {"type": "paragraph", "text": "Der eigentliche Inhalt.", "locator": {"page": 1}},
            {"type": "footer", "text": "Seite 1 von 1", "locator": {"page": 1}},
        )
    )

    assert [b.text for b in doc.blocks] == ["Der eigentliche Inhalt."]
    assert doc.body_text == "Der eigentliche Inhalt."
    assert "VS-NfD" not in doc.body_text
    assert "Spurenlage" not in doc.body_text


def test_a_payload_with_nothing_but_furniture_is_rejected() -> None:
    with pytest.raises(ExtractionError, match="no indexable content"):
        parse_payload(
            payload(
                {"type": "header", "text": "VS-NfD", "locator": None},
                {"type": "toc", "text": "Spurenlage\t2", "locator": None},
            )
        )


def test_the_locator_survives_and_may_be_null() -> None:
    doc = parse_payload(
        payload(
            {"type": "paragraph", "text": "Auf Seite drei.", "locator": {"page": 3}},
            {"type": "paragraph", "text": "Aus einem Word-Dokument.", "locator": None},
        )
    )

    assert doc.blocks[0].locator == {"page": 3}
    assert doc.blocks[1].locator is None


def test_the_level_belongs_to_headings_only() -> None:
    doc = parse_payload(
        payload(
            {"type": "heading", "level": 2, "text": "Spurenlage", "locator": None},
            {"type": "paragraph", "level": 2, "text": "Fließtext.", "locator": None},
        )
    )

    assert doc.blocks[0].level == 2
    assert doc.blocks[1].level is None


@pytest.mark.parametrize(
    ("broken", "message"),
    [
        ({"schema_version": 2, "source": {}, "blocks": [{"type": "paragraph", "text": "x"}]},
         "unsupported schema_version"),
        ({"schema_version": 1, "source": {}, "blocks": []}, "must not be empty"),
        ({"schema_version": 1, "source": {}, "blocks": "nope"}, "must be a list"),
        ({"schema_version": 1, "blocks": [{"type": "paragraph", "text": "x"}]},
         "'source' must be an object"),
    ],
)
def test_a_payload_that_violates_the_contract_is_rejected(broken, message) -> None:
    with pytest.raises(ExtractionError, match=message):
        parse_payload(broken)


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ({"type": "sidebar", "text": "x"}, "unknown type"),
        ({"type": "paragraph", "text": "   "}, "non-empty string"),
        ({"type": "paragraph"}, "non-empty string"),
        ({"type": "paragraph", "text": "x", "locator": "page 3"}, "'locator' must be"),
        ({"type": "heading", "text": "x", "level": "eins"}, "'level' must be"),
    ],
)
def test_a_block_that_violates_the_contract_is_rejected(block, message) -> None:
    with pytest.raises(ExtractionError, match=message):
        parse_payload(payload(block))


def test_a_hit_is_mapped_back_to_the_block_it_sits_in() -> None:
    """This is what will let a search hit say 'page 3': the hit carries an
    offset, and the block it falls into carries the locator."""
    doc = parse_payload(
        payload(
            {"type": "paragraph", "text": "Erster Absatz.", "locator": {"page": 1}},
            {"type": "paragraph", "text": "Zweiter Absatz.", "locator": {"page": 7}},
        )
    )
    second = doc.blocks[1]

    hit = block_at(doc.blocks, second.start_char + 3)

    assert hit is not None
    assert hit.locator == {"page": 7}


def test_an_offset_in_the_separator_or_beyond_the_text_belongs_to_no_block() -> None:
    doc = parse_payload(
        payload(
            {"type": "paragraph", "text": "Erster Absatz.", "locator": None},
            {"type": "paragraph", "text": "Zweiter Absatz.", "locator": None},
        )
    )
    gap = doc.blocks[0].end_char  # the "\n\n" between the two

    assert block_at(doc.blocks, gap) is None
    assert block_at(doc.blocks, len(doc.body_text)) is None
    assert block_at(doc.blocks, -1) is None
    assert block_at([], 0) is None


def test_block_at_finds_every_offset_of_every_block() -> None:
    """Exhaustive over a small document, because an off-by-one here would
    mislabel the provenance of a hit rather than fail loudly."""
    doc = parse_payload(
        payload(
            {"type": "heading", "level": 1, "text": "A", "locator": {"page": 1}},
            {"type": "paragraph", "text": "Bee", "locator": {"page": 2}},
            {"type": "paragraph", "text": "Cee!", "locator": {"page": 3}},
        )
    )

    for block in doc.blocks:
        for offset in range(block.start_char, block.end_char):
            assert block_at(doc.blocks, offset) is block


# --- against the committed sample payloads --------------------------------


def load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def test_the_docling_sample_normalizes_to_a_clean_body() -> None:
    """The real Docling output for a Word file: no page numbers anywhere (Word
    has none), and the 29 table-of-contents entries must not survive."""
    doc = parse_payload(load("extracted_docling_example.json"))

    assert len(doc.blocks) == 536  # 565 in the payload, minus 29 toc entries
    assert all(b.locator is None for b in doc.blocks)
    assert "Impulspapier" in doc.body_text
    for block in doc.blocks:
        assert doc.body_text[block.start_char : block.end_char] == block.text

    # Every heading keeps a usable depth — that is what a heading path is built
    # from later. (The document's own "Inhalt" heading is gone: it is the table
    # of contents and was classified as such.)
    headings = [b for b in doc.blocks if b.type == "heading"]
    assert len(headings) == 35
    assert all(isinstance(b.level, int) and b.level >= 1 for b in headings)
    assert "Inhalt" not in [b.text for b in headings]


def test_the_pdf_sample_keeps_its_page_numbers() -> None:
    doc = parse_payload(load("extracted_pdf_example.json"))

    assert doc.mime_type == "application/pdf"
    # header, footer and both toc entries are gone; everything else survives.
    assert [b.type for b in doc.blocks] == [
        "heading", "heading", "paragraph", "paragraph",
        "heading", "list_item", "list_item", "list_item",
        "caption", "table", "paragraph",
    ]
    assert "VS-NfD" not in doc.body_text
    assert "Seite 3 von 3" not in doc.body_text

    table = next(b for b in doc.blocks if b.type == "table")
    assert table.locator == {"page": 2}
    assert block_at(doc.blocks, table.start_char + 5) is table
