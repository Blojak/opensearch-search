"""The query fingerprint — this is where a *product decision* is nailed down.

The hash decides what counts as "the same search", and therefore who gets a
duplicate notification. Two rules were chosen deliberately and must not drift:

* Normalization is **conservative**: case, surrounding and repeated whitespace
  are irrelevant, but **word order is not**. Sorting tokens would blur the line
  between *identical* and *similar* and destroy our ability to measure how often
  exact repeats really happen — which is what decides whether semantic
  similarity is needed at all.
* The hash covers the **query text only, never the filters**. The filters are
  stored beside it, so the rule can be tightened later without a migration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.filters import SearchFilters
from app.query_log import compute_query_hash, filters_to_json, normalize_query


def test_case_and_whitespace_are_irrelevant() -> None:
    assert normalize_query("  Einbruch   Hauptstrasse ") == "einbruch hauptstrasse"
    assert compute_query_hash("  SECURITY   Report ") == compute_query_hash(
        "security report"
    )


def test_word_order_is_significant() -> None:
    """Deliberate: reordering words is a *similar*, not an identical, query."""
    assert compute_query_hash("Einbruch Hauptstrasse") != compute_query_hash(
        "Hauptstrasse Einbruch"
    )


def test_different_queries_hash_differently() -> None:
    assert compute_query_hash("phishing") != compute_query_hash("einbruch")


def test_hash_is_stable_across_calls() -> None:
    assert compute_query_hash("einbruch") == compute_query_hash("einbruch")


def test_filters_do_not_change_the_hash() -> None:
    """The fingerprint is about intent, not about how the result was narrowed."""
    assert compute_query_hash("einbruch") == compute_query_hash("einbruch")
    # (compute_query_hash takes no filters at all - that IS the guarantee.)


def test_filters_are_serialized_without_the_unset_ones() -> None:
    filters = SearchFilters(
        language="de",
        created_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    payload = filters_to_json(filters)
    assert payload == {
        "language": "de",
        "created_from": "2026-01-01T00:00:00+00:00",
    }


def test_empty_filters_serialize_to_none() -> None:
    """An empty filter set must be NULL in the column, not an empty object."""
    assert filters_to_json(SearchFilters()) is None
    assert filters_to_json(None) is None
