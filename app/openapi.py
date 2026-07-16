"""The OpenAPI description served at ``/apidocs/``.

Kept out of ``app.api`` so the routes stay readable — the template is data, and
it was a third of that module. It stays a Python module rather than a JSON/YAML
file for one reason: the enums below are derived from ``app.enums`` at import
time. In a hand-maintained data file that list would have to be duplicated, and
would silently start lying the day somebody adds a language.
"""

from __future__ import annotations

from app.enums import Language

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
        {"name": "notifications"},
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
        "NewVersionRequest": {
            "type": "object",
            "properties": {
                "change_reason": {
                    "type": "string",
                    "description": (
                        "Optional. Why is the document changing? Not enforced, but "
                        "it is what makes the version history readable later on."
                    ),
                    "example": "Scan der Seite 3 nachgetragen",
                },
                "content": {
                    "type": "string",
                    "description": "The corrected text. Provide either content or path.",
                },
                "path": {
                    "type": "string",
                    "description": "Server-side file path. Provide either content or path.",
                },
                "language": {
                    "type": "string",
                    "enum": [member.value for member in Language],
                    "description": (
                        "Optional. Re-detected from the new content when omitted."
                    ),
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
                "mime_type": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Document type = MIME type(s); OR-matched. A bare string "
                        "is also accepted. E.g. ['application/pdf']."
                    ),
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
        "Notification": {
            "type": "object",
            "description": (
                "Somebody else searched the same thing. 'counterpart' is who "
                "that was, so the two can talk to each other."
            ),
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "status": {"type": "string", "example": "pending"},
                "created_at": {"type": "string", "format": "date-time"},
                "query_text": {"type": "string"},
                "searched_at": {"type": "string", "format": "date-time"},
                "counterpart": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "format": "uuid"},
                        "email": {"type": "string"},
                        "orgeinheit": {"type": "string"},
                    },
                },
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
