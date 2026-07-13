"""Parsing of the Authorization header — the front door of every endpoint.

Only the pure header handling is covered here; validating a signature needs a
live IdP and belongs in an integration test.
"""

from __future__ import annotations

import pytest

from app.auth import AuthError, bearer_token


def test_extracts_the_token() -> None:
    assert bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"


def test_scheme_is_matched_case_insensitively() -> None:
    """RFC 6750: the scheme is case-insensitive, so 'bearer' must work too."""
    assert bearer_token("bearer abc") == "abc"


@pytest.mark.parametrize(
    "header",
    [
        None,  # header absent entirely
        "",
        "abc",  # no scheme
        "Basic dXNlcjpwYXNz",  # wrong scheme
        "Bearer",  # scheme without a token
        "Bearer ",  # scheme with an empty token
    ],
)
def test_anything_that_is_not_a_bearer_token_is_rejected(header: str | None) -> None:
    with pytest.raises(AuthError):
        bearer_token(header)


def test_rejection_is_a_401() -> None:
    with pytest.raises(AuthError) as raised:
        bearer_token(None)
    assert raised.value.status == 401
