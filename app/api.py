"""Flask API: document ingestion and lexical / semantic / hybrid search.

Endpoints:
    GET    /health              liveness probe
    POST   /documents           ingest a document (raw text or server-side path)
    GET    /documents/<id>      fetch a document's metadata + ordered chunks
    DELETE /documents/<id>      delete a document (all its chunks) from OpenSearch
    POST   /search              search with a selectable mode + optional filters

The document id is the sha256 content hash. Slim JSON in / JSON out with basic
validation and error handling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Flask, jsonify, request

from app.config import get_settings
from app.enums import Classification, DocType, Language
from app.ingestion import DocumentMeta, delete_document, ingest_file, ingest_text
from app.opensearch_store import ensure_setup
from app.search import SearchFilters, SearchMode, get_document, search


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


def _parse_meta(body: dict) -> DocumentMeta:
    """Build DocumentMeta from a request body."""
    filename = body.get("filename")
    if not filename:
        raise ApiError("'filename' is required")
    return DocumentMeta(
        filename=filename,
        title=body.get("title"),
        language=_parse_enum(Language, body.get("language"), "language")
        or Language.UNKNOWN,
        doc_type=_parse_enum(DocType, body.get("doc_type"), "doc_type")
        or DocType.OTHER,
        classification=_parse_enum(
            Classification, body.get("classification"), "classification"
        )
        or Classification.INTERNAL,
        created_at=_parse_dt(body.get("created_at"), "created_at"),
        source=body.get("source"),
        extra=body.get("extra") or {},
    )


def create_app() -> Flask:
    """Application factory. Ensures the OpenSearch index and the hybrid search
    pipeline exist on startup."""
    app = Flask(__name__)
    ensure_setup()

    @app.errorhandler(ApiError)
    def _handle_api_error(err: ApiError):
        return jsonify({"error": err.message}), err.status

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/documents")
    def post_documents():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError("request body must be a JSON object")

        meta = _parse_meta(body)
        path = body.get("path")
        content = body.get("content")

        if path:
            result = ingest_file(path, meta)
        elif content:
            result = ingest_text(content, meta)
        else:
            raise ApiError("provide either 'content' or 'path'")

        payload = {
            "document_id": result.document_id,
            "filename": result.filename,
            "num_chunks": result.num_chunks,
            "deduplicated": result.deduplicated,
        }
        return jsonify(payload), (200 if result.deduplicated else 201)

    @app.get("/documents/<doc_id>")
    def get_document_endpoint(doc_id: str):
        doc = get_document(doc_id)
        if doc is None:
            raise ApiError(f"document {doc_id} not found", status=404)
        return jsonify(doc)

    @app.delete("/documents/<doc_id>")
    def delete_document_endpoint(doc_id: str):
        if not delete_document(doc_id):
            raise ApiError(f"document {doc_id} not found", status=404)
        return jsonify({"deleted": True, "document_id": doc_id})

    @app.post("/search")
    def post_search():
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
        filters = SearchFilters(
            doc_type=_parse_enum(DocType, raw_filters.get("doc_type"), "doc_type"),
            language=_parse_enum(Language, raw_filters.get("language"), "language"),
            classification=_parse_enum(
                Classification, raw_filters.get("classification"), "classification"
            ),
            created_from=_parse_dt(raw_filters.get("created_from"), "created_from"),
            created_to=_parse_dt(raw_filters.get("created_to"), "created_to"),
        )

        hits = search(query, mode=mode, filters=filters, limit=limit)
        results = [
            {
                "score": hit.score,
                "doc_id": hit.doc_id,
                "chunk_index": hit.chunk_index,
                "chunk_text": hit.chunk_text,
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
