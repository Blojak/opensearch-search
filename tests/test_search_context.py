"""Search context enrichment — the decision, against the real stack.

A semantic hit has no term highlights (nothing to underline), so the search
attaches a *context window*: the chunk shown inside the surrounding body text.
What is pinned here:

* the context is only attached when asked for (``context_chars > 0``),
* the marked span inside the window is exactly the chunk
  (``context.text[hit_start:hit_end] == chunk_text``), which is what lets the UI
  highlight the chunk in place,
* the window actually adds surrounding text (context on at least one side).

Needs Postgres + OpenSearch, so run the compose stack first:

    docker compose up -d
    pytest -m integration
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
    """A document long enough to chunk, so a hit has context around it."""
    from app.db import session_scope
    from app.ingestion import DocumentMeta, ingest_text
    from app.models import Document, DocumentVersion
    from app.opensearch_store import delete_document_chunks, get_client

    body = (
        "Die Kriminaltechnik sichert Spuren am Tatort. "
        "Daktyloskopische Spuren werden mit Rußpulver sichtbar gemacht und "
        "auf Folie abgezogen. " * 60
    )
    result = ingest_text(
        body,
        DocumentMeta(
            aktenzeichen=f"AZ-CTX-{uuid.uuid4().hex[:8]}",
            klassifizierung="Test",
            s3_object_key="documents/ctx.txt",
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


def test_no_context_unless_asked_for(document) -> None:
    from app.search import SearchMode, search

    hits = search("daktyloskopische Spuren", mode=SearchMode.SEMANTIC, limit=5)
    assert hits
    assert all(hit.context is None for hit in hits)


def test_semantic_hits_get_a_context_window(document) -> None:
    from app.passages import SEARCH_CONTEXT_CHARS
    from app.search import SearchMode, search

    hits = [
        h
        for h in search(
            "daktyloskopische Spuren",
            mode=SearchMode.SEMANTIC,
            limit=10,
            context_chars=SEARCH_CONTEXT_CHARS,
        )
        if h.document_id == str(document)
    ]
    assert hits

    for hit in hits:
        # A pure semantic hit has nothing to underline via term highlights.
        assert hit.highlights == []
        assert hit.context is not None
        text = hit.context["text"]
        start, end = hit.context["hit_start"], hit.context["hit_end"]
        # The marked span is exactly the chunk — this is what the UI highlights.
        assert text[start:end] == hit.chunk_text

    # At least one hit sits in the middle of the body, so it has context on both
    # sides — the whole point of the window.
    assert any(
        h.context["hit_start"] > 0 and h.context["hit_end"] < len(h.context["text"])
        for h in hits
    )
