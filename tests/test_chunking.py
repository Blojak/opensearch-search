"""The chunker's central promise: the offsets point back at the exact text.

``passages`` and the UI's hit highlighting both slice the stored body with the
offsets a chunk carries. If that invariant ever breaks, hits would be
highlighted in the wrong place — silently, because nothing else would fail.
"""

from __future__ import annotations

import pytest

from app.chunking import chunk_text


def _body(words: int) -> str:
    return " ".join(f"wort{i:04d}" for i in range(words))


@pytest.mark.parametrize("size, overlap", [(120, 0), (120, 30), (512, 64), (80, 40)])
def test_offsets_slice_back_to_the_chunk_text(size: int, overlap: int) -> None:
    """body[start_char:end_char] must equal the chunk's own text — always."""
    body = _body(400)
    for chunk in chunk_text(body, chunk_size=size, overlap=overlap):
        assert body[chunk.start_char : chunk.end_char] == chunk.text


def test_chunks_are_indexed_from_zero_without_gaps() -> None:
    body = _body(200)
    chunks = chunk_text(body)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_cover_the_whole_body() -> None:
    """No text may be lost between chunks: the last chunk reaches the end."""
    body = _body(300)
    chunks = chunk_text(body)
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(body)


def test_overlap_makes_consecutive_chunks_share_text() -> None:
    body = _body(300)
    chunks = chunk_text(body, chunk_size=200, overlap=50)
    # With overlap the next chunk must start before the previous one ended.
    for previous, following in zip(chunks, chunks[1:]):
        assert following.start_char < previous.end_char


def test_blank_input_yields_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []
