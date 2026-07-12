"""OIDC bearer-token validation (framework-agnostic).

The API is a **resource server**: it never runs the login flow, it only
validates the bearer tokens the identity provider issues. The login (an
authorization code flow, later from a UI) happens elsewhere; the client simply
sends ``Authorization: Bearer <jwt>``.

Everything downstream depends on ``Principal``, not on Authlib, JWTs or
Keycloak. Swapping the IdP — or brokering a SAML upstream through Keycloak —
therefore stays a change confined to this module.

This module deliberately has no Flask import: the Flask binding is a thin layer
on top (see ``app.api``), so the validation core survives a framework change.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests
from authlib.jose import JsonWebToken
from authlib.jose.errors import JoseError

from app.config import get_settings
from app.tls import resolve_ca_bundle

# Keycloak signs with RS256. Pinning the accepted algorithms is essential: it
# prevents a token from selecting a weaker algorithm (or "none") than intended.
_ALLOWED_ALGORITHMS = ["RS256"]

_HTTP_TIMEOUT = 10


class AuthError(Exception):
    """Authentication failed. Carries the HTTP status to return."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, derived from the validated token claims."""

    issuer: str  # 'iss' - together with subject the stable identity
    subject: str  # 'sub' - stable, never reassigned by the IdP
    email: str | None
    orgeinheit: str | None
    username: str | None


class _JwksCache:
    """Caches the IdP's signing keys, refetching on expiry or an unknown key id.

    Key rotation is the reason for the ``force`` path: when the IdP starts
    signing with a new key, the cached set no longer contains its ``kid``, so we
    refetch once instead of rejecting valid tokens.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict | None = None
        self._fetched_at: float = 0.0
        self._jwks_uri: str | None = None

    def _discover_jwks_uri(self) -> str:
        """Read the JWKS endpoint from the issuer's OIDC discovery document."""
        settings = get_settings()
        if self._jwks_uri is not None:
            return self._jwks_uri
        url = f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
        try:
            response = requests.get(
                url, timeout=_HTTP_TIMEOUT, verify=resolve_ca_bundle(settings.ca_bundle)
            )
            response.raise_for_status()
            self._jwks_uri = response.json()["jwks_uri"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise AuthError(
                f"cannot reach the identity provider at {url}", status=503
            ) from exc
        return self._jwks_uri

    def get(self, force: bool = False) -> dict:
        """Return the JWKS, refetching when stale or when ``force`` is set."""
        settings = get_settings()
        with self._lock:
            fresh = (
                self._keys is not None
                and (time.monotonic() - self._fetched_at) < settings.oidc_jwks_ttl
            )
            if fresh and not force:
                return self._keys

            uri = self._discover_jwks_uri()
            try:
                response = requests.get(
                    uri,
                    timeout=_HTTP_TIMEOUT,
                    verify=resolve_ca_bundle(settings.ca_bundle),
                )
                response.raise_for_status()
                self._keys = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise AuthError(
                    f"cannot fetch the signing keys from {uri}", status=503
                ) from exc
            self._fetched_at = time.monotonic()
            return self._keys


_jwks_cache = _JwksCache()


def _claims_options() -> dict:
    """Claims that must be present and must match, beyond the signature.

    A valid signature alone is not enough: a token minted by a different realm,
    or for a different audience, would otherwise be accepted here.
    """
    settings = get_settings()
    return {
        "iss": {"essential": True, "value": settings.oidc_issuer},
        "aud": {"essential": True, "values": [settings.oidc_audience]},
        "sub": {"essential": True},
        "exp": {"essential": True},
    }


def _decode(token: str, keys: dict) -> dict:
    """Decode and validate the token against the given key set."""
    jwt = JsonWebToken(_ALLOWED_ALGORITHMS)
    claims = jwt.decode(token, key=keys, claims_options=_claims_options())
    claims.validate()  # signature is checked by decode; this checks exp/nbf/iss/aud
    return claims


def decode_token(token: str) -> Principal:
    """Validate a bearer token and return the caller it identifies.

    Raises ``AuthError`` if the token is malformed, expired, signed by an
    unknown key, or issued for another issuer/audience.
    """
    try:
        claims = _decode(token, _jwks_cache.get())
    except JoseError:
        # Most likely an expired/invalid token - but it may also be a key the
        # cache has not seen yet (rotation), so refetch once and retry.
        try:
            claims = _decode(token, _jwks_cache.get(force=True))
        except JoseError as exc:
            raise AuthError(f"invalid token: {exc}") from exc

    return Principal(
        issuer=claims["iss"],
        subject=claims["sub"],
        email=claims.get("email"),
        orgeinheit=claims.get("orgeinheit"),
        username=claims.get("preferred_username"),
    )


def bearer_token(authorization_header: str | None) -> str:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization_header:
        raise AuthError("missing Authorization header")
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("expected an 'Authorization: Bearer <token>' header")
    return token.strip()
