"""Recursively ingest a directory of documents.

Walks a directory tree, keeps the files whose extension is accepted, and ingests
them one after another through the normal pipeline (``ingest_file`` -> Postgres +
OpenSearch). Dedup by content hash makes it idempotent: re-running skips files
whose content is already indexed.

Metadata is minimal on purpose — bulk ingest rarely has an Aktenzeichen or a
classification up front (both are optional since the metadata was loosened).
``s3_object_key`` is required, so it is set to each file's path relative to the
walked root. A UI or the later classification step can fill in the rest.

CLI::

    python -m app.ingest_dir <directory>
    python -m app.ingest_dir <directory> --created-by <user-uuid>
    python -m app.ingest_dir <directory> --ext .txt,.pdf,.md
    python -m app.ingest_dir <directory> --dry-run     # list, do not ingest

``created_by`` must reference an existing user. Users are projected just-in-time
from the IdP token on the first authenticated request, so either pass a known id
with ``--created-by`` or make one authenticated request (UI / curl) first; the
script otherwise falls back to the single existing user, or aborts if there is
none.
"""

from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from app.db import session_scope
from app.ingestion import DocumentMeta, ingest_file
from app.models import User

# Extensions the ingest pipeline can make sense of. ``read_document_file`` reads
# .pdf via pypdf and treats everything else as UTF-8 text, so the default set is
# deliberately narrow: walking a real tree would otherwise feed it images and
# binaries as "text".
DEFAULT_EXTENSIONS = frozenset({".txt", ".pdf", ".md"})


def iter_ingestable_files(root: Path, extensions: frozenset[str]) -> list[Path]:
    """Return the files under ``root`` whose suffix is in ``extensions``, sorted.

    Pure filesystem walking, no database — kept separate so it can be tested on
    its own. Comparison is case-insensitive on the extension.
    """
    wanted = {e.lower() for e in extensions}
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() in wanted:
                found.append(path)
    return sorted(found)


def resolve_created_by(explicit: uuid.UUID | None) -> uuid.UUID:
    """The user to attribute the documents to.

    Uses ``explicit`` when given (and verifies it exists), otherwise the single
    existing user. Aborts with a clear message if the id is unknown or the users
    table is empty.
    """
    with session_scope() as session:
        if explicit is not None:
            if session.get(User, explicit) is None:
                raise SystemExit(f"user {explicit} does not exist")
            return explicit
        users = session.query(User).limit(2).all()
        if not users:
            raise SystemExit(
                "no user in the database — make one authenticated request first "
                "(UI or curl with a bearer token), or pass --created-by <uuid>."
            )
        if len(users) > 1:
            raise SystemExit(
                "several users exist — pass --created-by <uuid> to choose one."
            )
        return users[0].id


def ingest_directory(
    root: Path,
    created_by: uuid.UUID,
    extensions: frozenset[str] = DEFAULT_EXTENSIONS,
) -> tuple[int, int, int]:
    """Ingest every accepted file under ``root``. Returns ``(new, dedup, failed)``.

    A single file failing (e.g. an unreadable PDF) is logged and skipped so a
    bulk run is not aborted by one bad file.
    """
    files = iter_ingestable_files(root, extensions)
    new = dedup = failed = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            result = ingest_file(
                path,
                DocumentMeta(created_by=created_by, s3_object_key=rel),
            )
        except Exception as exc:  # pylint: disable=broad-except
            failed += 1
            print(f"  FAIL  {rel}: {exc}")
            continue
        if result.deduplicated:
            dedup += 1
            print(f"  dedup {rel}")
        else:
            new += 1
            print(f"  neu   {rel}  -> {result.num_chunks} chunk(s)")
    return new, dedup, failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recursively ingest a directory of documents into the index.",
    )
    parser.add_argument("directory", help="directory to walk")
    parser.add_argument(
        "--created-by", metavar="UUID", help="user id to attribute the documents to"
    )
    parser.add_argument(
        "--ext",
        metavar="LIST",
        help="comma-separated extensions to accept "
        f"(default: {','.join(sorted(DEFAULT_EXTENSIONS))})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the files that would be ingested, without ingesting",
    )
    args = parser.parse_args()

    root = Path(args.directory)
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    if args.ext:
        extensions = frozenset(
            e if e.startswith(".") else f".{e}"
            for e in (part.strip().lower() for part in args.ext.split(","))
            if e
        )
    else:
        extensions = DEFAULT_EXTENSIONS

    if args.dry_run:
        files = iter_ingestable_files(root, extensions)
        for path in files:
            print(f"  {path.relative_to(root).as_posix()}")
        print(f"{len(files)} file(s) would be ingested "
              f"(accepted: {','.join(sorted(extensions))}).")
        return

    created_by = resolve_created_by(
        uuid.UUID(args.created_by) if args.created_by else None
    )
    new, dedup, failed = ingest_directory(root, created_by, extensions)
    print(
        f"Done: {new} new, {dedup} already indexed, {failed} failed "
        f"(accepted: {','.join(sorted(extensions))})."
    )


if __name__ == "__main__":
    main()
