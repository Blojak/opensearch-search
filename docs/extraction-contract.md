# Übergabeformat: extrahierte Dokumente

Schnittstelle zwischen der **Extraktion** (Tika / Docling) und der **Suchanwendung**
(Chunking, Embedding, Indexierung). Sie ist bewusst *kein* Docling- und *kein*
Tika-Format, sondern ein eigenes, schmales Format dazwischen — damit ein Update
oder ein Werkzeugwechsel auf der Extraktionsseite die Suchanwendung nicht bricht.

Arbeitsteilung in einem Satz: **Die Extraktion klassifiziert, die Suchanwendung
entscheidet.** Was ein Block *ist*, weiß die Extraktion. Was davon in den
Suchindex kommt, entscheidet die Anwendung.

## Das Format

```json
{
  "schema_version": 1,
  "source": {
    "tool": "docling",
    "tool_version": "1.10.0",
    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "filename": "Report_Beratung_Modernisierung_RessortA_v1.0.docx",
    "ocr": false
  },
  "blocks": [
    {"type": "heading",   "level": 1, "text": "Ausgangslage",              "locator": {"page": 4}},
    {"type": "paragraph",             "text": "Das Ressort A verfolgt ...",      "locator": {"page": 4}},
    {"type": "list_item",             "text": "IST-Zustand",               "locator": null},
    {"type": "table",                 "text": "| | Thema 1 | Thema 2 |\n|---|---|---|\n| Design | ... |",
                                      "locator": {"page": 12}},
    {"type": "toc",                   "text": "Ausgangslage\t4",           "locator": null}
  ]
}
```

### `blocks`

Die Blöcke stehen **in Lesereihenfolge** — die Reihenfolge im Array *ist* die
Reihenfolge des Dokuments. Die Anwendung fügt die Blocktexte zum kanonischen
Volltext zusammen und rechnet daraus ihre Zeichen-Offsets aus; es werden also
keine Offsets übergeben.

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `type` | ja | `heading`, `paragraph`, `list_item`, `table`, `caption`, `toc`, `header`, `footer` |
| `text` | ja | Der Text des Blocks, nicht leer |
| `level` | nur bei `heading` | Überschriftenebene (1 = oberste) |
| `locator` | ja, darf `null` sein | Wo im Dokument der Block steht — siehe unten |

### `locator`

Der Fundort, damit ein Mensch die Stelle im Originaldokument wiederfindet. Er
sieht je nach Quelle anders aus, und **`null` ist ausdrücklich erlaubt und wird
der Normalfall sein**:

- PDF: `{"page": 3}`
- Excel: `{"sheet": "Zahlungen"}`
- PowerPoint: `{"slide": 7}`
- Word, E-Mail: in aller Regel `null` — Word-Dateien enthalten keine echten
  Seitenzahlen (die entstehen erst beim Rendern), also bitte **keine** erfinden
  und **nicht** die dokumentweite Seitenzahl aus den Metadaten auf jeden Block
  schreiben.

Kurz: Nur füllen, was wirklich in der Datei steht. Lieber `null` als geraten.

## Regeln

1. **Keine Bilddaten.** Im Docling-Beispiel waren 1,16 MB der 1,8 MB Base64-PNG,
   bei 61 KB Nutztext. Bilder gehören nicht in die Nutzlast. Entweder ganz
   weglassen oder nach S3 schreiben und nur den Key mitgeben.
2. **Keine Werkzeug-Artefakte im Text.** Im Tika-Plaintext standen 33
   `[bookmark: _Toc…]`-Marker und 6 `[image: ]`-Platzhalter mitten im Fließtext.
   Die würden mit-indexiert und mit-embedded. Ebenso: Zeilenenden auf `\n`
   normalisieren (kein CRLF), leere Blöcke weglassen.
3. **Tika bitte über die XHTML-Ausgabe, nicht die Plaintext-Ausgabe.** Der
   Plaintext wirft genau das weg, was wir brauchen: Überschriften sind nicht mehr
   von Fließtext unterscheidbar, und Tabellen werden zu einer eingerückten Zeile
   pro *Zelle* plattgewalzt — Zeilen und Spalten sind dann nicht mehr
   rekonstruierbar. Aus `<h1>` / `<table>` / `<tr>` lässt sich dieses Format
   dagegen sauber befüllen.
4. **Tabellen als Markdown-Pipe-Tabelle** im `text`. Damit bleibt der kanonische
   Volltext lesbar und die Tabelle als Einheit erhalten.
5. **Inhaltsverzeichnis, Kopf- und Fußzeilen markieren, nicht löschen** (`toc`,
   `header`, `footer`). Sie sind ein echtes Problem für die Suche —
   TOC-Einträge sind Beinahe-Duplikate der Überschriften und würden Treffer auf
   das Inhaltsverzeichnis statt auf die Fundstelle liefern. Aber ob sie
   ausgefiltert werden, entscheidet die Anwendung, nicht die Extraktion.
6. **Kein Chunking auf der Extraktionsseite.** Auch nicht mit Doclings
   `HybridChunker`. Die Chunkgröße hängt am Token-Limit des Embedding-Modells der
   Suchanwendung und wird beim Tuning laufend verändert — dieser Parameter darf
   nicht in zwei Repos liegen.

## Beispiele

Zwei vollständige Nutzlasten liegen in `sample_docs/`:

- **`extracted_docling_example.json`** — aus dem echten Docling-Ausgabeformat des
  Beispiel-DOCX erzeugt (565 Blöcke: 35 Überschriften mit Ebene, 239
  Listenpunkte, 9 Tabellen als Markdown, 29 markierte TOC-Einträge; 68 leere
  Elemente und 9 Bilder verworfen). Alle `locator` sind `null` — das ist bei
  einem Word-Dokument korrekt und der zu erwartende Normalfall.
- **`extracted_pdf_example.json`** — ein kurzer, von Hand geschriebener PDF-Fall
  mit gefüllten `{"page": n}`-Locatoren und allen Randfällen: Kopf- und
  Fußzeile, Inhaltsverzeichnis, Tabelle mit Caption, Überschriftenebenen.

Sie sind das, wogegen die Suchanwendung entwickelt wird, solange die Extraktion
noch nicht liefert.

## Offen

**E-Mail-Anhänge.** Eine `.msg` mit angehängten PDFs überlappt die Aufteilung
"Tika für E-Mails, Docling für PDFs": Tika würde die Anhänge mit seinem eigenen
PDF-Parser lesen, also an Docling vorbei. Anhänge müssen als Binärblobs
herausgelöst und einzeln geroutet werden (PDF → Docling, DOCX → Tika). Ob ein
Anhang dann ein eigenes Dokument ist (Vorschlag: ja, mit Verweis auf die E-Mail),
ist noch zu klären und betrifft das Datenmodell der Suchanwendung.
