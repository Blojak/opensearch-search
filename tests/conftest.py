"""Shared test configuration.

The OIDC settings are required and have no defaults in code on purpose (a
missing value must fail loudly in production). Tests therefore have to supply
them, and they do so here rather than relying on a developer's ``.env`` — the
suite must give the same result on a machine that has none.
"""

from __future__ import annotations

import os

# Must happen before anything imports app.config, which reads the environment.
os.environ.setdefault("OIDC_ISSUER", "http://localhost:8080/realms/osearch")
os.environ.setdefault("OIDC_AUDIENCE", "osearch-api")
