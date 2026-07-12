"""Backfill the language of documents that still carry the 'unknown' default.

The language is detected at ingest, so documents created before the field
existed keep the ``'unknown'`` server default. Reindexing alone does not fix
that: it mirrors ``documents.language`` from Postgres rather than recomputing
it. This script therefore detects the language from the current version's body
text, writes it to Postgres (the source of truth) and only then reindexes the
affected documents so the OpenSearch chunks pick up the new value.

Idempotent: documents whose language is already set, and those that stay
undetectable, are left untouched. Run from the project root::

    python scripts/backfill_language.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

# Make the ``app`` package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import session_scope  # noqa: E402
from app.enums import Language  # noqa: E402
from app.language import detect_language  # noqa: E402
from app.models import Document, DocumentVersion  # noqa: E402
from app.reindex import reindex_document  # noqa: E402


def _current_body(session, document: Document) -> str | None:
    """Body text of the document's current version, or ``None`` if missing."""
    version = (
        session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_number == document.current_version,
        )
        .one_or_none()
    )
    return version.body_text if version else None


def backfill(dry_run: bool = False) -> int:
    """Detect and persist the language of every live 'unknown' document.

    Returns how many documents were updated (0 on a dry run).
    """
    updated: list[uuid.UUID] = []

    with session_scope() as session:
        documents = (
            session.query(Document)
            .filter(
                Document.language == Language.UNKNOWN.value,
                Document.deleted_at.is_(None),
            )
            .all()
        )
        for document in documents:
            body = _current_body(session, document)
            if not body:
                continue
            language = detect_language(body).value
            if language == Language.UNKNOWN.value:
                print(f"  {document.aktenzeichen}: still undetectable, skipped")
                continue
            print(f"  {document.aktenzeichen}: unknown -> {language}")
            if not dry_run:
                document.language = language
                updated.append(document.id)

    # Reindex after the transaction committed, so OpenSearch mirrors the values
    # that are actually persisted in Postgres.
    for document_id in updated:
        reindex_document(document_id)

    return len(updated)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill the language of documents still set to 'unknown'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only report what would change, write nothing",
    )
    args = parser.parse_args()

    count = backfill(dry_run=args.dry_run)
    if args.dry_run:
        print("Dry run: nothing written.")
    else:
        print(f"Backfilled and reindexed {count} document(s).")


if __name__ == "__main__":
    main()
