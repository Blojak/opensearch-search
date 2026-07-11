# opensearch-search — lexical + semantic document search (PoC)

Proof of concept for **lexical (BM25), semantic (kNN) and hybrid** search over a
document collection, with **native highlighting** of the matching terms.
Documents are split into chunks, embedded locally with a multilingual model, and
indexed in OpenSearch. Sibling project to `qdrant-search` (semantic-only, with
PostgreSQL as source of truth) — built to compare the two approaches.

## Architecture

**OpenSearch is the single store.** Every chunk becomes one OpenSearch document
that carries, side by side:

- `text` — analyzed → **BM25 lexical** search + highlighting
- `embedding` — `knn_vector` (HNSW, cosine) → **semantic** search
- denormalized document metadata (`doc_id`, `filename`, `title`, `language`,
  `doc_type`, `classification`, `created_at`, `source`, `extra`, …) → filtering

No separate metadata database: unlike the Qdrant sibling, there is no Postgres,
no Alembic and no stored full-text body. The sha256 content hash is the
`doc_id`; each chunk's `_id` is `"{doc_id}-{chunk_index}"`, so re-ingesting is
idempotent. Highlighting is done natively by OpenSearch (`<em>` fragments), so
no character offsets are needed.

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
sentence-transformers (`intfloat/multilingual-e5-large`), Flask,
pydantic-settings. All open source. Infrastructure via Docker Compose.

## Setup

```bash
# 1. Start infrastructure (OpenSearch + OpenSearch Dashboards)
docker compose up -d

# 2. Python environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configuration
cp .env.example .env        # adjust if needed (ports, model, weights, ...)
```

> The security plugin is disabled for the local PoC, so the app talks plain
> HTTP on port `9200` without auth. The first ingest/search downloads the
> embedding model (~2.2 GB) once. **Dashboards** is available at
> http://localhost:5601 to inspect the index and run queries.

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
startup (idempotent).

## Example requests

### Ingest a document

```bash
curl -s -X POST http://localhost:5002/documents \
  -H 'Content-Type: application/json' \
  -d '{
    "filename": "report_en_2024.txt",
    "title": "Q1 2024 Security Report",
    "language": "en",
    "doc_type": "report",
    "classification": "internal",
    "created_at": "2024-03-31T00:00:00+00:00",
    "path": "sample_docs/report_en_2024.txt"
  }'
```

Pass `"content": "..."` instead of `"path"` to ingest raw text. Response
(`201` created, `200` if deduplicated by content hash):

```json
{"document_id": "9263...", "filename": "report_en_2024.txt", "num_chunks": 1, "deduplicated": false}
```

### Search (mode + optional filters)

```bash
curl -s -X POST http://localhost:5002/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "phishing attempts",
    "mode": "lexical",
    "limit": 5,
    "filters": {"language": "en", "doc_type": "report"}
  }'
```

`mode` is one of `lexical` | `semantic` | `hybrid` (default `hybrid`). Response:

```json
{
  "query": "phishing attempts",
  "mode": "lexical",
  "count": 1,
  "results": [
    {
      "score": 2.47,
      "doc_id": "9263...",
      "chunk_index": 0,
      "chunk_text": "The quarterly security report ...",
      "highlights": ["<em>Phishing</em> <em>attempts</em> increased by 18 percent ..."],
      "document": {"id": "9263...", "filename": "report_en_2024.txt", "language": "en", "doc_type": "report", "...": "..."}
    }
  ]
}
```

`highlights` are OpenSearch-native `<em>`-wrapped fragments — render them
directly to show the matching terms. Semantic hits have no query terms, so their
`highlights` list is usually empty (the full `chunk_text` is always returned).

### Fetch / delete a document

```bash
curl -s http://localhost:5002/documents/<doc_id>          # metadata + ordered chunks
curl -s -X DELETE http://localhost:5002/documents/<doc_id>  # remove all its chunks
```

## Comparing with qdrant-search

Both projects share the same chunker, embedding model and metadata vocabulary,
so results are comparable. Key differences:

| | qdrant-search | opensearch-search |
|---|---|---|
| stores | Postgres (truth) + Qdrant (vectors) | OpenSearch only |
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
