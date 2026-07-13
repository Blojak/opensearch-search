# opensearch-search — lexical + semantic document search (PoC)

*(Deutsche Fassung: [README.de.md](README.de.md))*

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
  `aktenzeichen`, `verfahren_id`, `klassifizierung`, `language`, `mime_type`,
  `created_at`) → filtering

The relational metadata schema — `documents`, `document_versions` (append-only,
holds the `body_text`), `search_queries`, `query_notifications`, `users` (a local
projection of the IdP identity) and a placeholder `verfahren` — lives in
SQLAlchemy models (`app/models.py`) and is managed with Alembic migrations.
Ingestion writes a `Document` and its first
`DocumentVersion` to Postgres, then derives the OpenSearch chunks; a chunk's
`_id` is `"{document_id}-v{version_number}-{chunk_index}"`. Deduplication is by
`content_hash`. Highlighting is done natively by OpenSearch (`<em>` fragments).

> **Status:** PostgreSQL is the source of truth end to end — ingestion, search,
> fetch and (soft-)delete all go through it, and the OpenSearch index is derived
> and can be rebuilt from Postgres with `python -m app.reindex` (see
> [Rebuilding the index](#rebuilding-the-index-opensearch-is-derived)). Searches
> are logged and duplicates across users are detected; notification **delivery**
> (email) is still open, as is creating a *new version* of an existing document.

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
(`intfloat/multilingual-e5-large`), Keycloak + Authlib (OIDC), Flask,
pydantic-settings. All open source. Infrastructure (OpenSearch, Dashboards,
PostgreSQL, pgAdmin, Keycloak) via Docker Compose.

## Setup

```bash
# 1. Start infrastructure (OpenSearch, Dashboards, PostgreSQL, pgAdmin, Keycloak)
docker compose up -d

# 2. Python environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime + tests
# (the container image installs only requirements.txt)

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

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Test dependencies live in `requirements-dev.txt`, not `requirements.txt` — the
runtime image installs only the latter, so pytest never ends up in a production
container.

The suite covers the pure logic — no Postgres, OpenSearch or Keycloak needed, so
it runs in well under a second. It deliberately pins the *decisions* rather than
the implementation: that a chunk's offsets slice back to its exact text (the UI
highlights hits with them), that the query fingerprint ignores case and
whitespace but **not** word order, that a passage's `hit_start`/`hit_end` are
offsets into the returned window, and that undetectable text becomes `unknown`
instead of raising.

## Authentication

Every endpoint except `/health` requires an OIDC bearer token. The API is a
**resource server**: it never issues tokens, it only validates the ones the IdP
signs (RS256, keys from the JWKS endpoint, `iss` and `aud` checked). Keycloak
runs in Docker Compose as the local IdP, with the realm, the `osearch-api`
client and a test user imported from `keycloak/realm-osearch.json`.

Get a token (the direct grant is enabled for local development, so no UI is
needed):

```bash
TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/osearch/protocol/openid-connect/token \
  -d client_id=osearch-api -d grant_type=password \
  -d username=ermittler -d password=ermittler | jq -r .access_token)

curl -s http://localhost:5002/search -H "Authorization: Bearer $TOKEN" ...
```

In the Swagger UI, use the **Authorize** button and paste `Bearer <token>`.

**`created_by` is never sent by the client** — it is derived from the token, so a
caller cannot attribute a document to somebody else. On the first authenticated
request the user is projected into the local `users` table just-in-time, keyed by
`(issuer, subject)` from the token; `email` and `orgeinheit` are refreshed from
the claims on every request. The internal `users.id` stays a separate UUID from
the IdP's `sub`, so the foreign keys survive an IdP migration.

> **OIDC vs SAML.** The app speaks OIDC only. If the corporate IdP later turns
> out to speak SAML, Keycloak brokers it upstream and still issues OIDC tokens
> downstream — no application change.

`OIDC_ISSUER` and `OIDC_AUDIENCE` are **required** settings with no defaults in
code: a missing value (e.g. a misspelled key in a Kubernetes ConfigMap) fails at
startup instead of silently trusting the wrong issuer.

## Example requests

### Ingest a document

The optional `verfahren_id` must reference a row that already exists in Postgres.
`verfahren` is owned by another bounded context and has no API, so seed the fixed
dev one (idempotent). Users need no seeding — they are created just-in-time from
the token:

```bash
python scripts/seed_dev.py
```

`klassifizierung` is a free string for now — later it will be assigned by an ML
classifier using the police taxonomy.

`language` is **auto-detected** from the content at ingest (offline, via
`langdetect`, mapped onto `de` / `en` / `fr` / `es` / `it`, falling back to
`unknown`). Pass `"language": "de"` explicitly to override the detection.

```bash
curl -s -X POST http://localhost:5002/documents \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "aktenzeichen": "AZ-2026-0001",
    "verfahren_id": "111f3f39-54f7-435e-a4bf-47dc088c5e79",
    "klassifizierung": "VS-NfD",
    "s3_object_key": "documents/az-2026-0001/report.txt",
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
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "phishing attempts",
    "mode": "lexical",
    "limit": 5,
    "filters": {"aktenzeichen": "AZ-2026-0001", "klassifizierung": "VS-NfD"}
  }'
```

`mode` is one of `lexical` | `semantic` | `hybrid` (default `hybrid`); the
filters (`aktenzeichen`, `verfahren_id`, `klassifizierung`, `language`,
`created_from` / `created_to`) are all optional. Response:

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
curl -s http://localhost:5002/documents/<document_id> \
  -H "Authorization: Bearer $TOKEN"          # Postgres metadata + ordered chunks

curl -s -X DELETE http://localhost:5002/documents/<document_id> \
  -H "Authorization: Bearer $TOKEN"          # soft-delete + drop chunks from OpenSearch
```

`DELETE` sets `deleted_at` in Postgres (the row is kept for auditing) and removes
the document's chunks from OpenSearch, so it disappears from search while
remaining on record.

## Query logging

Every search is recorded in `search_queries`: who searched, the verbatim query,
a fingerprint of the normalized query, the applied filters (JSONB) and the number
of hits. This is the data basis for later spotting that two people research the
same thing (`query_notifications`).

Two deliberate decisions:

- **The hash covers the query text only, not the filters.** The filters are
  stored beside it, so the matching rule can be tightened later ("same query
  *and* same verfahren") without a migration. The reverse would not work — what
  was never hashed cannot be recovered.
- **Normalization is conservative**: lowercase, trim, collapse whitespace; word
  order is preserved. So `"  SECURITY   Report "` and `"security report"` share a
  hash, but `"Hauptstrasse Einbruch"` and `"Einbruch Hauptstrasse"` do not.
  Sorting tokens would blur the line between *identical* and *similar* and
  destroy the ability to measure how often exact repeats actually occur — which
  is exactly what decides whether semantic similarity is needed at all.

Recording is telemetry and **can never break a search**: a failure to write the
row is logged and swallowed, the search still returns its results.

### Duplicate detection

When a search matches one that **somebody else** already ran (same
`query_hash`), both sides get an entry in `query_notifications` — so duplicate
investigative work surfaces instead of staying invisible. Read your own with:

```bash
curl -s http://localhost:5002/notifications -H "Authorization: Bearer $TOKEN"
```

Each entry names the `counterpart`: who else is researching this, and in which
`orgeinheit`. That is the whole point — knowing *whom* to talk to.

Rules, deliberately:

- **You never match yourself.** Repeating your own search is not a duplicate.
- **A pair is reported once.** However often either side searches again, no
  second notification is created for the same query and the same pair.
- **No visibility gate yet.** Any two users match, regardless of orgeinheit.
  *Who may be told about whom is a legal/organizational question and must be
  settled before this goes anywhere near production.*
- **Nothing is delivered.** Entries stay in `status = 'pending'`; email delivery
  is a separate, later step.

Like the logging, detection can never break a search.

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

Reach for the partial reindex whenever metadata changed **in Postgres** — a
corrected `klassifizierung`, a reassigned `verfahren_id`, a backfilled
`language`. The chunks carry a denormalized copy of those fields, so they stay
stale until the document is re-derived.

> **Reindex derives, it does not recompute.** It mirrors `documents.language` /
> `documents.klassifizierung` exactly as stored in Postgres; language detection
> only runs at ingest. Always fix Postgres **first**, then reindex.

### Backfilling the language of older documents

Documents ingested before the `language` field existed keep the `'unknown'`
default — and a reindex will not fix that (see the note above). This detects the
language from the current version's body text, writes it to Postgres and then
reindexes exactly those documents (idempotent):

```bash
python scripts/backfill_language.py --dry-run   # report only, write nothing
python scripts/backfill_language.py             # write + reindex
```

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

Deliberately excluded (PoC): frontend, reranking, a full extraction pipeline
(only `.txt` and simple `.pdf` text), TLS/security plugin, and embedding models
inside the cluster (embeddings are computed app-side). Authentication **is**
implemented (OIDC); authorization is not — every valid token may do everything,
roles/scopes are a later step.
