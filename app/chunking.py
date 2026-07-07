"""Simple, deterministic chunker.

Splits text into fixed-size, overlapping segments (values from the config).
Size is measured in characters – deliberately kept simple for the PoC.

Each chunk carries its character offsets (``start``/``end``) into the exact
text that was passed in, so ``text[start:end] == chunk.text``. Highlighting of
hits is handled natively by OpenSearch (``<em>`` fragments), so these offsets
are not indexed; they are kept only as useful chunk provenance.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class Chunk:
    """A text segment with its 0-based index and character offsets."""

    index: int
    text: str
    start: int  # inclusive char offset into the source text
    end: int  # exclusive char offset into the source text

    @property
    def char_count(self) -> int:
        return len(self.text)


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    """Split ``text`` into overlapping chunks of fixed character length.

    Leading/trailing whitespace of each window is trimmed, and the offsets are
    adjusted accordingly so ``text[chunk.start:chunk.end] == chunk.text``.
    Empty / whitespace-only chunks are dropped. If the parameters are omitted,
    the values from the configuration are used.
    """
    settings = get_settings()
    size = chunk_size if chunk_size is not None else settings.chunk_size
    ovl = overlap if overlap is not None else settings.chunk_overlap

    if size <= 0:
        raise ValueError("chunk_size must be > 0")
    if ovl < 0 or ovl >= size:
        raise ValueError("overlap must be >= 0 and < chunk_size")

    if not text.strip():
        return []

    step = size - ovl
    chunks: list[Chunk] = []
    idx = 0
    for window_start in range(0, len(text), step):
        window = text[window_start : window_start + size]
        lstripped = window.lstrip()
        lead = len(window) - len(lstripped)  # trimmed leading whitespace
        piece = lstripped.rstrip()
        if piece:
            start = window_start + lead
            chunks.append(
                Chunk(index=idx, text=piece, start=start, end=start + len(piece))
            )
            idx += 1
        if window_start + size >= len(text):
            break
    return chunks
