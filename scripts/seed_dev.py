"""Seed deterministic dev fixtures so local document ingest works.

``users`` and ``verfahren`` are owned by another bounded context and have no
API of their own. For local testing this inserts a fixed-UUID placeholder user
and verfahren (idempotent — safe to re-run, e.g. after truncating the tables),
using the same UUIDs as the README ingest example. Run from the project root::

    python scripts/seed_dev.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Make the ``app`` package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import session_scope  # noqa: E402
from app.models import User, Verfahren  # noqa: E402

USER_ID = uuid.UUID("85709c0d-3a9b-4b72-9bd2-ebf672982868")
VERFAHREN_ID = uuid.UUID("111f3f39-54f7-435e-a4bf-47dc088c5e79")


def seed() -> None:
    """Upsert the fixed dev user + verfahren (idempotent)."""
    with session_scope() as session:
        session.merge(User(id=USER_ID, orgeinheit="K3"))
        session.merge(Verfahren(id=VERFAHREN_ID))
    print("Seeded dev fixtures:")
    print(f"  created_by   (user):      {USER_ID}")
    print(f"  verfahren_id (verfahren): {VERFAHREN_ID}")


if __name__ == "__main__":
    seed()
