# haiku-rag-macos

macOS setup for [haiku.rag](https://github.com/ggozad/haiku.rag): search your own
files by meaning from Raycast, served by a background service on this Mac, with
the embeddings computed on the Apple Neural Engine.

- **The indexer**: `haiku-ingester serve` from
  [haiku.rag](https://pypi.org/project/haiku.rag/) (0.107.0, from PyPI) as a
  per-user launchd agent. It watches the folders you register, converts and
  indexes what changes, runs at low priority and exits when idle.
- **The search service** (`desktop/`): `haiku-rag-macos reader`, a small server
  on `127.0.0.1:8765` that reads the index through haiku.rag's client and
  answers searches, manages the folders and reports the indexer's status. It
  starts on demand and exits when idle.
- **The Neural Engine embedding server** (`ane-server/`): an OpenAI-compatible
  `/v1/embeddings` endpoint on `127.0.0.1:58741` running
  jina-embeddings-v5-omni-nano as a Core ML model on the Neural Engine. haiku.rag
  embeds through it with `provider: openai`. It starts on the first request and
  exits after five idle minutes.
- **A Raycast extension** (`raycast/`): Search Documents (results grouped by file,
  a page preview with the passage highlighted, opening the exact page or line)
  and Manage Indexed Folders.
- **A Cmd+Shift+F hotkey** (`karabiner/`) opening Search Documents, through
  Karabiner-Elements.
- **An installer** that asks what to install, installs it and removes it again.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/tolgaerdonmez/haiku-rag-macos/main/install.sh | sh
```

It needs macOS 15 or later (Apple Silicon for the Neural Engine) and the Xcode
Command Line Tools; it offers to install [uv](https://docs.astral.sh/uv), which
builds the Python environments. Run on its own, the installer clones this
repository into `~/Library/Application Support/haiku.rag/source` (or updates that
clone) and runs the copy there; from a checkout, `./install.sh` installs that
checkout.

It checks the Mac, then opens a menu in the terminal: every component is a
checkbox (arrows move, space checks or unchecks, Return continues), with the
recommended ones checked and the unavailable ones greyed out with the reason. It
then asks where the model comes from and which folders to index (the usual ones
that exist, any other folder you type, Tab completing), shows the plan, and
installs exactly what is checked, goes back to change it, or cancels without
changing anything.

| Component | | Preselected |
|---|---|---|
| `service` | The indexer and the search service: their launchd agents, the launcher and the Python environment with haiku.rag (about 1.9 GB) | yes |
| `model` | The embedding model from Hugging Face (about 850 MB installed, 420 MB download, each file checked against its SHA-256) and the Neural Engine server that runs it, with its own launchd agent | yes |
| `raycast` | The Raycast extension. Needs Node.js | when Raycast is installed |
| `hotkey` | Cmd+Shift+F opens Search Documents, as a Karabiner-Elements rule. Other apps lose Cmd+Shift+F | no; offered when Karabiner is installed |
| `folders` | Folders to index (`~/Documents` suggested), registered with the excludes `.*`, `*.json`, `*.yaml`, `*.yml`. Needs the service | yes |
| `fda` | Afterwards, open the Full Disk Access pane and the launcher in Finder | on a first install |

Afterwards it shows the [Full Disk Access](#full-disk-access) step with the
launcher's exact path, checks that the service answers, runs a test search when
documents are indexed, and prints how to pause and uninstall. Running it again
updates what changed and keeps the index, the folder list and the model.

Options pick components without asking: `--service`, `--model`, `--raycast`,
`--hotkey` and their `--no-` forms, `--folder PATH` (repeatable) and
`--no-folders`. `--yes` asks nothing and installs the preselected set, without
folders unless `--folder` names them. `--dry-run` checks the Mac, shows the menu
and prints the plan without changing anything. With the one-liner, pass them
after `sh -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/tolgaerdonmez/haiku-rag-macos/main/install.sh \
  | sh -s -- --yes --folder ~/Documents
```

`--model-from DIR` installs a model folder you already have (`model.mlpackage`,
`tokenizer.json`) instead of downloading one; `--model-repo ID` and
`--model-revision REV` download from another Hugging Face repository.
`HAIKU_RAG_MACOS_REPO`, `HAIKU_RAG_MACOS_REF` and `HAIKU_RAG_MACOS_SOURCE` pick the
repository, branch and clone directory of the one-liner. The menu is
`scripts/install-tui.py`, which uv runs with its pinned `questionary`; without uv,
or with `HAIKU_RAG_TUI=0`, a numbered menu asks instead.

## Uninstall

```bash
~/Library/Application\ Support/haiku.rag/source/install.sh --uninstall
```

(or `./install.sh --uninstall` from a checkout) removes the agents, the launcher,
the Python environments, the embedding server, the configuration, the Raycast
extension, the Cmd+Shift+F rule, the model and the fetched sources. It asks before
deleting the index, the queue and the folder list, and keeps them by default
(`--delete-index` deletes them, `--keep-model` keeps the model). It never touches
the indexed folders or their files. Remove `haiku-rag-service` from
System Settings > Privacy & Security > Full Disk Access yourself.

The caches stay unless you add `--purge` (or answer yes when the uninstaller asks;
the default is no): it also deletes `~/Library/Caches/haiku.rag`, where uv keeps
the packages the Python environments were built from and the service keeps
docling's layout and table models and the chunking tokenizer, and the Raycast
extension's page previews in
`~/Library/Application Support/com.raycast.macos/extensions/haiku-rag` (up to
200 MB). The installer points uv and the service at that cache of their own
because `~/.cache/uv` and `~/.cache/huggingface` are shared with other tools, so
nothing there can be deleted as ours. An install made before this version
downloaded into those shared caches; `--purge` leaves them alone (docling's models
are `~/.cache/huggingface/hub/models--docling-project--*`, the tokenizer
`models--Qwen--Qwen3-Embedding-0.6B`). `--dry-run --uninstall` lists every path
the uninstall would delete, with its size, and changes nothing:

```bash
./install.sh --uninstall --purge --dry-run
```

## Full Disk Access

Folders under `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive are
private: a process launchd starts cannot read them until the program responsible
for it has Full Disk Access. That program is the launcher,
`~/Library/Application Support/haiku.rag/service/bin/haiku-rag-service`, a small
C program (`service/launcher.c`) that launchd runs for both agents and that
runs the indexer, or the search service, as its child. They inherit its access:
the indexer reads the folders, the search service reads a text file to find a
hit's line.

1. Open **System Settings > Privacy & Security > Full Disk Access**.
2. Click **+**, press **Cmd+Shift+G**, paste the launcher's path, press Return and
   click **Open**.
3. Turn on the switch next to **haiku-rag-service**.
4. Restart the service: `scripts/service.sh restart`.

The grant names the launcher's code, and the launcher only runs what it is
given, so it survives upgrades of haiku.rag, Python and the configuration. The
installer replaces the launcher only when `launcher.c` changes; then the switch
has to be turned off and on again, and the installer says so. Without the grant,
a folder under a private location fails discovery: its sweep logs
`Operation not permitted` in `service/ingester.log`, and Manage Indexed Folders
shows it with no documents.
The embedding server reads only the model and needs no grant.

## How it runs

```text
Raycast ──► 127.0.0.1:8765  search service (haiku-rag-macos reader)
                 │  launchd: com.haiku-rag.service, on demand
                 │  reads the index; asks the indexer for status and failures
                 ▼
            127.0.0.1:8766  indexer (haiku-ingester serve, haiku.rag)
                 │  launchd: com.haiku-rag.ingester, writes the index
                 ▼
            127.0.0.1:58741 Neural Engine embedding server (OpenAI API)
                    launchd: com.haiku-rag.ane-server
```

Both service agents run through the launcher, which holds the Full Disk Access
grant.

- **The indexer writes, the search service reads.** haiku-ingester is the one
  writer of the index. The search service opens it read-only through
  haiku.rag's client, and gets the queue's state and the failed documents from
  haiku-ingester's own API (`/stats`, `/sources`, `/dlq`).
- **On demand.** launchd listens on 8765 and 58741 and starts the search
  service and the embedding server on their first connection; they exit after
  15 and 5 idle minutes (`HAIKU_RAG_EMBED_IDLE_EXIT`, 300 s). The indexer starts
  at login, every hour, when the folder list changes, and whenever the search
  service starts, indexes what changed, and exits once it has been idle for 10
  minutes and its maintenance pass has run (`haiku-rag-macos ingester
  --idle-exit`).
- **Lightly.** The indexer runs at `Nice 10` with low-priority I/O and 4
  conversion workers. haiku.rag's maintenance (`ingester.maintenance`) vacuums
  the index and builds its vector index two quiet minutes after the last
  write.
- **Stalls and crashes.** A conversion that runs past
  `processing.conversion_timeout` (300 s) fails its document, and haiku-ingester
  exits; launchd restarts it (`KeepAlive` on an unsuccessful exit, at most every
  30 s), so only that document is lost. Large PDFs convert 20 pages at a time
  (`processing.split_pages`).

The configuration (`service/haiku.rag.yaml.in`, installed as
`~/Library/Application Support/haiku.rag/haiku.rag.yaml`) uses Apple Vision OCR
and 1024-token chunks, and keeps PDF attachments out (haiku.rag#829). The
folders live in `databases.d/`, one file per database (haiku.rag's
`databases_include`), which `scripts/service.sh folders`, Manage Indexed Folders
and the search service's `/folders` edit; haiku-ingester reloads it. A folder
removed with its documents is listed in `databases.d/.retired.yaml`, and the
`macos-folders` haiku.rag hook (`desktop/haiku_rag_macos/hook.py`) deletes its
documents inside haiku-ingester when it restarts the database. The search
service's HTTP contract is [docs/search-api.md](docs/search-api.md).

## The embedding server

`ane-server/haiku_rag_ane.py` runs
[tolgaerdonmez/jina-embeddings-v5-omni-nano-coreml](https://huggingface.co/tolgaerdonmez/jina-embeddings-v5-omni-nano-coreml),
a Core ML conversion of `jinaai/jina-embeddings-v5-omni-nano-retrieval` (768
dimensions) with one function per sequence length, 64 to 2048 tokens. Each text
runs on the shortest function that fits it, with the CPU and Neural Engine
compute units, through PyObjC's Core ML bindings: no coremltools, no PyTorch. It
answers `POST /v1/embeddings` (`input` a string or a list, `encoding_format`
`float` or `base64`, `dimensions` to truncate), `GET /v1/models` and
`GET /health`, so any OpenAI-compatible client can use it:

```bash
curl -s http://127.0.0.1:58741/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"input": "Query: chain rule"}'
```

The model expects the prefixes `Query: ` and `Document: `; haiku.rag adds them
(`embeddings.model.query_prefix`, `document_prefix`). Run by hand it binds
`--host` and `--port`; under launchd it serves the socket launchd holds.

## haiku.rag's version

haiku.rag comes from PyPI, unmodified: the service uses only its public API
(the client, the CLI, haiku-ingester and its HTTP API, configuration fragments
and hooks). `desktop/pyproject.toml` pins `haiku.rag-slim[docling,ingester]`
and constrains every other package to the versions in haiku.rag's own
`uv.lock` at that release, so the service runs the package set haiku.rag was
tested with; `desktop/uv.lock` holds the result. Two additions: `onnxruntime`,
without which RapidOCR falls back to torch (haiku.rag#828), and `ocrmac` for
Apple Vision OCR.

To move to another release, with haiku.rag's `uv.lock` at its tag:

```bash
scripts/pin-haiku-rag.py 0.108.0 path/to/haiku.rag/uv.lock
(cd desktop && uv lock)
scripts/service.sh install
```

## Managing it: `scripts/service.sh`

The installer is built on `scripts/service.sh`, which installs and manages the
service from a checkout:

| Command | |
|---|---|
| `install` | Build the Python environment from `desktop/uv.lock`, install the launcher, configuration and both agents, the Raycast extension and the Karabiner rule. `--no-raycast`, `--no-karabiner` |
| `embedder` | Install the embedding server and its agent; `--model-from DIR` installs that model first |
| `status` | The indexer's state, the search service's `/status`, the embedding server's state |
| `restart` | Restart the indexer, stop the search service until its next request (and stop a running embedding server) |
| `pause`, `resume` | Unload the indexer's and the search service's agents until the next login, load them again |
| `logs`, `logs reader`, `logs embedder` | Follow the indexer's, the search service's or the embedding server's log |
| `folders list`, `folders add DIR`, `folders update ID`, `folders remove ID` | The indexed folders (`haiku-rag-macos folders`: `--include`, `--exclude`, `--id`, `--keep-documents`) |
| `cli ARGUMENT...` | `haiku-rag` with the service's configuration, e.g. `cli info` |
| `raycast`, `karabiner` | One install step |
| `uninstall` | Remove the agents, the embedding server and the environment; data, model, launcher and Raycast extension stay |

`HAIKU_RAG_SERVICE_LABEL`, `HAIKU_RAG_SERVICE_PORT` (the search service),
`HAIKU_RAG_INGESTER_LABEL`, `HAIKU_RAG_INGESTER_PORT` (the indexer's API),
`HAIKU_RAG_EMBED_LABEL` and `HAIKU_RAG_EMBED_PORT` change the agents' labels and
ports, so a test install under another `HOME` runs beside the real one.

### Coming from an earlier install

An earlier version of this setup ran a haiku.rag fork whose `haiku-ingester
serve` also searched, kept a `folders.yaml` register and supervised its
conversions in processes of their own. Running this installer over it keeps the
index, the queue, the model and the launcher (and so its Full Disk Access
grant): haiku.rag 0.107.0 opens that index as it is, re-indexing nothing. It
moves the folders of `folders.yaml` into `databases.d/files.yaml` with the same
ids, so every document keeps its folder, and keeps the old file as
`folders.yaml.previous`. The configuration is replaced (the old one kept as
`haiku.rag.yaml.previous`), and the agent `com.haiku-rag.service` becomes the
search service beside the new `com.haiku-rag.ingester`. Chunks the fork indexed
carry their page boxes in their metadata; the search service reads them, and
resolves the boxes of newer chunks from the stored document.

An install older still embedded in-process with a `coreml` provider. The index
records the embedder that wrote it; since the embedding server runs the same
model the same way and returns the same vectors, the installer records the
server as that embedder (`haiku-rag rebuild --set-embedder`) and re-embeds
nothing.

## Files

In this repository:

| Path | |
|---|---|
| `install.sh` | The installer |
| `scripts/install-tui.py` | Its menu |
| `scripts/service.sh` | Install and manage the services and the embedding server |
| `scripts/pin-haiku-rag.py` | Pins `desktop/` to a haiku.rag release's tested package set |
| `desktop/` | `haiku-rag-macos`: the search service, the folder register, the `macos-folders` hook and the indexer's idle exit, with its `uv.lock` |
| `docs/search-api.md` | The search service's HTTP contract |
| `service/launcher.c` | The launcher: holds the Full Disk Access grant, hands launchd's socket to the search service, runs either service as its child |
| `service/haiku-rag-reader.in`, `service/haiku-rag-ingester.in` | What the launcher runs; the indexer exits successfully while the model is missing |
| `service/com.haiku-rag.service.plist.in`, `service/com.haiku-rag.ingester.plist.in`, `service/com.haiku-rag.ane-server.plist.in` | The launchd agents |
| `service/haiku.rag.yaml.in` | The configuration |
| `service/build.sh` | Compiles the launcher and renders all of the above for an install directory, installing nothing (for other installers, such as a Nix module) |
| `ane-server/` | The embedding server, with its `uv.lock` |
| `raycast/` | The Raycast extension ([README](raycast/README.md)) |
| `karabiner/haiku-search.json` | The Cmd+Shift+F rule |

Installed under `~/Library/Application Support/haiku.rag/`:

| Path | |
|---|---|
| `haiku.rag.yaml` | Configuration |
| `databases.d/` | The indexed folders, one file per database, and `.retired.yaml` |
| `files.lancedb/`, `ingester.db` | The index and the queue |
| `models/jina-nano-coreml/` | The embedding model |
| `service/bin/haiku-rag-service` | The launcher, the program granted Full Disk Access |
| `service/env/` | The Python environment: haiku.rag and haiku-rag-macos |
| `service/ingester.log`, `service/reader.log` | The indexer's and the search service's logs |
| `ane-server/env/`, `ane-server/ane-server.log` | The embedding server's environment and log |
| `raycast/` | The Raycast extension's working copy |
| `source/` | This repository, when the installer ran on its own |

Caches, deleted by `install.sh --uninstall --purge`:

| Path | |
|---|---|
| `~/Library/Caches/haiku.rag/uv/` | uv's package cache for the Python environments and the menu |
| `~/Library/Caches/haiku.rag/huggingface/`, `~/Library/Caches/haiku.rag/docling/` | Models the indexer downloads: docling's layout and table models, the chunking tokenizer |
| `~/Library/Application Support/com.raycast.macos/extensions/haiku-rag/` | The Raycast extension's page previews |

## Development

```bash
uv run --project ane-server pytest            # installer, menu and embedding server
uv run --project ane-server pytest -m integration   # also on a terminal and with test agents
uv run --project desktop pytest desktop/tests       # search service, folders, hook, idle exit
(cd raycast && npm install && npm test)
```

The tests install into throwaway `HOME`s with their own agent labels and ports.

## Licenses

The code in this repository is MIT-licensed ([LICENSE](LICENSE)), like haiku.rag.
It contains no model and no code that converts or modifies one: the installer
downloads the converted model from Hugging Face, and the embedding server runs
it. The converted model and the script that converts it live in
[that Hugging Face repository](https://huggingface.co/tolgaerdonmez/jina-embeddings-v5-omni-nano-coreml),
under the original model's license, CC BY-NC 4.0 by Jina AI: non-commercial use,
with attribution.
