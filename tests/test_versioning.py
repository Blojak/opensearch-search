"""Document versioning — the rules, end to end.

Versioning is the one feature that touches Postgres, OpenSearch and the search
path at the same time, so it is tested against the real stack. Run the compose
services first; without them these are skipped rather than failing:

    docker compose up -d
    pytest -m integration

What is pinned here are the decisions, not the implementation:

* the old body is **kept** in Postgres (that is the audit trail), but
* the old version is **not searchable** — a corrected document must not show up
  twice, and
* ``change_reason`` is **optional** — annotating a change makes the history far
  more useful, but a missing reason must not block a correction.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def stack():
    """Skip the whole module unless Postgres and OpenSearch are reachable."""
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
    """A user to attribute the documents to (normally the token provides this)."""
    from app.db import session_scope
    from app.models import User

    with session_scope() as session:
        user = session.query(User).first()
        if user is None:
            user = User(email="pytest@example.invalid", orgeinheit="TEST")
            session.add(user)
            session.flush()
        return user.id


@pytest.fixture
def document(user_id):
    """A freshly ingested document, removed again afterwards."""
    from app.db import session_scope
    from app.ingestion import DocumentMeta, ingest_text
    from app.models import Document, DocumentVersion
    from app.opensearch_store import delete_document_chunks, get_client

    result = ingest_text(
        "Der Zeuge schildert einen Einbruch in der Hauptstrasse am Montagabend.",
        DocumentMeta(
            aktenzeichen=f"AZ-PYTEST-{uuid.uuid4().hex[:8]}",
            klassifizierung="Test",
            s3_object_key="documents/pytest.txt",
            created_by=user_id,
        ),
    )
    yield result.document_id

    delete_document_chunks(get_client(), result.document_id)
    with session_scope() as session:
        session.query(DocumentVersion).filter(
            DocumentVersion.document_id == result.document_id
        ).delete()
        session.query(Document).filter(Document.id == result.document_id).delete()


def _search(query: str) -> list:
    from app.search import SearchMode, search

    return search(query, mode=SearchMode.LEXICAL, limit=10)


def test_a_new_version_increments_and_becomes_current(document, user_id) -> None:
    from app.db import session_scope
    from app.ingestion import add_version
    from app.models import Document

    result = add_version(
        document_id=document,
        content="Der Zeuge schildert einen Raubueberfall in der Bahnhofstrasse.",
        change_reason="Sachverhalt korrigiert",
        created_by=user_id,
    )

    assert result.version_number == 2
    assert result.deduplicated is False
    with session_scope() as session:
        assert session.get(Document, document).current_version == 2


def test_the_old_body_is_kept_as_the_audit_trail(document, user_id) -> None:
    from app.db import session_scope
    from app.ingestion import add_version
    from app.models import DocumentVersion

    add_version(
        document_id=document,
        content="Voellig anderer Sachverhalt: Raubueberfall Bahnhofstrasse.",
        change_reason="Sachverhalt korrigiert",
        created_by=user_id,
    )

    with session_scope() as session:
        versions = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document)
            .order_by(DocumentVersion.version_number)
            .all()
        )
        assert [v.version_number for v in versions] == [1, 2]
        assert "Einbruch" in versions[0].body_text  # v1 is still there, verbatim
        assert versions[1].change_reason == "Sachverhalt korrigiert"


def test_a_version_without_a_change_reason_is_accepted(document, user_id) -> None:
    """A missing reason must not block a correction."""
    from app.db import session_scope
    from app.ingestion import add_version
    from app.models import DocumentVersion

    result = add_version(
        document_id=document,
        content="Der Zeuge schildert einen Raubueberfall in der Bahnhofstrasse.",
        created_by=user_id,
    )

    assert result.version_number == 2
    with session_scope() as session:
        version = (
            session.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == document,
                DocumentVersion.version_number == 2,
            )
            .one()
        )
        assert version.change_reason is None


def test_the_old_version_is_no_longer_searchable(document, user_id) -> None:
    """The whole point: a corrected document must not be found twice."""
    from app.ingestion import add_version

    assert any(h.document_id == str(document) for h in _search("Einbruch Hauptstrasse"))

    add_version(
        document_id=document,
        content="Der Zeuge schildert einen Raubueberfall in der Bahnhofstrasse.",
        change_reason="Sachverhalt korrigiert",
        created_by=user_id,
    )

    # The old wording is gone from the index ...
    assert not any(
        h.document_id == str(document) for h in _search("Einbruch Hauptstrasse")
    )
    # ... and the new one is there, exactly once.
    hits = [h for h in _search("Raubueberfall Bahnhofstrasse") if h.document_id == str(document)]
    assert len(hits) == 1
    assert hits[0].version_number == 2


def test_resubmitting_the_current_content_is_a_no_op(document, user_id) -> None:
    from app.ingestion import add_version

    result = add_version(
        document_id=document,
        content="Der Zeuge schildert einen Einbruch in der Hauptstrasse am Montagabend.",
        change_reason="versehentlich erneut hochgeladen",
        created_by=user_id,
    )

    assert result.deduplicated is True
    assert result.version_number == 1  # still v1, nothing was appended


def test_versioning_an_unknown_document_is_rejected(user_id) -> None:
    from app.ingestion import add_version

    with pytest.raises(ValueError, match="does not exist"):
        add_version(
            document_id=uuid.uuid4(),
            content="irgendwas",
            change_reason="test",
            created_by=user_id,
        )


def test_language_is_redetected_from_the_new_content(document, user_id) -> None:
    """The body changed, so the language may have changed with it."""
    from app.db import session_scope
    from app.ingestion import add_version
    from app.models import Document

    add_version(
        document_id=document,
        content="The witness describes a robbery at the central station last night.",
        change_reason="Uebersetzung nachgereicht",
        created_by=user_id,
    )

    with session_scope() as session:
        assert session.get(Document, document).language == "en"
