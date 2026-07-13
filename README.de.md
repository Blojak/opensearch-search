# opensearch-search — lexikalische + semantische Dokumentensuche (PoC)

*(English version: [README.md](README.md))*

Proof of Concept für **lexikalische (BM25), semantische (kNN) und hybride** Suche
über eine Dokumentensammlung, mit **nativem Highlighting** der Fundstellen.
Dokumente werden in Chunks zerlegt, lokal mit einem multilingualen Modell
eingebettet und in OpenSearch indexiert. Schwesterprojekt zu `qdrant-search`
(rein semantisch, ebenfalls mit PostgreSQL als Source of Truth) — gebaut, um die
beiden Ansätze zu vergleichen.

## Architektur

**PostgreSQL ist die Source of Truth für die Dokument-Metadaten; OpenSearch ist
der abgeleitete, jederzeit neu aufbaubare Suchindex.** Jeder Chunk wird zu einem
OpenSearch-Dokument, das nebeneinander trägt:

- `text` — analysiert → **lexikalische BM25-Suche** + Highlighting
- `embedding` — `knn_vector` (HNSW, Cosinus) → **semantische Suche**
- aus Postgres gespiegelte Metadaten (`document_id`, `version_number`,
  `aktenzeichen`, `verfahren_id`, `klassifizierung`, `language`, `mime_type`,
  `created_at`) → Filterung

Das relationale Schema — `documents`, `document_versions` (append-only, enthält
den `body_text`), `search_queries`, `query_notifications`, `users` (lokale
Projektion der IdP-Identität) und ein Platzhalter-`verfahren` — liegt in
SQLAlchemy-Models (`app/models.py`) und wird per Alembic migriert. Der Ingest
schreibt ein `Document` und dessen erste `DocumentVersion` nach Postgres und
leitet daraus die OpenSearch-Chunks ab; die `_id` eines Chunks ist
`"{document_id}-v{version_number}-{chunk_index}"`. Deduplizierung erfolgt über
den `content_hash`. Das Highlighting macht OpenSearch nativ (`<em>`-Fragmente).

> **Stand:** PostgreSQL ist durchgängig die Wahrheit — Ingest, Suche, Abruf und
> (Soft-)Delete laufen darüber, und der OpenSearch-Index ist abgeleitet und mit
> `python -m app.reindex` aus Postgres neu aufbaubar (siehe
> [Index neu aufbauen](#index-neu-aufbauen-opensearch-ist-abgeleitet)). Suchen
> werden protokolliert und nutzerübergreifende Duplikate erkannt; offen sind noch
> die **Zustellung** der Benachrichtigungen (E-Mail) sowie das Anlegen einer
> *neuen Version* eines bestehenden Dokuments.

### Suchmodi

| Modus      | Query                                        | Highlighting |
|------------|----------------------------------------------|--------------|
| `lexical`  | BM25-`match` auf `text` (+ Filter)           | `<em>`-Fragmente |
| `semantic` | kNN über `embedding` (+ Filter)              | keins (keine Suchbegriffe) |
| `hybrid`   | beides, kombiniert per Normalisierungs-Pipeline | `<em>` auf dem lexikalischen Teil |

Hybrid nutzt eine OpenSearch-**Search-Pipeline** (`normalization-processor`):
Jede Score-Liste wird min-max-normalisiert und dann als gewichtetes
arithmetisches Mittel kombiniert (`HYBRID_LEXICAL_WEIGHT` /
`HYBRID_SEMANTIC_WEIGHT`).

## Tech-Stack

Python 3.13, OpenSearch 2.19 (kNN + hybride Search-Pipeline), opensearch-py,
PostgreSQL 17 mit SQLAlchemy 2.0 + Alembic (Metadatenspeicher),
sentence-transformers (`intfloat/multilingual-e5-large`), Keycloak + Authlib
(OIDC), Flask, pydantic-settings. Alles Open Source. Infrastruktur (OpenSearch,
Dashboards, PostgreSQL, pgAdmin, Keycloak) via Docker Compose.

## Setup

```bash
# 1. Infrastruktur starten (OpenSearch, Dashboards, PostgreSQL, pgAdmin, Keycloak)
docker compose up -d

# 2. Python-Umgebung
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # Laufzeit + Tests
# (das Container-Image installiert nur requirements.txt)

# 3. Konfiguration
cp .env.example .env        # bei Bedarf anpassen (Ports, Modell, Gewichte, ...)

# 4. Metadaten-Schema in PostgreSQL anlegen
alembic upgrade head
```

> Das Security-Plugin ist für den lokalen PoC deaktiviert, die App spricht also
> auf Port `9200` unverschlüsseltes HTTP ohne Auth. Der erste Ingest bzw. die
> erste Suche lädt einmalig das Embedding-Modell (~2,2 GB). **Dashboards** liegt
> auf http://localhost:5601 (Index inspizieren, Queries fahren), **pgAdmin** auf
> http://localhost:5050 (die Postgres-Verbindung ist vorregistriert).

### Embedding-Modell hinter einem Firmen-Proxy (Mirror, CA, offline)

Standardmäßig wird das Modell vom öffentlichen HuggingFace Hub geladen. In einer
netzsegmentierten Umgebung zeigst du den Loader per `.env` auf einen internen
Mirror und dessen CA-Zertifikate (alles optional, siehe `.env.example`):

```dotenv
HF_ENDPOINT=https://huggingface.internal.example   # interner Hub-Mirror/Proxy
HF_TOKEN=<token>                                    # falls der Mirror Auth verlangt
HF_HOME=/opt/models/hf-cache                        # wohin Modelle gecacht werden
# TLS: nur nötig, wenn die Firmen-CA NICHT schon im System-Trust-Store liegt
CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
```

**CA-Zertifikate.** Liegt die Root-CA der Organisation bereits im System-Trust-
Store (z. B. via `update-ca-certificates`), ist **nichts** zu tun — sie wird
automatisch erkannt. Andernfalls `CA_BUNDLE` setzen (oder die Standard-Variablen
`REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`); der Loader legt sie auf den gesamten
HTTP-Stack, **bevor** der Hub kontaktiert wird.

**Einmal vorab ziehen, danach komplett offline.** Modell einmalig laden, solange
der Mirror erreichbar ist, danach ausschließlich aus dem lokalen Cache:

```bash
# 1. Einmaliger Download nach HF_HOME über den internen Mirror (Netz nötig).
#    Nutzt dieselbe Settings-/CA-Logik wie die App.
HF_HOME=/opt/models/hf-cache \
  python -c "from app.embedding import get_model; get_model()"

# 2. Für alle weiteren Läufe in die .env, damit das Modell nur aus dem Cache
#    kommt und das Netz nie angefasst wird (schlägt sonst sofort fehl):
#      HF_HOME=/opt/models/hf-cache
#      HF_OFFLINE=true
```

`HF_HOME` muss in beiden Schritten auf dasselbe Verzeichnis zeigen — der
Offline-Lauf löst das Modell ausschließlich aus diesem Cache auf.

## API starten

```bash
python -m app.api          # lauscht auf http://localhost:5002 (API_PORT)
```

Der OpenSearch-Index und die hybride Search-Pipeline werden beim Start
automatisch angelegt (idempotent). Die interaktive API-Doku (Swagger UI) liegt
auf **http://localhost:5002/apidocs/**, die rohe OpenAPI-Spec auf
`/apispec_1.json`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Die Test-Abhängigkeiten liegen in `requirements-dev.txt`, **nicht** in
`requirements.txt` — das Laufzeit-Image installiert nur letztere, damit pytest
nie in einem Produktiv-Container landet.

Die Suite deckt die reine Logik ab — **ohne** Postgres, OpenSearch oder Keycloak,
sie läuft daher in deutlich unter einer Sekunde. Sie nagelt bewusst die
**Entscheidungen** fest, nicht die Implementierung: dass die Offsets eines Chunks
exakt auf seinen Text zurückschneiden (die UI hebt Treffer damit hervor), dass
der Query-Fingerabdruck Groß-/Kleinschreibung und Leerzeichen ignoriert, die
**Wortreihenfolge aber nicht**, dass `hit_start`/`hit_end` einer Passage Offsets
**ins zurückgegebene Fenster** sind (nicht ins Dokument), und dass nicht
erkennbarer Text zu `unknown` wird, statt zu werfen.

## Authentifizierung

Jeder Endpunkt außer `/health` verlangt ein OIDC-Bearer-Token. Die API ist ein
**Resource Server**: Sie stellt **keine** Tokens aus, sie validiert nur die, die
der IdP signiert (RS256, Schlüssel vom JWKS-Endpoint, `iss` und `aud` werden
geprüft). Keycloak läuft als lokaler IdP in Docker Compose; Realm, der Client
`osearch-api` und die Testnutzer werden aus `keycloak/realm-osearch.json`
importiert.

Token holen (der Direct Grant ist für die lokale Entwicklung aktiviert, es wird
also keine UI gebraucht):

```bash
TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/osearch/protocol/openid-connect/token \
  -d client_id=osearch-api -d grant_type=password \
  -d username=ermittler -d password=ermittler | jq -r .access_token)

curl -s http://localhost:5002/search -H "Authorization: Bearer $TOKEN" ...
```

Testnutzer: `ermittler` / `ermittler` (Orgeinheit K3) und `ermittler2` /
`ermittler2` (K5). Der zweite wird gebraucht, um die Duplikat-Erkennung zu
testen — die greift ja erst, wenn **zwei verschiedene** Personen dasselbe suchen.

In der Swagger UI den **Authorize**-Button nutzen und `Bearer <token>` einfügen.

**`created_by` wird nie vom Client geschickt** — es wird aus dem Token
abgeleitet. Ein Aufrufer kann ein Dokument also nicht jemand anderem zuschreiben.
Beim ersten authentifizierten Request wird der Nutzer just-in-time in die lokale
`users`-Tabelle projiziert, Schlüssel ist `(issuer, subject)` aus dem Token;
`email` und `orgeinheit` werden bei jedem Request aus den Claims aufgefrischt.
Die interne `users.id` bleibt bewusst eine **eigene** UUID, getrennt vom `sub`
des IdP — dadurch überleben die Fremdschlüssel einen IdP-Wechsel.

> **OIDC vs. SAML.** Die App spricht ausschließlich OIDC. Falls der Unternehmens-
> IdP später SAML spricht, brokert Keycloak das nach oben und stellt nach unten
> weiterhin OIDC-Tokens aus — **ohne Änderung an der Anwendung**.

`OIDC_ISSUER` und `OIDC_AUDIENCE` sind **Pflicht-Settings ohne Default im Code**:
Ein fehlender Wert (z. B. ein vertippter Key in einer Kubernetes-ConfigMap) lässt
die App beim Start **laut scheitern**, statt still dem falschen Issuer zu
vertrauen.

## Beispiel-Requests

### Dokument einliefern

Die optionale `verfahren_id` muss auf eine bereits existierende Zeile in Postgres
zeigen. `verfahren` gehört einem anderen Bounded Context und hat keine eigene
API — deshalb das feste Dev-Verfahren seeden (idempotent). Nutzer brauchen
**kein** Seeding, sie entstehen just-in-time aus dem Token:

```bash
python scripts/seed_dev.py
```

`klassifizierung` ist vorerst ein **freier String** — später wird das Feld von
einem ML-Klassifikator anhand der Polizei-Taxonomie befüllt.

`language` wird beim Ingest **automatisch erkannt** (offline via `langdetect`,
abgebildet auf `de` / `en` / `fr` / `es` / `it`, sonst `unknown`). Mit
`"language": "de"` lässt sich die Erkennung explizit überschreiben.

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

Statt `"path"` kann `"content": "..."` mitgegeben werden, um Rohtext
einzuliefern. Antwort (`201` bei Neuanlage, `200` wenn per Content-Hash
dedupliziert):

```json
{"document_id": "0bbafd99-...", "version_number": 1, "aktenzeichen": "AZ-2026-0001", "num_chunks": 1, "deduplicated": false}
```

### Suche (Modus + optionale Filter)

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

`mode` ist `lexical` | `semantic` | `hybrid` (Default `hybrid`); die Filter
(`aktenzeichen`, `verfahren_id`, `klassifizierung`, `language`, `created_from` /
`created_to`) sind alle optional. Antwort:

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

`highlights` sind native `<em>`-Fragmente von OpenSearch — direkt renderbar, um
die Fundstellen zu zeigen. Rein semantische Treffer haben keine Suchbegriffe,
ihre `highlights`-Liste ist daher meist leer (der volle `chunk_text` kommt immer
mit).

### Treffer im Kontext anzeigen (für die UI-Detailansicht)

Die Trefferliste kommt mit `chunk_text` + Highlights aus. Für eine
**Detailansicht** braucht die UI den Treffer **im umgebenden Text** — dafür die
Offsets aus dem Suchtreffer weiterreichen:

```bash
curl -s "http://localhost:5002/documents/<document_id>/passage?version=1&start=0&end=512&context=200" \
  -H "Authorization: Bearer $TOKEN"
```

Antwort: `text` ist das Kontextfenster (Treffer + Umgebung), `hit_start` /
`hit_end` sind die Offsets des Treffers **innerhalb von `text`** — damit kann die
UI ihn im Kontext hervorheben. Bewusst ein eigener Endpunkt: ein Kontextfenster
pro Suchtreffer würde je einen zusätzlichen DB-Read kosten, deshalb holt die UI
es **on demand** beim Klick.

### Dokument abrufen / löschen

```bash
curl -s http://localhost:5002/documents/<document_id> \
  -H "Authorization: Bearer $TOKEN"          # Postgres-Metadaten + geordnete Chunks

curl -s -X DELETE http://localhost:5002/documents/<document_id> \
  -H "Authorization: Bearer $TOKEN"          # Soft-Delete + Chunks aus OpenSearch entfernen
```

`DELETE` setzt `deleted_at` in Postgres (die Zeile bleibt revisionssicher
erhalten) und entfernt die Chunks aus OpenSearch — das Dokument verschwindet also
aus der Suche, bleibt aber aktenkundig.

## Query-Logging

Jede Suche wird in `search_queries` protokolliert: wer gesucht hat, der Query im
Wortlaut, ein Fingerabdruck der normalisierten Anfrage, die gesetzten Filter
(JSONB) und die Trefferzahl. Das ist die Datengrundlage dafür, zu erkennen, dass
zwei Personen dasselbe recherchieren (`query_notifications`).

Zwei bewusste Entscheidungen:

- **Der Hash umfasst nur den Query-Text, nicht die Filter.** Die Filter liegen
  daneben, sodass sich die Matching-Regel später verschärfen lässt („gleiche
  Anfrage *und* gleiches Verfahren") — **ohne Migration**. Umgekehrt ginge das
  nicht: Was nie gehasht wurde, lässt sich nicht rekonstruieren.
- **Die Normalisierung ist bewusst konservativ**: lowercase, trimmen,
  Mehrfach-Leerzeichen zusammenfassen; die **Wortreihenfolge bleibt erhalten**.
  `"  SECURITY   Report "` und `"security report"` teilen sich also einen Hash,
  `"Hauptstrasse Einbruch"` und `"Einbruch Hauptstrasse"` **nicht**. Tokens zu
  sortieren würde die Grenze zwischen *identisch* und *ähnlich* verwischen und
  die Möglichkeit zerstören, zu **messen**, wie oft exakte Wiederholungen
  überhaupt vorkommen — und genau das entscheidet, ob semantische Ähnlichkeit
  später gebraucht wird.

Das Protokollieren ist Telemetrie und **darf eine Suche niemals kaputtmachen**:
Schlägt das Schreiben fehl, wird es geloggt und geschluckt, die Suche liefert
trotzdem ihre Ergebnisse.

### Duplikat-Erkennung

Wenn eine Suche eine Anfrage wiederholt, die **jemand anderes** schon gestellt
hat (gleicher `query_hash`), bekommen **beide Seiten** einen Eintrag in
`query_notifications` — damit doppelte Ermittlungsarbeit sichtbar wird, statt
unbemerkt zu bleiben. Eigene Einträge abrufen:

```bash
curl -s http://localhost:5002/notifications -H "Authorization: Bearer $TOKEN"
```

Jeder Eintrag nennt den `counterpart`: **wer** sonst noch daran arbeitet und in
welcher `orgeinheit`. Genau darum geht es — zu wissen, **mit wem** man reden
sollte.

Die Regeln, bewusst gesetzt:

- **Man matcht nie mit sich selbst.** Die eigene Suche zu wiederholen ist kein
  Duplikat.
- **Ein Paar wird nur einmal gemeldet.** Egal wie oft eine der beiden Seiten
  erneut sucht — für dieselbe Anfrage und dasselbe Nutzerpaar entsteht keine
  zweite Benachrichtigung (sonst würde jede Wiederholung beide zuspammen).
- **Noch keine Sichtbarkeits-Schranke.** Aktuell matcht **jeder mit jedem**,
  unabhängig von der Orgeinheit. *Wer über wen informiert werden darf, ist eine
  fachlich-rechtliche Frage und muss geklärt sein, bevor das in die Nähe eines
  Produktivbetriebs kommt* — bei verdeckten Ermittlungen kann genau diese
  Offenlegung schaden.
- **Es wird nichts zugestellt.** Die Einträge bleiben auf `status = 'pending'`;
  der E-Mail-Versand ist ein eigener, späterer Schritt.

Wie beim Logging gilt: Die Erkennung kann eine Suche nie kaputtmachen.

## Index neu aufbauen (OpenSearch ist abgeleitet)

In OpenSearch liegen keine Daten, die sich nicht rekonstruieren ließen — Postgres
ist die Wahrheit. Neu indexieren aus Postgres:

```bash
python -m app.reindex                     # Full-Rebuild (droppt + legt den Index neu an)
python -m app.reindex --document <uuid>   # nur die aktuelle Version eines Dokuments
python -m app.reindex --verfahren <uuid>  # alle lebenden Dokumente eines Verfahrens
```

Der **Full-Rebuild** `rebuild_index()` (in `app/reindex.py`):

1. **droppt und erstellt** den `chunks`-Index mit dem aktuellen Mapping neu
   (`recreate_index()`) und legt die hybride Search-Pipeline erneut an;
2. iteriert über alle **lebenden** Dokumente (`deleted_at IS NULL`);
3. lädt je Dokument die **aktuelle Version** (`document_versions` bei
   `documents.current_version`) und chunkt → embedded → indexiert deren
   `body_text` neu.

Die Embeddings dominieren die Kosten, deshalb werden Chunks **dokumentübergreifend
gebatcht** eingebettet und mit `refresh=False` indexiert; der Index wird erst am
Ende einmal refresht. Ein Full-Rebuild soll die **Ausnahme** sein — nach einer
Mapping-/Analyzer-Änderung, nach geändertem `CHUNK_SIZE` / `CHUNK_OVERLAP` /
`EMBEDDING_MODEL`, oder zur Wiederherstellung nach OpenSearch-Datenverlust. Im
Alltag den **partiellen** Reindex (`--document` / `--verfahren`) nutzen — er wirft
nur die Chunks dieses einen Dokuments weg und indexiert dessen aktuelle Version
über den laufenden Index.

Zum partiellen Reindex greifst du immer dann, wenn sich Metadaten **in Postgres**
geändert haben — eine korrigierte `klassifizierung`, ein neu zugeordnetes
`verfahren_id`, eine nachgetragene `language`. Die Chunks tragen eine
denormalisierte Kopie dieser Felder und sind daher **veraltet**, bis das Dokument
neu abgeleitet wird.

> **Reindex leitet ab, er berechnet nicht neu.** Er spiegelt
> `documents.language` / `documents.klassifizierung` exakt so, wie sie in Postgres
> stehen; die Spracherkennung läuft **nur beim Ingest**. Also immer **erst**
> Postgres korrigieren, **dann** reindexieren.

### Sprache von Altbestand nachtragen

Dokumente, die vor Einführung des `language`-Felds eingeliefert wurden, stehen
auf dem Default `'unknown'` — und ein Reindex behebt das **nicht** (siehe Hinweis
oben). Dieses Skript erkennt die Sprache aus dem `body_text` der aktuellen
Version, schreibt sie nach Postgres und reindexiert **erst danach** genau diese
Dokumente (idempotent):

```bash
python scripts/backfill_language.py --dry-run   # nur berichten, nichts schreiben
python scripts/backfill_language.py             # schreiben + reindexieren
```

### Versionierung

Der Rebuild ist *kein* Versionierungs-Mechanismus; er **respektiert** nur einen.
Jedes Dokument hat eine append-only-Historie in `document_versions` und einen
`current_version`-Zeiger. Jeder OpenSearch-Chunk trägt seine `version_number`,
und die `_id` lautet `"{document_id}-v{version_number}-{chunk_index}"` — Versionen
kollidieren also nie. Der Rebuild indexiert immer **nur die aktuelle Version**
jedes lebenden Dokuments.

> **Noch nicht implementiert:** das Anlegen einer *neuen* Version (v2, v3) eines
> bestehenden Dokuments. Identischer Inhalt wird per `content_hash` dedupliziert;
> jeder andere Ingest legt ein **neues** Dokument mit Version 1 an. Die komplette
> `version_number`-Verrohrung (Postgres-Spalte, OpenSearch-Feld, `_id`-Schema)
> steht bereits — spätere Versions-Inkremente brauchen also **weder** Schema-
> **noch** Index-Änderung.

## Vergleich mit qdrant-search

Beide Projekte teilen denselben Chunker und dasselbe Embedding-Modell, die
Ergebnisse sind also vergleichbar. Die wesentlichen Unterschiede:

| | qdrant-search | opensearch-search |
|---|---|---|
| Speicher | Postgres (Wahrheit) + Qdrant (Vektoren) | Postgres (Metadaten-Wahrheit) + OpenSearch (Suchindex) |
| Suche | nur semantisch | lexikalisch + semantisch + hybrid |
| Highlighting | Zeichen-Offsets in den gespeicherten Body | native `<em>`-Fragmente |
| Filter | Qdrant-Payload-Filter | OpenSearch Keyword-/Datums-Filter |

Um die Trade-offs zu quantifizieren: ein kleines gelabeltes Set
`(query, relevante_doc_ids)` aufbauen und pro Modus **recall@k** / MRR rechnen;
danach `CHUNK_SIZE`, `CHUNK_OVERLAP` und die Hybrid-Gewichte tunen.

## Abgrenzung

Bewusst ausgeklammert (PoC): Frontend, Reranking, eine vollständige
Extraktions-Pipeline (nur `.txt` und einfacher `.pdf`-Text), TLS/Security-Plugin
sowie Embedding-Modelle im Cluster (die Embeddings werden app-seitig berechnet).

Die **Authentifizierung ist** implementiert (OIDC); die **Autorisierung nicht** —
jedes gültige Token darf alles, Rollen/Scopes sind ein späterer Schritt.
