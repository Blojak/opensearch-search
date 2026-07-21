"""Recursive directory ingest — the file selection, and optional metadata.

The file-walking is pure and tested on its own (no database). The actual ingest
of a document without an Aktenzeichen or classification needs the stack, so that
part is an integration test.
"""

from __future__ import annotations

import uuid

import pytest

from app.ingest_dir import DEFAULT_EXTENSIONS, iter_ingestable_files


# --- pure: which files get picked up -------------------------------------


def _make_tree(root):
    (root / "a.txt").write_text("a")
    (root / "note.md").write_text("m")
    (root / "photo.jpg").write_bytes(b"\xff\xd8")  # must be ignored
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.pdf").write_bytes(b"%PDF-1.4")
    (sub / "c.TXT").write_text("c")  # uppercase extension
    (sub / "d.docx").write_text("d")  # not accepted by default


def test_walks_recursively_and_filters_by_extension(tmp_path):
    _make_tree(tmp_path)

    names = [p.relative_to(tmp_path).as_posix() for p in iter_ingestable_files(tmp_path, DEFAULT_EXTENSIONS)]

    # .txt/.md/.pdf are kept (case-insensitively), .jpg/.docx dropped.
    assert names == ["a.txt", "note.md", "sub/b.pdf", "sub/c.TXT"]


def test_result_is_sorted(tmp_path):
    for name in ["z.txt", "a.txt", "m.txt"]:
        (tmp_path / name).write_text("x")

    names = [p.name for p in iter_ingestable_files(tmp_path, DEFAULT_EXTENSIONS)]

    assert names == sorted(names)


def test_a_custom_extension_set_narrows_the_selection(tmp_path):
    _make_tree(tmp_path)

    names = [p.name for p in iter_ingestable_files(tmp_path, frozenset({".pdf"}))]

    assert names == ["b.pdf"]


def test_empty_directory_yields_nothing(tmp_path):
    assert iter_ingestable_files(tmp_path, DEFAULT_EXTENSIONS) == []


# --- integration: ingest without identity metadata -----------------------

integration = pytest.mark.integration


@pytest.fixture(scope="module")
def stack():
    try:
        from app.db import session_scope
        from app.opensearch_store import ensure_setup, get_client

        with session_scope() as session:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
        get_client().info()
        ensure_setup()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"docker compose stack not reachable: {exc}")
    return True


@pytest.fixture
def user_id(stack) -> uuid.UUID:
    from app.db import session_scope
    from app.models import User

    with session_scope() as session:
        user = session.query(User).first()
        if user is None:
            user = User(email="pytest@example.invalid", orgeinheit="TEST")
            session.add(user)
            session.flush()
        return user.id


@integration
def test_ingest_without_aktenzeichen_or_klassifizierung(user_id) -> None:
    """The whole point of loosening the metadata: only created_by and
    s3_object_key are needed; the document is created with the rest NULL."""
    from app.db import session_scope
    from app.ingestion import DocumentMeta, ingest_text
    from app.models import Document, DocumentVersion
    from app.opensearch_store import delete_document_chunks, get_client

    result = ingest_text(
        f"Ein Dokument ohne Aktenzeichen, ingest {uuid.uuid4().hex}.",
        DocumentMeta(created_by=user_id, s3_object_key="dropzone/no-meta.txt"),
    )

    try:
        assert result.aktenzeichen is None
        with session_scope() as session:
            doc = session.get(Document, result.document_id)
            assert doc.aktenzeichen is None
            assert doc.klassifizierung is None
            assert doc.s3_object_key == "dropzone/no-meta.txt"  # still required
    finally:
        delete_document_chunks(get_client(), result.document_id)
        with session_scope() as session:
            session.query(DocumentVersion).filter(
                DocumentVersion.document_id == result.document_id
            ).delete()
            session.query(Document).filter(
                Document.id == result.document_id
            ).delete()
