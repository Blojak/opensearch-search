"""Flask API: document ingestion and lexical / semantic / hybrid search.

Endpoints:
    GET    /health                      liveness probe (the only open one)
    POST   /documents                   ingest a document (raw text or a path)
    GET    /documents/<id>              metadata + ordered chunks
    DELETE /documents/<id>              soft-delete + drop it from OpenSearch
    POST   /documents/<id>/versions     append a corrected version
    GET    /documents/<id>/passage      a search hit with the text around it
    POST   /search                      search with a mode + optional filters
    GET    /notifications               who else searched the same thing

PostgreSQL is the source of truth; the document id is its UUID. The OpenAPI
description lives in ``app.openapi`` and is served at ``/apidocs/``. Slim JSON in
/ JSON out with basic validation and error handling.
"""

from __future__ import annotations

import functools
import inspect
import uuid
from datetime import datetime
from typing import Any

from flasgger import Swagger
from flask import Flask, jsonify, request

from app.auth import AuthError, bearer_token, decode_token
from app.config import get_settings
from app.duplicates import list_notifications, notify_duplicates
from app.enums import Language
from app.ingestion import (
    DocumentMeta,
    add_version,
    delete_document,
    ingest_file,
    ingest_text,
    read_document_file,
)
from app.openapi import SWAGGER_TEMPLATE
from app.opensearch_store import ensure_setup
from app.passages import DEFAULT_CONTEXT_CHARS, SEARCH_CONTEXT_CHARS, extract_passage
from app.query_log import log_search
from app.search import SearchFilters, SearchMode, get_document, search
from app.users import upsert_user


def require_auth(view):
    """Authenticate the caller and inject the identity the view asks for.

    The thin Flask binding around ``app.auth``: it resolves the bearer token to
    a ``Principal`` and projects it into the local ``users`` table
    (just-in-time). The view then receives ``user_id`` (the internal
    ``users.id``) and/or ``principal`` — but only whichever it actually declares,
    so every signature states exactly what it depends on. Endpoints never take a
    user id from the client; it always comes from the token.

    Deliberately without Flask's ``g``: ambient request state makes a view's
    dependencies invisible in its signature, and it does not survive being
    handed to a worker thread (``g`` is bound to a ``ContextVar`` that a plain
    thread does not inherit) — which the notification worker will run into.
    """
    wanted = set(inspect.signature(view).parameters) & {"user_id", "principal"}

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        principal = decode_token(bearer_token(request.headers.get("Authorization")))
        # Refresh the local projection on every authenticated request.
        user_id = upsert_user(principal)

        injected: dict[str, Any] = {}
        if "user_id" in wanted:
            injected["user_id"] = user_id
        if "principal" in wanted:
            injected["principal"] = principal
        return view(*args, **injected, **kwargs)

    return wrapper


class ApiError(Exception):
    """Client-facing error with an HTTP status code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _parse_enum(enum_cls: type, value: Any, field: str):
    """Parse a string into an enum value or raise ApiError(400)."""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in enum_cls)
        raise ApiError(f"invalid {field}: {value!r} (allowed: {allowed})") from exc


def _parse_dt(value: Any, field: str) -> datetime | None:
    """Parse an ISO-8601 string into a datetime or raise ApiError(400)."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ApiError(f"invalid {field}: {value!r} (expected ISO-8601)") from exc


def _parse_int(value: Any, field: str, minimum: int | None = None) -> int:
    """Parse a query parameter into an int or raise ApiError(400)."""
    if value is None:
        raise ApiError(f"'{field}' is required")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(f"invalid {field}: {value!r} (expected an integer)") from exc
    if minimum is not None and parsed < minimum:
        raise ApiError(f"'{field}' must be >= {minimum}")
    return parsed


def _parse_str_list(value: Any, field: str) -> list[str] | None:
    """Parse a string or list of strings into a list, or None. Raises on other types.

    Accepts a bare string (wrapped into a one-element list) for convenience, so a
    client can send ``"application/pdf"`` or ``["application/pdf", ...]``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value else None
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value or None
    raise ApiError(f"invalid {field}: expected a string or list of strings")


def _parse_uuid(value: Any, field: str) -> uuid.UUID | None:
    """Parse a string into a UUID or raise ApiError(400)."""
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ApiError(f"invalid {field}: {value!r} (expected a UUID)") from exc


def _parse_meta(body: dict, created_by: uuid.UUID) -> DocumentMeta:
    """Build DocumentMeta from a request body.

    ``created_by`` is passed in from the authenticated principal, never read from
    the body: a client must not be able to attribute a document to someone else.
    """
    aktenzeichen = body.get("aktenzeichen")
    if not aktenzeichen:
        raise ApiError("'aktenzeichen' is required")
    klassifizierung = body.get("klassifizierung")
    if not klassifizierung:
        raise ApiError("'klassifizierung' is required")
    s3_object_key = body.get("s3_object_key")
    if not s3_object_key:
        raise ApiError("'s3_object_key' is required")

    # Optional: an explicit language overrides the auto-detection at ingest.
    language = _parse_enum(Language, body.get("language"), "language")

    return DocumentMeta(
        aktenzeichen=aktenzeichen,
        klassifizierung=klassifizierung,
        s3_object_key=s3_object_key,
        created_by=created_by,
        verfahren_id=_parse_uuid(body.get("verfahren_id"), "verfahren_id"),
        language=language.value if language else None,
    )


def create_app() -> Flask:
    """Application factory. Ensures the OpenSearch index and the hybrid search
    pipeline exist on startup and mounts the Swagger UI at ``/apidocs/``."""
    app = Flask(__name__)
    Swagger(app, template=SWAGGER_TEMPLATE)
    ensure_setup()

    @app.errorhandler(ApiError)
    def _handle_api_error(err: ApiError):
        return jsonify({"error": err.message}), err.status

    @app.errorhandler(AuthError)
    def _handle_auth_error(err: AuthError):
        response = jsonify({"error": err.message})
        response.status_code = err.status
        if err.status == 401:
            # RFC 6750: tell the client how to authenticate.
            response.headers["WWW-Authenticate"] = 'Bearer realm="opensearch-search"'
        return response

    @app.get("/health")
    def health():
        """Liveness probe.
        ---
        tags: [health]
        security: []   # the only unauthenticated endpoint
        responses:
          200:
            description: Service is up
        """
        return jsonify({"status": "ok"})

    @app.post("/documents")
    @require_auth
    def post_documents(user_id: uuid.UUID):
        """Ingest a document into Postgres and index its chunks in OpenSearch.
        ---
        tags: [documents]
        parameters:
          - in: body
            name: body
            required: true
            schema: {$ref: '#/definitions/IngestRequest'}
        responses:
          201:
            description: Document created
            schema: {$ref: '#/definitions/IngestResult'}
          200:
            description: 'Deduplicated: identical content already existed'
            schema: {$ref: '#/definitions/IngestResult'}
          400:
            description: Validation error
            schema: {$ref: '#/definitions/Error'}
          401:
            description: Missing or invalid bearer token
            schema: {$ref: '#/definitions/Error'}
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError("request body must be a JSON object")

        meta = _parse_meta(body, created_by=user_id)
        path = body.get("path")
        content = body.get("content")

        try:
            if path:
                result = ingest_file(path, meta)
            elif content:
                result = ingest_text(content, meta)
            else:
                raise ApiError("provide either 'content' or 'path'")
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

        payload = {
            "document_id": str(result.document_id),
            "version_number": result.version_number,
            "aktenzeichen": result.aktenzeichen,
            "num_chunks": result.num_chunks,
            "deduplicated": result.deduplicated,
        }
        return jsonify(payload), (200 if result.deduplicated else 201)

    @app.get("/documents/<doc_id>")
    @require_auth
    def get_document_endpoint(doc_id: str):
        """Fetch a document's metadata and ordered chunks.
        ---
        tags: [documents]
        parameters:
          - in: path
            name: doc_id
            required: true
            type: string
            format: uuid
        responses:
          200:
            description: Document
            schema: {$ref: '#/definitions/Document'}
          400:
            description: Invalid id
            schema: {$ref: '#/definitions/Error'}
          401:
            description: Missing or invalid bearer token
            schema: {$ref: '#/definitions/Error'}
          404:
            description: Not found
            schema: {$ref: '#/definitions/Error'}
        """
        document_id = _parse_uuid(doc_id, "document id")
        doc = get_document(document_id)
        if doc is None:
            raise ApiError(f"document {doc_id} not found", status=404)
        return jsonify(doc)

    @app.post("/documents/<doc_id>/versions")
    @require_auth
    def post_version(doc_id: str, user_id: uuid.UUID):
        """Append a corrected version of an existing document.

        The old body is never overwritten: a new version is appended and becomes
        the current one. The previous version stays in Postgres as the audit
        trail, but its chunks leave the index — a search only ever finds the
        current version, so a corrected document is not found twice.

        `change_reason` is optional but strongly encouraged: it is what makes the
        version history readable later on.
        ---
        tags: [documents]
        parameters:
          - in: path
            name: doc_id
            required: true
            type: string
            format: uuid
          - in: body
            name: body
            required: true
            schema: {$ref: '#/definitions/NewVersionRequest'}
        responses:
          201:
            description: New version created
            schema: {$ref: '#/definitions/IngestResult'}
          200:
            description: 'Unchanged: the content is already the current version'
            schema: {$ref: '#/definitions/IngestResult'}
          400:
            description: Validation error
            schema: {$ref: '#/definitions/Error'}
          401:
            description: Missing or invalid bearer token
            schema: {$ref: '#/definitions/Error'}
          404:
            description: Document not found
            schema: {$ref: '#/definitions/Error'}
        """
        document_id = _parse_uuid(doc_id, "document id")
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError("request body must be a JSON object")

        change_reason = body.get("change_reason")
        if change_reason is not None and not isinstance(change_reason, str):
            raise ApiError("'change_reason' must be a string")

        language = _parse_enum(Language, body.get("language"), "language")
        path = body.get("path")
        content = body.get("content")

        if path:
            content, _ = read_document_file(path)
        elif not content:
            raise ApiError("provide either 'content' or 'path'")

        try:
            result = add_version(
                document_id=document_id,
                content=content,
                change_reason=change_reason,
                created_by=user_id,
                language=language.value if language else None,
            )
        except ValueError as exc:
            # The document not existing is a 404; anything else is a bad request.
            status = 404 if "does not exist" in str(exc) else 400
            raise ApiError(str(exc), status=status) from exc

        payload = {
            "document_id": str(result.document_id),
            "version_number": result.version_number,
            "aktenzeichen": result.aktenzeichen,
            "num_chunks": result.num_chunks,
            "deduplicated": result.deduplicated,
        }
        return jsonify(payload), (200 if result.deduplicated else 201)

    @app.get("/documents/<doc_id>/passage")
    @require_auth
    def get_passage_endpoint(doc_id: str):
        """Fetch a search hit with the text surrounding it (for a detail view).

        Pass the offsets a search hit returned (`version_number`, `start_char`,
        `end_char`). `hit_start` / `hit_end` in the response locate the hit
        inside the returned `text`, so the UI can highlight it in context.
        ---
        tags: [documents]
        parameters:
          - in: path
            name: doc_id
            required: true
            type: string
            format: uuid
          - in: query
            name: version
            required: true
            type: integer
            description: version_number from the search hit
          - in: query
            name: start
            required: true
            type: integer
            description: start_char from the search hit
          - in: query
            name: end
            required: true
            type: integer
            description: end_char from the search hit
          - in: query
            name: context
            required: false
            type: integer
            default: 200
            description: characters of context on each side of the hit
        responses:
          200:
            description: The hit in context
            schema: {$ref: '#/definitions/Passage'}
          400:
            description: Validation error
            schema: {$ref: '#/definitions/Error'}
          401:
            description: Missing or invalid bearer token
            schema: {$ref: '#/definitions/Error'}
          404:
            description: Document version not found
            schema: {$ref: '#/definitions/Error'}
        """
        document_id = _parse_uuid(doc_id, "document id")
        version = _parse_int(request.args.get("version"), "version", minimum=1)
        start = _parse_int(request.args.get("start"), "start", minimum=0)
        end = _parse_int(request.args.get("end"), "end", minimum=0)
        context = _parse_int(
            request.args.get("context", DEFAULT_CONTEXT_CHARS), "context", minimum=0
        )
        if end < start:
            raise ApiError("'end' must not be smaller than 'start'")

        passage = extract_passage(document_id, version, start, end, context_chars=context)
        if passage is None:
            raise ApiError(
                f"document {doc_id} has no version {version}", status=404
            )
        return jsonify(
            {
                "document_id": str(document_id),
                "version_number": version,
                "text": passage.text,
                "hit_start": passage.hit_start,
                "hit_end": passage.hit_end,
            }
        )

    @app.delete("/documents/<doc_id>")
    @require_auth
    def delete_document_endpoint(doc_id: str):
        """Soft-delete a document and remove its chunks from OpenSearch.
        ---
        tags: [documents]
        parameters:
          - in: path
            name: doc_id
            required: true
            type: string
            format: uuid
        responses:
          200:
            description: Deleted
          400:
            description: Invalid id
            schema: {$ref: '#/definitions/Error'}
          401:
            description: Missing or invalid bearer token
            schema: {$ref: '#/definitions/Error'}
          404:
            description: Not found
            schema: {$ref: '#/definitions/Error'}
        """
        document_id = _parse_uuid(doc_id, "document id")
        if not delete_document(document_id):
            raise ApiError(f"document {doc_id} not found", status=404)
        return jsonify({"deleted": True, "document_id": doc_id})

    @app.get("/notifications")
    @require_auth
    def get_notifications(user_id: uuid.UUID):
        """Searches by other people that duplicate your own.

        Created as a side effect of searching: when your query matches one
        somebody else already ran, both sides get an entry. `counterpart` names
        who else is researching this — that is the point of the feature.
        Nothing is delivered by email yet; entries stay in status `pending`.
        ---
        tags: [notifications]
        responses:
          200:
            description: Your notifications, newest first
            schema:
              type: array
              items: {$ref: '#/definitions/Notification'}
          401:
            description: Missing or invalid bearer token
            schema: {$ref: '#/definitions/Error'}
        """
        return jsonify(list_notifications(user_id))

    @app.post("/search")
    @require_auth
    def post_search(user_id: uuid.UUID):
        """Search the indexed chunks (lexical, semantic or hybrid).
        ---
        tags: [search]
        parameters:
          - in: body
            name: body
            required: true
            schema: {$ref: '#/definitions/SearchRequest'}
        responses:
          200:
            description: Search results
            schema: {$ref: '#/definitions/SearchResponse'}
          400:
            description: Validation error
            schema: {$ref: '#/definitions/Error'}
          401:
            description: Missing or invalid bearer token
            schema: {$ref: '#/definitions/Error'}
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError("request body must be a JSON object")

        query = body.get("query")
        if not query or not isinstance(query, str):
            raise ApiError("'query' (non-empty string) is required")

        mode = _parse_enum(SearchMode, body.get("mode"), "mode") or SearchMode.HYBRID

        limit = body.get("limit", 10)
        if not isinstance(limit, int) or limit <= 0:
            raise ApiError("'limit' must be a positive integer")

        raw_filters = body.get("filters") or {}
        filter_language = _parse_enum(Language, raw_filters.get("language"), "language")
        filters = SearchFilters(
            aktenzeichen=raw_filters.get("aktenzeichen"),
            verfahren_id=raw_filters.get("verfahren_id"),
            klassifizierung=raw_filters.get("klassifizierung"),
            language=filter_language.value if filter_language else None,
            mime_type=_parse_str_list(raw_filters.get("mime_type"), "mime_type"),
            created_from=_parse_dt(raw_filters.get("created_from"), "created_from"),
            created_to=_parse_dt(raw_filters.get("created_to"), "created_to"),
        )

        hits = search(
            query,
            mode=mode,
            filters=filters,
            limit=limit,
            context_chars=SEARCH_CONTEXT_CHARS,
        )
        results = [
            {
                "score": hit.score,
                "document_id": hit.document_id,
                "version_number": hit.version_number,
                "chunk_index": hit.chunk_index,
                "chunk_text": hit.chunk_text,
                "start_char": hit.start_char,
                "end_char": hit.end_char,
                "highlights": hit.highlights,
                "document": hit.document,
                "context": hit.context,
            }
            for hit in hits
        ]

        # Telemetry, deliberately after the search: it needs the result count,
        # and a failure to record must never turn a working search into an error
        # (both calls swallow and log their own failures).
        query_id = log_search(
            user_id=user_id,
            query_text=query,
            filters=filters,
            result_count=len(results),
        )
        if query_id is not None:
            notify_duplicates(query_id)

        return jsonify(
            {
                "query": query,
                "mode": mode.value,
                "count": len(results),
                "results": results,
            }
        )

    return app


app = create_app()


def main() -> None:
    """Run the development server."""
    settings = get_settings()
    app.run(host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
