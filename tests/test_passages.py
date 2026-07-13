"""The context window a UI highlights a hit in.

``hit_start`` / ``hit_end`` are offsets *into the returned window*, not into the
document. Getting that wrong would highlight the wrong span in the UI without
anything failing, so it is pinned down here.
"""

from __future__ import annotations

from app.passages import passage_window

BODY = "".join(f"{i % 10}" for i in range(1000))  # 1000 chars, "0123456789..."


def test_hit_offsets_point_at_the_hit_inside_the_window() -> None:
    window = passage_window(BODY, start_char=400, end_char=450, context_chars=100)
    assert window.text[window.hit_start : window.hit_end] == BODY[400:450]


def test_context_is_added_on_both_sides() -> None:
    window = passage_window(BODY, start_char=400, end_char=450, context_chars=100)
    assert window.text == BODY[300:550]
    assert window.hit_start == 100  # 100 chars of context precede the hit


def test_window_is_clamped_at_the_start_of_the_body() -> None:
    """A hit at the very beginning cannot have context before it."""
    window = passage_window(BODY, start_char=0, end_char=50, context_chars=100)
    assert window.hit_start == 0
    assert window.text[window.hit_start : window.hit_end] == BODY[0:50]


def test_window_is_clamped_at_the_end_of_the_body() -> None:
    window = passage_window(BODY, start_char=980, end_char=1000, context_chars=100)
    assert window.hit_end == len(window.text)
    assert window.text[window.hit_start : window.hit_end] == BODY[980:1000]


def test_out_of_range_offsets_shorten_the_window_instead_of_raising() -> None:
    """Stale offsets (e.g. from a shortened document) must not blow up."""
    window = passage_window(BODY, start_char=5000, end_char=6000, context_chars=50)
    assert window.text == BODY[-50:]
    assert window.hit_start == window.hit_end == len(window.text)


def test_zero_context_returns_exactly_the_hit() -> None:
    window = passage_window(BODY, start_char=400, end_char=450, context_chars=0)
    assert window.text == BODY[400:450]
    assert (window.hit_start, window.hit_end) == (0, 50)
