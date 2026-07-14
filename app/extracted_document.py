"""Taking in a document that somebody else extracted.

Documents are parsed *elsewhere* — Tika for Office formats and email, Docling for
digital PDFs, built by another team — and arrive here in the format pinned in
``docs/extraction-contract.md``: a flat list of typed blocks in reading order.
Nothing in this module knows Tika or Docling; it only knows the contract.

It turns such a payload into the two things the rest of the pipeline runs on:

* ``body_text`` — one canonical string, the concatenation of the blocks worth
  indexing. It stays the source of truth in ``document_versions.body_text``, it
  is what the content hash is computed over, and it is what ``app.passages``
  slices the detail view out of.
* ``blocks`` — the map alongside it: for every block, where it begins and ends in
  ``body_text``, what kind of block it is, and where it sat in the original
  document (``locator``).

Two decisions are worth stating, because everything else follows from them:

**The offsets are ours, not theirs.** The contract deliberately carries no
character offsets. We concatenate the block texts and compute the offsets while
doing so, which guarantees ``body_text[block.start_char:block.end_char] ==
block.text``. A payload can therefore never hand us offsets into a text we cannot
reproduce — and an upgrade on the extraction side cannot silently shift them.

**The extraction classifies, we decide.** The payload *marks* a table of
contents, a running header or a footer; it does not drop them. Whether they reach
the index is a search-quality decision and belongs here: TOC entries are
near-duplicates of the headings and would answer searches with the table of
contents instead of with the finding. So they are kept out of the canonical text
— see ``NON_CONTENT_TYPES``.

The locator is nullable by design: Word files and emails have no page numbers
(pagination only comes into existence when a document is rendered), so ``None``
is the normal case, not an error.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any

# The payload format this module understands. A payload declaring anything else
# is rejected rather than guessed at.
SCHEMA_VERSION = 1

# Blocks separated by a blank line, so the canonical text reads as a document
# (and stays valid Markdown, which the block texts already are).
BLOCK_SEPARATOR = "\n\n"

BLOCK_TYPES = frozenset(
    {
        "heading",
        "paragraph",
        "list_item",
        "table",
        "caption",
        "toc",
        "header",
        "footer",
    }
)

# Marked by the extraction, excluded by us: navigation and page furniture are not
# content. Indexing them would answer searches with the table of contents.
NON_CONTENT_TYPES = frozenset({"toc", "header", "footer"})


@dataclass(frozen=True)
class Block:
    """One block of the document, located in the canonical text.

    ``start_char`` / ``end_char`` are offsets into ``ExtractedDocument.body_text``
    (``body_text[start_char:end_char] == text``). ``locator`` says where the block
    sat in the *original* document — ``{"page": 3}``, ``{"sheet": "Zahlungen"}``,
    ``{"slide": 7}`` — or is ``None`` when the format has no such notion.
    """

    index: int
    type: str
    text: str
    start_char: int
    end_char: int
    locator: dict | None = None
    level: int | None = None  # heading depth, 1 = topmost; None for other types


@dataclass(frozen=True)
class ExtractedDocument:
    """A validated payload: the canonical text and the map of what sits where."""

    body_text: str
    blocks: list[Block]
    source: dict  # tool, tool_version, mime_type, filename, ocr

    @property
    def mime_type(self) -> str | None:
        return self.source.get("mime_type")


class ExtractionError(ValueError):
    """The payload does not satisfy the contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExtractionError(message)


def parse_payload(payload: Any) -> ExtractedDocument:
    """Validate an extraction payload and normalize it.

    A payload that violates the contract is rejected rather than salvaged: a
    malformed handover is a bug on the extraction side and should be loud, not
    quietly indexed as half a document.

    Blocks whose type is in ``NON_CONTENT_TYPES`` are dropped here — the
    extraction still *reports* them, we simply do not index them.
    """
    _require(isinstance(payload, dict), "payload must be a JSON object")

    version = payload.get("schema_version")
    _require(
        version == SCHEMA_VERSION,
        f"unsupported schema_version: {version!r} (expected {SCHEMA_VERSION})",
    )

    source = payload.get("source")
    _require(isinstance(source, dict), "'source' must be an object")

    raw_blocks = payload.get("blocks")
    _require(isinstance(raw_blocks, list), "'blocks' must be a list")
    _require(bool(raw_blocks), "'blocks' must not be empty")

    blocks: list[Block] = []
    pieces: list[str] = []
    cursor = 0

    for position, raw in enumerate(raw_blocks):
        _require(isinstance(raw, dict), f"block {position} is not an object")

        block_type = raw.get("type")
        _require(
            block_type in BLOCK_TYPES,
            f"block {position}: unknown type {block_type!r} "
            f"(allowed: {', '.join(sorted(BLOCK_TYPES))})",
        )

        text = raw.get("text")
        _require(
            isinstance(text, str) and text.strip() != "",
            f"block {position}: 'text' must be a non-empty string",
        )

        locator = raw.get("locator")
        _require(
            locator is None or isinstance(locator, dict),
            f"block {position}: 'locator' must be an object or null",
        )

        level = raw.get("level")
        _require(
            level is None or isinstance(level, int),
            f"block {position}: 'level' must be an integer or null",
        )

        if block_type in NON_CONTENT_TYPES:
            continue

        if pieces:
            cursor += len(BLOCK_SEPARATOR)

        blocks.append(
            Block(
                index=len(blocks),
                type=block_type,
                text=text,
                start_char=cursor,
                end_char=cursor + len(text),
                locator=locator,
                level=level if block_type == "heading" else None,
            )
        )
        pieces.append(text)
        cursor += len(text)

    _require(
        bool(blocks),
        "no indexable content: every block was a table of contents, header or footer",
    )

    return ExtractedDocument(
        body_text=BLOCK_SEPARATOR.join(pieces),
        blocks=blocks,
        source=source,
    )


def block_at(blocks: list[Block], offset: int) -> Block | None:
    """The block a character offset falls into, or ``None`` if none does.

    This is how a search hit gets its provenance: the hit carries ``start_char``,
    and this maps it back to the block it sits in — and therefore to that block's
    ``locator``, which is what lets the UI say "page 3".

    Assumes ``blocks`` in ascending offset order, as ``parse_payload`` produces
    them, and binary-searches instead of scanning: a long document has thousands
    of blocks, and otherwise every hit of every search would walk all of them.
    """
    starts = [block.start_char for block in blocks]
    position = bisect.bisect_right(starts, offset) - 1
    if position < 0:
        return None
    candidate = blocks[position]
    # An offset between two blocks falls into the separator and belongs to neither.
    return candidate if offset < candidate.end_char else None
