# haiku-rag-macos

macOS setup for [haiku.rag](https://github.com/ggozad/haiku.rag): search your own
files by meaning from Raycast, served by a background service on this Mac, with
the embeddings computed on the Apple Neural Engine.

- **The search service**: `haiku-ingester serve` from haiku.rag as a per-user
  launchd agent. It watches the folders you register, converts and indexes what
  changes, and answers searches on `127.0.0.1:8765`. It starts on demand, runs at
  low priority and exits when idle.
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
| `service` | The search service: launchd agent, its launcher and haiku.rag's Python environment (about 1.9 GB) | yes |
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

(or `./install.sh --uninstall` from a checkout) removes both agents, the launcher,
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
for it has Full Disk Access. That program is the service's launcher,
`~/Library/Application Support/haiku.rag/service/bin/haiku-rag-service`, a small
C program (`service/launcher.c`) that launchd runs and that runs the service as
its child. The service and its conversion processes inherit its access.

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
`Operation not permitted`, and Manage Indexed Folders shows it with no documents.
The embedding server reads only the model and needs no grant.

## How it runs

```text
Raycast ──► 127.0.0.1:8765  search service (haiku-ingester serve)
                 │  launchd: com.haiku-rag.service, launcher with Full Disk Access
                 ▼
            127.0.0.1:58741 Neural Engine embedding server (OpenAI API)
                    launchd: com.haiku-rag.ane-server
```

- **On demand.** launchd listens on both ports and starts each process on its
  first connection. The search service also starts once at login (`RunAtLoad`)
  to index what changed while it was stopped, and exits after
  `ingester.service.idle_exit_s` (30 minutes) with no request and nothing to
  ingest. The embedding server exits after `HAIKU_RAG_EMBED_IDLE_EXIT` seconds
  without a request (300), releasing the model; the next embedding starts it
  again in about a second.
- **Lightly.** The service runs at `Nice 10` with low-priority I/O. Conversion runs
  at most `processing.supervisor.workers` processes at once, each exiting after a
  minute idle, and the throttle pauses conversions on battery, under load and in
  low memory. After indexing it compacts the index back to its live size.
- **Crashes** restart the service (`KeepAlive` on an unsuccessful exit), at most
  every 30 s.

The configuration (`service/haiku.rag.yaml.in`, installed as
`~/Library/Application Support/haiku.rag/haiku.rag.yaml`) is sized for a first
full index: 8 conversion processes with Apple Vision OCR (RapidOCR for the
retry), 1024-token chunks. Lower `processing.supervisor.workers` and
`ingester.workers.worker_count` for a lighter steady state.

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

The service runs haiku.rag from
[tolgaerdonmez/haiku.rag](https://github.com/tolgaerdonmez/haiku.rag), a fork
that is upstream main plus three pull requests open there (fixes, supervised
conversion and the local search service), until those are released upstream.
`scripts/service.sh` pins it to one commit, `haiku_rag_commit`, fetches that commit
and builds the environment with the `uv.lock` it holds, so every install gets the
same packages. `HAIKU_RAG_COMMIT` (a full commit id) and `HAIKU_RAG_REPO` install
another commit or repository.

Once [ggozad/haiku.rag](https://github.com/ggozad/haiku.rag) releases them,
switch to the release: `HAIKU_RAG_RELEASE=0.107.0 scripts/service.sh install`
installs `haiku.rag[ingester]==0.107.0` from PyPI instead (setting
`haiku_rag_release` in the script makes it the default).

## Managing it: `scripts/service.sh`

The installer is built on `scripts/service.sh`, which installs and manages the
service from a checkout:

| Command | |
|---|---|
| `install` | Fetch haiku.rag, build its environment, install the service's files and agent, the Raycast extension and the Karabiner rule. `--no-raycast`, `--no-karabiner`, `--folders-seed FILE` |
| `embedder` | Install the embedding server and its agent; `--model-from DIR` installs that model first |
| `status` | Both agents' state and the service's `/status` |
| `restart` | Restart the service (and stop a running embedding server) |
| `pause`, `resume` | Unload the service's agent until the next login, load it again |
| `logs`, `logs embedder` | Follow the service's or the embedding server's log |
| `cli ARGUMENT...` | `haiku-rag` with the service's configuration, e.g. `cli rebuild --rechunk` (pause first) |
| `raycast`, `karabiner` | One install step |
| `uninstall` | Remove the agents, the embedding server and the environment; data, model, launcher and Raycast extension stay |

`HAIKU_RAG_SERVICE_LABEL`, `HAIKU_RAG_SERVICE_PORT`, `HAIKU_RAG_EMBED_LABEL` and
`HAIKU_RAG_EMBED_PORT` change the agents' labels and ports, so a test install under
another `HOME` runs beside the real one.

### Coming from the installer in haiku.rag

An earlier version of this setup lived in the haiku.rag fork and embedded
in-process with a `coreml` provider. Running this installer over it keeps the
index, the folder list, the model and the launcher (and so its Full Disk Access
grant). The index records the embedder that wrote it; since the embedding server
runs the same model the same way and returns the same vectors, the installer
records the server as that embedder (`haiku-rag rebuild --set-embedder`) and
re-embeds nothing.

## Files

In this repository:

| Path | |
|---|---|
| `install.sh` | The installer |
| `scripts/install-tui.py` | Its menu |
| `scripts/service.sh` | Install and manage the service and the embedding server |
| `service/launcher.c` | The launcher: holds the Full Disk Access grant, hands launchd's socket to the service, runs it as its child |
| `service/haiku-rag-serve.in` | What the launcher runs: `haiku-ingester serve`, or a successful exit while the model is missing |
| `service/com.haiku-rag.service.plist.in`, `service/com.haiku-rag.ane-server.plist.in` | The launchd agents |
| `service/haiku.rag.yaml.in`, `service/folders.yaml` | The configuration and the empty folder register |
| `service/build.sh` | Compiles the launcher and renders all of the above for an install directory, installing nothing (for other installers, such as a Nix module) |
| `ane-server/` | The embedding server, with its `uv.lock` |
| `raycast/` | The Raycast extension ([README](raycast/README.md)) |
| `karabiner/haiku-search.json` | The Cmd+Shift+F rule |

Installed under `~/Library/Application Support/haiku.rag/`:

| Path | |
|---|---|
| `haiku.rag.yaml`, `folders.yaml` | Configuration and folder register |
| `files.lancedb/`, `ingester.db` | The index and the queue |
| `models/jina-nano-coreml/` | The embedding model |
| `service/bin/haiku-rag-service` | The launcher, the program granted Full Disk Access |
| `service/env/`, `service/haiku.rag/` | haiku.rag's Python environment and the source it was built from |
| `ane-server/env/`, `ane-server/ane-server.log` | The embedding server's environment and log |
| `service/service.log` | The service's log |
| `raycast/` | The Raycast extension's working copy |
| `source/` | This repository, when the installer ran on its own |

Caches, deleted by `install.sh --uninstall --purge`:

| Path | |
|---|---|
| `~/Library/Caches/haiku.rag/uv/` | uv's package cache for the Python environments and the menu |
| `~/Library/Caches/haiku.rag/huggingface/`, `~/Library/Caches/haiku.rag/docling/` | Models the service downloads: docling's layout and table models, the chunking tokenizer |
| `~/Library/Application Support/com.raycast.macos/extensions/haiku-rag/` | The Raycast extension's page previews |

## Development

```bash
uv run --project ane-server pytest            # installer, menu and embedding server
uv run --project ane-server pytest -m integration   # also on a terminal and with test agents
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
