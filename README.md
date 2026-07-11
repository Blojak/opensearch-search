# opensearch-search — lexical + semantic document search (PoC)

Proof of concept for **lexical (BM25), semantic (kNN) and hybrid** search over a
document collection, with **native highlighting** of the matching terms.
Documents are split into chunks, embedded locally with a multilingual model, and
indexed in OpenSearch. Sibling project to `qdrant-search` (semantic-only, with
PostgreSQL as source of truth) — built to compare the two approaches.

## Architecture

**PostgreSQL is the intended source of truth for document metadata; OpenSearch
is the derived, rebuildable search index.** Every chunk becomes one OpenSearch
document that carries, side by side:

- `text` — analyzed → **BM25 lexical** search + highlighting
- `embedding` — `knn_vector` (HNSW, cosine) → **semantic** search
- document metadata mirrored from Postgres (`document_id`, `version_number`,
  `aktenzeichen`, `verfahren_id`, `klassifizierung`, `mime_type`, `created_at`)
  → filtering

The relational metadata schema — `documents`, `document_versions` (append-only,
holds the `body_text`), `search_queries`, `query_notifications`, plus placeholder
`users`/`verfahren` — lives in SQLAlchemy models (`app/models.py`) and is managed
with Alembic migrations. Ingestion writes a `Document` and its first
`DocumentVersion` to Postgres, then derives the OpenSearch chunks; a chunk's
`_id` is `"{document_id}-v{version_number}-{chunk_index}"`. Deduplication is by
`content_hash`. Highlighting is done natively by OpenSearch (`<em>` fragments).

> **Status:** PostgreSQL is the source of truth end to end — ingestion, search,
> fetch and (soft-)delete all go through it, and the OpenSearch index is derived
> and can be rebuilt from Postgres with `python -m app.reindex` (see
> [Rebuilding the index](#rebuilding-the-index-opensearch-is-derived)).
> `search_queries` / `query_notifications` are schema-only so far (query logging
> and duplicate notifications are a later feature).

### Search modes

| mode       | query                                   | highlighting |
|------------|-----------------------------------------|--------------|
| `lexical`  | BM25 `match` on `text` (+ filters)      | `<em>` fragments |
| `semantic` | kNN over `embedding` (+ filters)        | none (no query terms) |
| `hybrid`   | both, combined by a normalization pipeline | `<em>` on the lexical part |

Hybrid uses an OpenSearch **search pipeline** (`normalization-processor`): each
score list is min-max normalized, then combined as a weighted arithmetic mean
(`HYBRID_LEXICAL_WEIGHT` / `HYBRID_SEMANTIC_WEIGHT`).

## Tech stack

Python 3.13, OpenSearch 2.19 (kNN + hybrid search pipeline), opensearch-py,
PostgreSQL 17 with SQLAlchemy 2.0 + Alembic (metadata store), sentence-transformers
(`intfloat/multilingual-e5-large`), Flask, pydantic-settings. All open source.
Infrastructure (OpenSearch, Dashboards, PostgreSQL, pgAdmin) via Docker Compose.

## Setup

```bash
# 1. Start infrastructure (OpenSearch, Dashboards, PostgreSQL, pgAdmin)
docker compose up -d

# 2. Python environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configuration
cp .env.example .env        # adjust if needed (ports, model, weights, ...)

# 4. Create the metadata schema in PostgreSQL
alembic upgrade head
```

> The security plugin is disabled for the local PoC, so the app talks plain
> HTTP on port `9200` without auth. The first ingest/search downloads the
> embedding model (~2.2 GB) once. **Dashboards** is available at
> http://localhost:5601 to inspect the index and run queries; **pgAdmin** at
> http://localhost:5050 (the Postgres connection is pre-registered).

### Embedding model behind a corporate proxy (mirror, CA, offline)

By default the model is pulled from the public HuggingFace Hub. In a
network-segmented environment, point the loader at an internal mirror and its
CA certificates via `.env` (all optional, see `.env.example`):

```dotenv
HF_ENDPOINT=https://huggingface.internal.example   # internal Hub mirror/proxy
HF_TOKEN=<token>                                    # if the mirror needs auth
HF_HOME=/opt/models/hf-cache                        # where models are cached
# TLS: only needed if the corporate CA is NOT already in the system trust store
CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
```

**CA certificates.** If the organization's root CA is already installed in the
system trust store (e.g. via `update-ca-certificates`), nothing is needed — it
is auto-detected. Otherwise set `CA_BUNDLE` (or the standard `REQUESTS_CA_BUNDLE`
/ `SSL_CERT_FILE` environment variables); the loader applies it to the whole
HTTP stack before contacting the Hub.

**Pre-download once, then run fully offline.** Fetch the model a single time
while the mirror is reachable, then serve it from the local cache with no
network access:

```bash
# 1. One-time download into HF_HOME via the internal mirror (network required).
#    Uses the same settings/CA handling as the app.
HF_HOME=/opt/models/hf-cache \
  python -c "from app.embedding import get_model; get_model()"

# 2. For all later runs, add this to .env so the model is loaded from cache only
#    and the network is never touched (fails fast if something is missing):
#      HF_HOME=/opt/models/hf-cache
#      HF_OFFLINE=true
```

Keep `HF_HOME` pointing at the same directory in both steps — the offline run
resolves the model exclusively from that cache.

## Run the API

```bash
python -m app.api          # serves on http://localhost:5002 (API_PORT)
```

The OpenSearch index and the hybrid search pipeline are created automatically on
startup (idempotent). Interactive API docs (Swagger UI) are served at
**http://localhost:5002/apidocs/**, the raw OpenAPI spec at `/apispec_1.json`.

## Example requests

### Ingest a document

`created_by` (and the optional `verfahren_id`) must reference rows that already
exist in Postgres. `klassifizierung` is a free string for now — later it will be
assigned by an ML classifier using the police taxonomy.

```bash
curl -s -X POST http://localhost:5002/documents \
  -H 'Content-Type: application/json' \
  -d '{
    "aktenzeichen": "AZ-2026-0001",
    "verfahren_id": "111f3f39-54f7-435e-a4bf-47dc088c5e79",
    "klassifizierung": "VS-NfD",
    "s3_object_key": "documents/az-2026-0001/report.txt",
    "created_by": "85709c0d-3a9b-4b72-9bd2-ebf672982868",
    "path": "sample_docs/report_en_2024.txt"
  }'
```

Pass `"content": "..."` instead of `"path"` to ingest raw text. Response
(`201` created, `200` if deduplicated by content hash):

```json
{"document_id": "0bbafd99-...", "version_number": 1, "aktenzeichen": "AZ-2026-0001", "num_chunks": 1, "deduplicated": false}
```

### Search (mode + optional filters)

```bash
curl -s -X POST http://localhost:5002/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "phishing attempts",
    "mode": "lexical",
    "limit": 5,
    "filters": {"aktenzeichen": "AZ-2026-0001", "klassifizierung": "VS-NfD"}
  }'
```

`mode` is one of `lexical` | `semantic` | `hybrid` (default `hybrid`); the
filters (`aktenzeichen`, `verfahren_id`, `klassifizierung`, `created_from` /
`created_to`) are all optional. Response:

```json
{
  "query": "phishing attempts",
  "mode": "lexical",
  "count": 1,
  "results": [
    {
      "score": 0.58,
      "document_id": "0bbafd99-...",
      "version_number": 1,
      "chunk_index": 0,
      "chunk_text": "The quarterly security report ...",
      "highlights": ["<em>Phishing</em> <em>attempts</em> increased by 18 percent ..."],
      "document": {"id": "0bbafd99-...", "aktenzeichen": "AZ-2026-0001", "klassifizierung": "VS-NfD", "...": "..."}
    }
  ]
}
```

`highlights` are OpenSearch-native `<em>`-wrapped fragments — render them
directly to show the matching terms. Semantic hits have no query terms, so their
`highlights` list is usually empty (the full `chunk_text` is always returned).

### Fetch / delete a document

```bash
curl -s http://localhost:5002/documents/<document_id>          # Postgres metadata + ordered chunks
curl -s -X DELETE http://localhost:5002/documents/<document_id>  # soft-delete + drop chunks from OpenSearch
```

`DELETE` sets `deleted_at` in Postgres (the row is kept for auditing) and removes
the document's chunks from OpenSearch, so it disappears from search while
remaining on record.

## Rebuilding the index (OpenSearch is derived)

OpenSearch holds no data that cannot be reconstructed — Postgres is the source of
truth. Reindex from Postgres with:

```bash
python -m app.reindex                   # full rebuild (drops + recreates the index)
python -m app.reindex --document <uuid>  # just one document's current version
python -m app.reindex --verfahren <uuid> # all live documents of a verfahren
```

The **full rebuild** `rebuild_index()` (in `app/reindex.py`):

1. **drops and recreates** the `chunks` index with the current mapping
   (`recreate_index()`) and re-puts the hybrid search pipeline;
2. iterates every **live** document (`deleted_at IS NULL`);
3. loads each document's **current version** (`document_versions` at
   `documents.current_version`) and re-chunks → re-embeds → re-indexes its
   `body_text`.

Embeddings dominate the cost, so chunks are embedded in batches across documents
and indexed with `refresh=False`, refreshing the index once at the end. A full
rebuild is meant to be the **exception** — after a mapping/analyzer change, after
changing `CHUNK_SIZE` / `CHUNK_OVERLAP` / `EMBEDDING_MODEL`, or to recover from
OpenSearch data loss. Day to day, use the **partial** `--document` / `--verfahren`
reindex (it drops just that document's chunks and re-indexes its current version
over the live index), so the expensive full rebuild stays rare.

### Versioning

The rebuild is *not* a versioning mechanism; it only respects one. Each document
has an append-only history in `document_versions` and a `current_version`
pointer. Every OpenSearch chunk carries its `version_number`, and a chunk's `_id`
is `"{document_id}-v{version_number}-{chunk_index}"`, so versions never collide.
The rebuild always indexes **only the current version** of each live document.

> **Not implemented yet:** creating a *new* version (v2, v3) of an existing
> document. Ingesting identical content is deduplicated by `content_hash`; any
> other ingest creates a **new** document at version 1. The full `version_number`
> plumbing (Postgres column, OpenSearch field, `_id` scheme) is already in place,
> so adding version increments later needs no schema or index change.

## Comparing with qdrant-search

Both projects share the same chunker and embedding model, so results are
comparable. Key differences:

| | qdrant-search | opensearch-search |
|---|---|---|
| stores | Postgres (truth) + Qdrant (vectors) | Postgres (metadata truth) + OpenSearch (search index) |
| search | semantic only | lexical + semantic + hybrid |
| highlighting | char offsets into stored body | native `<em>` fragments |
| filters | Qdrant payload filter | OpenSearch keyword/date filter |

Build a small labeled `(query, relevant_doc_ids)` set and compute **recall@k** /
MRR per mode to quantify the trade-offs; tune `CHUNK_SIZE`, `CHUNK_OVERLAP` and
the hybrid weights.

## Scope

Deliberately excluded (PoC): authentication, frontend, reranking, full
extraction pipeline (only `.txt` and simple `.pdf` text), TLS/security plugin,
and embedding models inside the cluster (embeddings are computed app-side).
