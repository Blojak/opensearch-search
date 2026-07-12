"""TLS trust configuration for outbound HTTPS.

In a corporate environment the egress proxy terminates TLS with the
organization's own CA, so anything the app talks to over HTTPS (the HuggingFace
mirror, the IdP's JWKS endpoint) must trust that CA rather than the certifi
bundle shipped with ``requests``.

``resolve_ca_bundle`` finds the bundle to use; ``configure_ca_env`` additionally
exports it into the standard environment variables so libraries that read them
(requests, urllib, curl) pick it up without being passed an explicit path.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

# Well-known system CA bundle locations, tried in order when neither the
# settings nor the standard environment variables point at a bundle. Covers the
# common Linux distributions plus Alpine/macOS.
_SYSTEM_CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL/CentOS/Fedora
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # Alpine/macOS
)

# Standard env vars the HTTP stack (requests/urllib) consults for a CA bundle.
CA_ENV_VARS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")


def resolve_ca_bundle(configured: str | None) -> str | None:
    """Resolve the CA bundle to use for HTTPS, or ``None`` to keep the default.

    Precedence: an explicitly configured path wins, then any already-set
    standard environment variable, then the OpenSSL default verify paths, then
    the well-known system locations.
    """
    if configured:
        return configured
    for var in CA_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    default = ssl.get_default_verify_paths().cafile
    if default and Path(default).is_file():
        return default
    for path in _SYSTEM_CA_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def configure_ca_env(configured: str | None) -> str | None:
    """Export the resolved CA bundle into the standard environment variables.

    An explicit setting overrides; unset variables are only filled in, so a
    deliberately configured environment stays authoritative. Returns the bundle
    that was resolved (or ``None``).
    """
    ca_bundle = resolve_ca_bundle(configured)
    if ca_bundle:
        for var in CA_ENV_VARS:
            if configured or var not in os.environ:
                os.environ[var] = ca_bundle
    return ca_bundle
