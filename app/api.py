"""Flask API: document ingestion and lexical / semantic / hybrid search.

Endpoints:
    GET    /health              liveness probe
    POST   /documents           ingest a document (raw text or server-side path)
    GET    /documents/<id>      fetch a document's metadata + ordered chunks
    DELETE /documents/<id>      soft-delete a document and drop it from OpenSearch
    POST   /search              search with a selectable mode + optional filters

PostgreSQL is the source of truth; the document id is its UUID. Interactive API
docs (Swagger UI) are served at ``/apidocs/``. Slim JSON in / JSON out with
basic validation and error handling.
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
from app.enums import Language
from app.ingestion import DocumentMeta, delete_document, ingest_file, ingest_text
from app.opensearch_store import ensure_setup
from app.passages import DEFAULT_CONTEXT_CHARS, extract_passage
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

# --- OpenAPI / Swagger definitions (served at /apidocs/) ---
SWAGGER_TEMPLATE = {
    "swagger": "2.0",
    "info": {
        "title": "opensearch-search API",
        "description": (
            "Document ingestion (PostgreSQL = source of truth) and "
            "lexical / semantic / hybrid search over the derived OpenSearch index."
        ),
        "version": "1.0.0",
    },
    "consumes": ["application/json"],
    "produces": ["application/json"],
    "tags": [
        {"name": "health"},
        {"name": "documents"},
        {"name": "search"},
    ],
    # Swagger 2.0 has no native bearer type; an apiKey in the Authorization
    # header is the standard way to express it and gives the UI an Authorize
    # button. Applied to every operation except /health (see its docstring).
    "securityDefinitions": {
        "bearerAuth": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "OIDC access token, sent as 'Bearer <jwt>'.",
        }
    },
    "security": [{"bearerAuth": []}],
    "definitions": {
        "Error": {
            "type": "object",
            "properties": {"error": {"type": "string"}},
        },
        "IngestRequest": {
            "type": "object",
            "required": ["aktenzeichen", "klassifizierung", "s3_object_key"],
            "properties": {
                "aktenzeichen": {"type": "string", "example": "AZ-2026-0001"},
                "verfahren_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Optional; must reference an existing verfahren.",
                },
                "klassifizierung": {
                    "type": "string",
                    "description": (
                        "Free string for now; later assigned by an ML classifier "
                        "using the police taxonomy."
                    ),
                    "example": "VS-NfD",
                },
                "s3_object_key": {
                    "type": "string",
                    "example": "documents/az-2026-0001/original.pdf",
                },
                "language": {
                    "type": "string",
                    "enum": [member.value for member in Language],
                    "description": (
                        "Optional. Auto-detected from the content when omitted; "
                        "supplying it overrides the detection."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "Raw text to ingest. Provide either content or path.",
                },
                "path": {
                    "type": "string",
                    "description": "Server-side file path. Provide either content or path.",
                },
            },
        },
        "IngestResult": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "format": "uuid"},
                "version_number": {"type": "integer"},
                "aktenzeichen": {"type": "string"},
                "num_chunks": {"type": "integer"},
                "deduplicated": {"type": "boolean"},
            },
        },
        "SearchFilters": {
            "type": "object",
            "properties": {
                "aktenzeichen": {"type": "string"},
                "verfahren_id": {"type": "string", "format": "uuid"},
                "klassifizierung": {"type": "string"},
                "language": {
                    "type": "string",
                    "enum": [member.value for member in Language],
                },
                "created_from": {"type": "string", "format": "date-time"},
                "created_to": {"type": "string", "format": "date-time"},
            },
        },
        "SearchRequest": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "example": "phishing attempts"},
                "mode": {
                    "type": "string",
                    "enum": ["lexical", "semantic", "hybrid"],
                    "default": "hybrid",
                },
                "limit": {"type": "integer", "default": 10},
                "filters": {"$ref": "#/definitions/SearchFilters"},
            },
        },
        "Document": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "aktenzeichen": {"type": "string"},
                "verfahren_id": {"type": "string", "format": "uuid"},
                "klassifizierung": {"type": "string"},
                "s3_object_key": {"type": "string"},
                "mime_type": {"type": "string"},
                "language": {"type": "string"},
                "created_by": {"type": "string", "format": "uuid"},
                "created_at": {"type": "string", "format": "date-time"},
                "current_version": {"type": "integer"},
                "deleted_at": {"type": "string", "format": "date-time"},
                "content_hash": {"type": "string"},
                "num_chunks": {"type": "integer"},
                "chunks": {"type": "array", "items": {"type": "object"}},
            },
        },
        "Passage": {
            "type": "object",
            "description": (
                "A search hit with the text around it. hit_start/hit_end are "
                "offsets into 'text', so the UI can highlight the hit in context."
            ),
            "properties": {
                "document_id": {"type": "string", "format": "uuid"},
                "version_number": {"type": "integer"},
                "text": {"type": "string"},
                "hit_start": {"type": "integer"},
                "hit_end": {"type": "integer"},
            },
        },
        "SearchHit": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "document_id": {"type": "string", "format": "uuid"},
                "version_number": {"type": "integer"},
                "chunk_index": {"type": "integer"},
                "chunk_text": {"type": "string"},
                "start_char": {"type": "integer"},
                "end_char": {"type": "integer"},
                "highlights": {"type": "array", "items": {"type": "string"}},
                "document": {"$ref": "#/definitions/Document"},
            },
        },
        "SearchResponse": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string"},
                "count": {"type": "integer"},
                "results": {"type": "array", "items": {"$ref": "#/definitions/SearchHit"}},
            },
        },
    },
}


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

    @app.post("/search")
    @require_auth
    def post_search():
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
            created_from=_parse_dt(raw_filters.get("created_from"), "created_from"),
            created_to=_parse_dt(raw_filters.get("created_to"), "created_to"),
        )

        hits = search(query, mode=mode, filters=filters, limit=limit)
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
            }
            for hit in hits
        ]
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
