# Search service API

The search service is `haiku-rag-macos reader` (`desktop/haiku_rag_macos/reader.py`):
it answers searches over the indexed files, manages the indexed folders and
reports what haiku-ingester is doing. This page is the contract the Raycast
extension (and any script) builds against.

It reads and haiku-ingester writes. Searches open the databases read-only
through haiku.rag's client; a folder change edits the folder fragments and asks
haiku-ingester to reload; status and failures come from haiku-ingester's own
API (`/stats`, `/sources`, `/dlq`).

- **Base URL:** `http://127.0.0.1:8765`, the socket of the
  `com.haiku-rag.service` launchd agent. The service binds the loopback
  interface only.
- **Format:** JSON in and out, UTF-8. Errors are `{"detail": "..."}` with a 4xx
  or 5xx status.
- **Auth:** none by default. When `ingester.api.auth_token` is set in
  haiku.rag.yaml, every endpoint except `GET /health` needs
  `Authorization: Bearer <token>`, and the service passes it on to
  haiku-ingester.
- **Start-up:** launchd starts the service on the first connection and it exits
  after 15 idle minutes. That request, and the first search after the
  embedding model was unloaded, can take a few seconds. Use a 15 s timeout for
  searches, 5 s for everything else.
- **Versioning:** additive changes (new fields, new endpoints) do not bump
  anything. Clients must ignore fields they do not know.

## Coordinates

A hit locates its passage per page as boxes in the page's own coordinate
space:

- Units are the converter's page units: **PDF points** (1/72 inch) for PDFs,
  pixels for images. `width` and `height` give the page size in the same units.
- The origin is the page's **top-left corner**, `top < bottom`.
- To draw a box in PDF user space (origin bottom-left, as Skim and PDFKit use):
  `x = left`, `y = height - bottom`, `w = right - left`, `h = bottom - top`.
- Page numbers are 1-based and match the PDF's page index (page 1 is the first
  page of the file, not a printed page label).

The boxes are each matched item's provenance in the stored document, resolved
the way haiku.rag's `visualize_chunk` resolves them; chunks indexed by the
earlier haiku.rag fork recorded the same boxes, and those are read directly.
Documents without page layout (Markdown, plain text, code, HTML, EPUB, Word)
have `page_numbers: []` and `pages: []`. PowerPoint pages are slides.

## Search

### `GET /search`

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `q` | string, required | | The query, natural language or keywords. |
| `limit` | int, 1-50 | 10 | Hits to return. |
| `type` | `hybrid`, `vector`, `fts` | `hybrid` | Search mode. `fts` needs no embedding model. |
| `database` | string, repeatable | all | Restrict to these databases (names from `lancedb.databases`). |
| `folder` | string, repeatable | all | Restrict to documents of these folders (folder ids). |
| `context` | bool | `false` | Also return each hit's passage expanded to its section, in `context`. |

Response:

```json
{
  "query": "travis picking exercises",
  "took_ms": 23.4,
  "hits": [
    {
      "rank": 1,
      "score": 0.0312,
      "chunk_id": "4f0c...",
      "document_id": "8a1e...",
      "database": "files",
      "folder_id": "guitar",
      "title": "Country Guitar",
      "path": "/Users/me/Documents/Guitar/Country Guitar.pdf",
      "uri": "file:///Users/me/Documents/Guitar/Country%20Guitar.pdf",
      "document_type": "pdf",
      "content_type": "application/pdf",
      "page_numbers": [40, 41],
      "line": null,
      "headings": ["Chapter 5", "Travis Picking"],
      "labels": ["text", "list_item"],
      "snippet": "...Travis picking is a fingerstyle technique named after...",
      "snippet_highlights": [[3, 9], [10, 17]],
      "text": "Travis picking is a fingerstyle technique named after Merle Travis...",
      "pages": [
        {"page": 40, "width": 612.0, "height": 792.0,
         "boxes": [{"left": 72.0, "top": 412.3, "right": 540.0, "bottom": 498.7}]},
        {"page": 41, "width": 612.0, "height": 792.0,
         "boxes": [{"left": 72.0, "top": 60.1, "right": 540.0, "bottom": 96.4}]}
      ],
      "context": null
    }
  ]
}
```

| Field | Type | Meaning |
|---|---|---|
| `rank` | int | 1-based position, best first. |
| `score` | float | Ranking score. Only comparable within one response. |
| `chunk_id` | string | The matched chunk. Unique within its database. |
| `document_id` | string | The document. |
| `database` | string | Database the hit came from. |
| `folder_id` | string or null | Folder that ingested the document; null for documents added another way. |
| `title` | string or null | Document title, else the file name. |
| `path` | string or null | Absolute local file path; null when the document is not a local file. |
| `uri` | string or null | The stored URI (`file://...` for local files). |
| `document_type` | string | One of `pdf`, `word`, `powerpoint`, `excel`, `epub`, `html`, `markdown`, `text`, `code`, `image`, `other`. |
| `content_type` | string or null | MIME type recorded at ingest. |
| `page_numbers` | int[] | Pages the passage spans, ascending. |
| `line` | int or null | 1-based line in the file where the passage starts, for `markdown`, `text` and `code` documents when it can be found; null otherwise. |
| `headings` | string[] or null | Section heading path, outermost first. |
| `labels` | string[] | Docling element labels of the passage's items, in passage order. |
| `snippet` | string | At most about 300 characters of the passage around the best match, with `...` where cut. |
| `snippet_highlights` | `[int, int][]` | `[start, end)` character offsets into `snippet` of query words found in it. |
| `text` | string | The whole matched passage. |
| `pages` | object[] | Per page: `page`, `width`, `height`, and `boxes` (`left`, `top`, `right`, `bottom`), see Coordinates. |
| `context` | string or null | The passage expanded to its section, when `context=true`. |

### `GET /documents/{document_id}/search`

Search within one document. Takes `q`, `limit`, `type`, `database` and
`context` as above. Same response shape; every hit has this `document_id`.

## Folders

A folder is a directory haiku-ingester indexes, an `fs` source in the
`databases.d` fragments of haiku.rag.yaml (`databases_include`). A change is
written at once and haiku-ingester applies it with a reload, started in the
background: it starts watching an added folder and indexing it, and stops
watching a removed one.

```json
{
  "id": "guitar",
  "database": "files",
  "path": "/Users/me/Documents/Guitar",
  "include": ["**/*.pdf"],
  "exclude": ["**/drafts/**"],
  "status": {
    "watching": true,
    "documents": 507,
    "queued": 3,
    "failed": 2,
    "last_scan_at": "2026-11-03T10:12:00Z"
  }
}
```

`include` and `exclude` are gitignore-style patterns matched against each file's
absolute path, so `*.pdf` and `drafts/` match at any depth. An empty `include`
takes every supported file. `status` is null in the answer to an add or change,
before haiku-ingester runs the folder. `watching` is false while haiku-ingester
is not running, and the service starts it when asked for folders, status or
failures.

### `GET /folders`

`{"folders": [<folder>, ...]}`

### `POST /folders`

Body: `{"path": "/abs/dir", "database": "files", "include": [], "exclude": [], "id": "guitar"}`.
Only `path` is required. `database` defaults to the only configured database;
`id` defaults to one derived from the directory name. Returns `201` with the
folder. `400` when the path is not an existing directory, the database is
unknown or the configuration would not load, `409` when the id is taken or the
path is registered already or overlaps a registered folder.

### `PATCH /folders/{id}`

Body: any of `include`, `exclude`. Returns the folder.

### `DELETE /folders/{id}`

Query: `keep_documents` (bool, default `false`). Stops watching the folder.
Unless `keep_documents=true`, its documents are deleted by haiku-ingester when
it restarts the database for the change (the `macos-folders` hook). Returns
`{"removed": "guitar"}`, `404` for an unknown id.

## Status and failures

### `GET /status`

```json
{
  "indexing": true,
  "queued": 12,
  "in_flight": 2,
  "failed": 2,
  "paused": false,
  "pause_reason": null,
  "ingester_running": true,
  "embedder_loaded": true,
  "databases": [{"name": "files", "documents": 1204}]
}
```

`failed` counts the dead-letter jobs of registered folders. `paused` is always
false: nothing holds ingestion back. `ingester_running` is false when
haiku-ingester is stopped and could not be started; the counts are 0 then.

### `GET /failures`

Documents that failed to ingest, haiku-ingester's dead-letter queue, newest
first. Query: `folder`, `limit` (default 50), `offset`. Without `folder`, only
registered folders' failures. `503` while haiku-ingester is not running.

```json
{
  "failures": [
    {
      "job_id": "eb4d6bde-...",
      "folder_id": "guitar",
      "database": "files",
      "path": "/Users/me/Documents/Guitar/scan.pdf",
      "uri": "file:///Users/me/Documents/Guitar/scan.pdf",
      "reason": "conversion timed out after 300s",
      "attempts": 1,
      "failed_at": "2026-11-03T10:12:00Z"
    }
  ]
}
```

### `POST /failures/{job_id}/retry`

Queues the document again. Returns `{"retried": ["eb4d6bde-..."]}`.

### `POST /failures/retry`

Queues every failure again, optionally only `folder`'s. Returns
`{"retried": [...]}`.

### `GET /health`

Unauthenticated liveness: `{"status": "ok", "search_loaded": false}`.
