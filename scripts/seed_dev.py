"""Seed the deterministic dev fixture that ingest needs.

``verfahren`` is owned by another bounded context and has no API of its own, so
a document referencing one needs the row to exist. This inserts a fixed-UUID
placeholder verfahren (idempotent — safe to re-run, e.g. after truncating the
tables), using the same UUID as the README ingest example.

Users are **not** seeded any more: they are projected just-in-time from the IdP
token on the first authenticated request (see ``app/users.py``).

Run from the project root::

    python scripts/seed_dev.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Make the ``app`` package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import session_scope  # noqa: E402
from app.models import Verfahren  # noqa: E402

VERFAHREN_ID = uuid.UUID("111f3f39-54f7-435e-a4bf-47dc088c5e79")


def seed() -> None:
    """Upsert the fixed dev verfahren (idempotent)."""
    with session_scope() as session:
        session.merge(Verfahren(id=VERFAHREN_ID))
    print("Seeded dev fixtures:")
    print(f"  verfahren_id: {VERFAHREN_ID}")
    print("  (users are created just-in-time from the IdP token)")


if __name__ == "__main__":
    seed()
