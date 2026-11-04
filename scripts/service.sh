#!/bin/sh
# Install and run haiku.rag's local search service on macOS from this checkout.
#
#   scripts/service.sh install [--folders-seed FILE] [--no-raycast] [--no-karabiner]
#   scripts/service.sh embedder [--model-from DIR]   # the model and its server
#   scripts/service.sh status | restart | pause | resume | logs [embedder]
#   scripts/service.sh cli ARGUMENT...   # haiku-rag with the service's config
#   scripts/service.sh raycast | karabiner   # one install step
#   scripts/service.sh uninstall
#
# install fetches haiku.rag at the pinned commit (haiku_rag_commit below), builds
# the service's Python environment from it with uv (its uv.lock, non-editable),
# renders service/ with build.sh and installs it under
# ~/Library/Application Support/haiku.rag: the launcher (replaced only when
# launcher.c changes, so its Full Disk Access grant survives), the configuration,
# the folder register (only if missing), the launchd agent, the Raycast extension
# (developer-mode registration) and the Karabiner Cmd+Shift+F rule (only if
# missing). It refuses while a Nix-managed install is present.
#
# embedder installs the Neural Engine embedding server (ane-server/) the service
# embeds with: the model (from --model-from when it differs), the server's Python
# environment and its launchd agent, which starts it on the first request.
#
# HAIKU_RAG_SERVICE_LABEL, HAIKU_RAG_SERVICE_PORT, HAIKU_RAG_EMBED_LABEL and
# HAIKU_RAG_EMBED_PORT override the agents' labels and ports, so a test install
# under another HOME runs beside the real one; HAIKU_RAG_EMBED_IDLE_EXIT is how
# long the embedding server stays without a request (seconds, default 300).
# uv's package cache and the models the service downloads live in
# ~/Library/Caches/haiku.rag, not the caches shared with other tools.
# install.sh is the interactive installer built on this script.
set -eu

# The haiku.rag the service runs: this commit of this repository, built with the
# uv.lock it holds. A full commit id. HAIKU_RAG_RELEASE=VERSION installs that
# release of haiku.rag from PyPI instead (see the README).
haiku_rag_repo=${HAIKU_RAG_REPO:-https://github.com/tolgaerdonmez/haiku.rag.git}
haiku_rag_commit=${HAIKU_RAG_COMMIT:-327e03525e7a3b8ee4d26f15cf28fc63113c7ece}
haiku_rag_release=${HAIKU_RAG_RELEASE:-}

repo=$(cd "$(dirname "$0")/.." && pwd)
app="$HOME/Library/Application Support/haiku.rag"
service="$app/service"
env="$service/env"
src="$service/haiku.rag"
model="$app/models/jina-nano-coreml"
ane="$app/ane-server"
label=${HAIKU_RAG_SERVICE_LABEL:-com.haiku-rag.service}
plist="$HOME/Library/LaunchAgents/$label.plist"
job="gui/$(id -u)/$label"
port=${HAIKU_RAG_SERVICE_PORT:-8765}
embed_label=${HAIKU_RAG_EMBED_LABEL:-com.haiku-rag.ane-server}
embed_plist="$HOME/Library/LaunchAgents/$embed_label.plist"
embed_job="gui/$(id -u)/$embed_label"
embed_port=${HAIKU_RAG_EMBED_PORT:-58741}
idle_exit=${HAIKU_RAG_EMBED_IDLE_EXIT:-300}
cache="$HOME/Library/Caches/haiku.rag"
# This install's own package cache, which install.sh --uninstall --purge deletes.
UV_CACHE_DIR="$cache/uv"
export UV_CACHE_DIR

say() { printf '%s\n' "$*"; }
die() { printf 'service: %s\n' "$*" >&2; exit 1; }

loaded() { /bin/launchctl print "$1" >/dev/null 2>&1; }

running() { /usr/bin/pgrep -f -- "$1" >/dev/null; }

# bootout returns before the service has exited: it drains its jobs for up to
# ingester.workers.shutdown_grace_s, and a second instance must not start meanwhile.
# stop JOB PROCESS-PATTERN
stop() {
    if loaded "$1"; then
        /bin/launchctl bootout "$1" 2>/dev/null || true
    fi
    i=0
    while { loaded "$1" || running "$2"; } && [ $i -lt 150 ]; do sleep 1; i=$((i + 1)); done
    ! loaded "$1" || die "$1 did not unload within 150 s"
    ! running "$2" || die "processes still running after 150 s: $(/usr/bin/pgrep -f -- "$2" | tr '\n' ' ')"
}
stop_agent() { stop "$job" "$env/bin/"; }
stop_embedder() { stop "$embed_job" "$ane/env/bin/"; }

start_agent() {
    /bin/launchctl bootstrap "gui/$(id -u)" "$plist"
}

refuse_nix_install() {
    if [ -L "$app/haiku.rag.yaml" ] && readlink "$app/haiku.rag.yaml" | grep -q '^/nix/store/'; then
        die "$app/haiku.rag.yaml links into /nix/store: the Nix-managed install is still active. Switch it off and activate that first."
    fi
    if [ -e "$plist" ] && grep -q '/nix/store/' "$plist"; then
        die "$plist runs from /nix/store: the Nix-managed agent is still installed. Switch it off and activate that first."
    fi
}

# haiku.rag's source at the pinned commit, in $src.
fetch_haiku_rag() {
    command -v git >/dev/null || die "git not found"
    case "$haiku_rag_commit" in
        *[!0-9a-f]*) die "HAIKU_RAG_COMMIT must be a full commit id: $haiku_rag_commit" ;;
    esac
    [ ${#haiku_rag_commit} -eq 40 ] || die "HAIKU_RAG_COMMIT must be a full commit id: $haiku_rag_commit"
    if [ "$(git -C "$src" rev-parse -q --verify 'HEAD^{commit}' 2>/dev/null || true)" = "$haiku_rag_commit" ] \
        && [ -z "$(git -C "$src" status --porcelain 2>/dev/null)" ]; then
        return 0
    fi
    say "haiku.rag: fetching $(printf '%.10s' "$haiku_rag_commit") from $haiku_rag_repo"
    if [ ! -d "$src/.git" ]; then
        rm -rf "$src"
        git init --quiet "$src"
    fi
    git -C "$src" fetch --quiet --depth 1 "$haiku_rag_repo" "$haiku_rag_commit" \
        || die "could not fetch $haiku_rag_commit from $haiku_rag_repo"
    git -C "$src" -c advice.detachedHead=false checkout --quiet --force --detach FETCH_HEAD
    git -C "$src" clean --quiet -fdx
}

install_env() {
    command -v uv >/dev/null || die "uv not found"
    # A uv-managed CPython: an interpreter from a profile on PATH can change under the environment.
    if [ -n "$haiku_rag_release" ]; then
        say "Python environment: $env (haiku.rag $haiku_rag_release from PyPI)"
        uv venv --quiet --clear --managed-python --python 3.13 "$env"
        uv pip install --quiet --python "$env/bin/python" "haiku.rag[ingester]==$haiku_rag_release"
        return 0
    fi
    fetch_haiku_rag
    say "Python environment: $env (haiku.rag $(printf '%.10s' "$haiku_rag_commit"))"
    UV_PROJECT_ENVIRONMENT="$env" uv sync --project "$src" --frozen --no-dev \
        --no-editable --managed-python --extra ingester --quiet
}

install_ane_env() {
    command -v uv >/dev/null || die "uv not found"
    say "Embedding server environment: $ane/env"
    UV_PROJECT_ENVIRONMENT="$ane/env" uv sync --project "$repo/ane-server" --frozen --no-dev \
        --no-editable --managed-python --quiet
}

# render: the service's files, built into a temporary directory.
render() {
    rendered=$(mktemp -d)
    trap 'rm -rf "$rendered"' EXIT
    "$repo/service/build.sh" --out "$rendered" --ingester "$env/bin/haiku-ingester" \
        --ane-server "$ane/env/bin/haiku-rag-ane-server" --app-dir "$app" --cache-dir "$cache" \
        --port "$port" --label "$label" --embed-port "$embed_port" \
        --embed-label "$embed_label" --idle-exit "$idle_exit" >/dev/null
}

install_launcher() {
    launcher="$service/bin/haiku-rag-service"
    stamp="$service/bin/.launcher.c.sha256"
    want=$(cat "$rendered/launcher.c.sha256")
    have=$(cat "$stamp" 2>/dev/null || true)
    if [ -x "$launcher" ] && [ "$want" = "$have" ]; then
        say "Launcher: unchanged, Full Disk Access kept"
        return 0
    fi
    install -m 755 "$rendered/bin/haiku-rag-service" "$launcher.new"
    mv -f "$launcher.new" "$launcher"
    install -m 644 "$rendered/launcher.c.sha256" "$stamp"
    say "Launcher: NEW. Grant it Full Disk Access (turn the switch off and on if listed):"
    say "  System Settings > Privacy & Security > Full Disk Access > + > $launcher"
    say "  then: $0 restart"
}

install_config() {
    target="$app/haiku.rag.yaml"
    if [ -f "$target" ] && ! cmp -s "$rendered/haiku.rag.yaml" "$target"; then
        cp "$target" "$target.previous"
        say "Configuration: replaced, previous kept as haiku.rag.yaml.previous"
    fi
    install -m 644 "$rendered/haiku.rag.yaml" "$target"
    if [ ! -e "$app/folders.yaml" ]; then
        install -m 644 "${folders_seed:-$rendered/folders.yaml}" "$app/folders.yaml"
        say "Folder register: seeded"
    fi
}

# An index written by haiku.rag's in-process coreml embedder (an earlier version of
# this setup) holds the very vectors the embedding server gives for the same
# model: record the server as its embedder, re-embedding nothing. Any other
# embedder change is left to haiku.rag, which refuses to write to the index.
adopt_embedder() {
    found=$(HAIKU_RAG_CONFIG_PATH="$app/haiku.rag.yaml" "$env/bin/python" - <<'EOF'
import json, os
import lancedb
from haiku.rag.config import get_config

model = get_config().embeddings.model
for name, path in (get_config().lancedb.databases or {}).items():
    path = os.path.expandvars(str(path))
    if not os.path.isdir(path):
        continue
    db = lancedb.connect(path)
    if "settings" not in db.list_tables().tables:
        continue
    rows = db.open_table("settings").search().where("id = 'settings'").limit(1).to_list()
    recorded = json.loads(rows[0]["settings"]).get("embeddings", {}).get("model", {}) if rows else {}
    if (
        recorded.get("provider") == "coreml"
        and recorded.get("vector_dim") == model.vector_dim
        and recorded.get("query_prefix", "") == model.query_prefix
        and recorded.get("document_prefix", "") == model.document_prefix
        and os.path.basename(str(recorded.get("name", "")).rstrip("/")) == "jina-nano-coreml"
    ):
        print(name)
EOF
    ) || die "could not read the index's recorded embedder"
    [ -n "$found" ] || return 0
    say "Index ($(printf '%s' "$found" | tr '\n' ' ' | sed 's/ $//')): written by the in-process coreml embedder;"
    say "  recording the embedding server as its embedder (the same model and vectors: nothing is re-embedded)"
    HAIKU_RAG_CONFIG_PATH="$app/haiku.rag.yaml" "$env/bin/haiku-rag" rebuild --set-embedder >/dev/null
}

install_model() {
    if [ -n "${model_from:-}" ]; then
        [ -e "$model_from/model.mlpackage" ] && [ -e "$model_from/tokenizer.json" ] \
            || die "$model_from holds no model.mlpackage and tokenizer.json"
        if [ -e "$model/model.mlpackage" ] \
            && diff -rq "$model_from/model.mlpackage" "$model/model.mlpackage" >/dev/null \
            && cmp -s "$model_from/tokenizer.json" "$model/tokenizer.json"; then
            say "Model: unchanged"
            return 0
        fi
        rm -rf "$model.new"
        mkdir -p "$model.new"
        /bin/cp -cR "$model_from/model.mlpackage" "$model_from/tokenizer.json" "$model.new/"
        kept=
        if [ -e "$model" ]; then
            rm -rf "$model.previous"
            mv "$model" "$model.previous"
            kept=" (previous kept as $(basename "$model").previous)"
        fi
        mv "$model.new" "$model"
        say "Model: installed from $model_from$kept"
    elif [ ! -e "$model/model.mlpackage" ]; then
        say "Model: MISSING. Neither the embedding server nor the service starts without it. Install it with:"
        say "  $repo/install.sh --model --no-service"
        say "  or download huggingface.co/tolgaerdonmez/jina-embeddings-v5-omni-nano-coreml"
        say "  to DIR, then: $0 embedder --model-from DIR"
    fi
}

install_embedder() {
    model_from=
    while [ $# -gt 0 ]; do
        case "$1" in
            --model-from) model_from=$(cd "$2" && pwd); shift 2 ;;
            *) die "unknown option: $1" ;;
        esac
    done
    [ "$(uname -s)" = Darwin ] || die "macOS only"
    mkdir -p "$ane" "$app/models" "$HOME/Library/LaunchAgents"
    stop_embedder
    install_model
    install_ane_env
    render
    install -m 644 "$rendered/com.haiku-rag.ane-server.plist" "$embed_plist"
    /bin/launchctl bootstrap "gui/$(id -u)" "$embed_plist"
    say "Embedding server: $embed_label loaded (127.0.0.1:$embed_port, starts on the first request, log $ane/ane-server.log)"
}

raycast_digest() {
    (cd "$repo/raycast" \
        && find package.json package-lock.json tsconfig.json eslint.config.js src assets \
            -type f ! -path '*/__tests__/*' | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 \
        | cut -d' ' -f1)
}

install_raycast() {
    work="$app/raycast"
    out="$HOME/.config/raycast/extensions/haiku-rag"
    want=$(raycast_digest)
    if [ "$(cat "$work/.source.sha256" 2>/dev/null || true)" = "$want" ] && [ -e "$out/package.json" ]; then
        say "Raycast: unchanged"
        return 0
    fi
    { command -v node && command -v npm; } >/dev/null || die "node and npm are needed for the Raycast extension"
    [ -n "$(/usr/bin/lsappinfo find bundleid=com.raycast.macos 2>/dev/null)" ] \
        || { say "Raycast: not running, extension not registered; open Raycast and run install again"; return 0; }
    mkdir -p "$work"
    [ -L "$work/node_modules" ] && rm "$work/node_modules"
    lock_before=$(shasum -a 256 "$work/package-lock.json" 2>/dev/null | cut -d' ' -f1 || true)
    /usr/bin/rsync -a --delete --exclude node_modules --exclude raycast-env.d.ts \
        --exclude develop.log --exclude '.source.sha256' --exclude '.store-path' \
        --exclude __tests__ \
        "$repo/raycast/package.json" "$repo/raycast/package-lock.json" \
        "$repo/raycast/tsconfig.json" "$repo/raycast/eslint.config.js" \
        "$repo/raycast/src" "$repo/raycast/assets" "$work/"
    rm -f "$work/.store-path"
    if [ ! -d "$work/node_modules" ] || [ "$lock_before" != "$(shasum -a 256 "$work/package-lock.json" | cut -d' ' -f1)" ]; then
        (cd "$work" && npm ci --no-audit --no-fund --loglevel=error)
    fi
    # Raycast loads a local extension only through developer mode: `ray develop`
    # builds it into $out and registers it, and it stays after develop stops.
    start=$(date +%s)
    (cd "$work" && exec node node_modules/@raycast/api/bin/run.js develop) >"$work/develop.log" 2>&1 &
    pid=$!
    ok=0
    i=0
    while [ $i -lt 120 ]; do
        if [ -f "$out/cli.pid" ] && [ -f "$out/package.json" ] \
            && [ "$(stat -f %m "$out/package.json")" -ge "$start" ]; then
            ok=1
            break
        fi
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
        i=$((i + 1))
    done
    sleep 3
    kill -INT "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    [ "$ok" = 1 ] || die "Raycast registration not confirmed; see $work/develop.log"
    printf '%s\n' "$want" > "$work/.source.sha256"
    say "Raycast: extension registered (Search Documents, Manage Indexed Folders)"
}

install_karabiner() {
    config="$HOME/.config/karabiner/karabiner.json"
    rule="$repo/karabiner/haiku-search.json"
    if [ ! -f "$config" ]; then
        say "Karabiner: no karabiner.json, Cmd+Shift+F rule not installed"
        return 0
    fi
    py="$env/bin/python"
    [ -x "$py" ] || py=/usr/bin/python3
    "$py" - "$config" "$rule" <<'EOF'
import json, os, sys, tempfile
config_path, rule_path = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as f:
    config = json.load(f)
with open(rule_path, encoding="utf-8") as f:
    rule = json.load(f)["rules"][0]
profile = next((p for p in config.get("profiles", []) if p.get("selected")), None)
if profile is None:
    print("Karabiner: no selected profile, Cmd+Shift+F rule not installed")
    sys.exit(0)
rules = profile.setdefault("complex_modifications", {}).setdefault("rules", [])
if any(r.get("description") == rule["description"] for r in rules):
    print("Karabiner: Cmd+Shift+F rule present")
    sys.exit(0)
rules.append(rule)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(config_path))
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=4, ensure_ascii=False)
    f.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, config_path)
print("Karabiner: Cmd+Shift+F rule added to the selected profile")
EOF
}

install_all() {
    folders_seed=
    raycast=1
    karabiner=1
    while [ $# -gt 0 ]; do
        case "$1" in
            --folders-seed) folders_seed=$2; shift 2 ;;
            --no-raycast) raycast=0; shift ;;
            --no-karabiner) karabiner=0; shift ;;
            *) die "unknown option: $1" ;;
        esac
    done
    [ "$(uname -s)" = Darwin ] || die "macOS only"
    refuse_nix_install
    mkdir -p "$service/bin" "$app/models" "$HOME/Library/LaunchAgents"
    stop_agent
    install_env
    render
    install_launcher
    install -m 755 "$rendered/bin/haiku-rag-serve" "$service/bin/haiku-rag-serve"
    install_config
    adopt_embedder
    install_model
    [ -f "$embed_plist" ] || say "Embedding server: not installed; the service embeds with it. Install it: $0 embedder"
    install -m 644 "$rendered/com.haiku-rag.service.plist" "$plist"
    start_agent
    say "Agent: $label loaded (127.0.0.1:$port, log $service/service.log)"
    [ "$raycast" = 0 ] || install_raycast
    [ "$karabiner" = 0 ] || install_karabiner
}

case "${1:-}" in
    install) shift; install_all "$@" ;;
    embedder) shift; install_embedder "$@" ;;
    status)
        if loaded "$job"; then
            /bin/launchctl print "$job" | grep -E '^[[:space:]]*(state|pid|last exit code) =' || true
            /usr/bin/curl -fsS --max-time 30 "http://127.0.0.1:$port/status" && echo
        else
            say "search service: not loaded (paused or not installed)"
        fi
        if loaded "$embed_job"; then
            if running "$ane/env/bin/"; then
                say "embedding server: running on 127.0.0.1:$embed_port"
            else
                say "embedding server: waiting for a request on 127.0.0.1:$embed_port"
            fi
        else
            say "embedding server: not loaded (not installed)"
        fi ;;
    restart)
        /bin/launchctl kickstart -k "$job"
        if running "$ane/env/bin/"; then /bin/launchctl kill TERM "$embed_job" 2>/dev/null || true; fi ;;
    pause) stop_agent; say "stopped until the next login or '$0 resume'" ;;
    resume) start_agent; say "loaded; starts now (RunAtLoad) and on the next request" ;;
    logs)
        if [ "${2:-}" = embedder ]; then exec tail -n 100 -f "$ane/ane-server.log"; fi
        exec tail -n 100 -f "$service/service.log" ;;
    cli)
        shift
        HAIKU_RAG_CONFIG_PATH="$app/haiku.rag.yaml" HF_HOME="$cache/huggingface" \
            DOCLING_CACHE_DIR="$cache/docling" exec "$env/bin/haiku-rag" "$@" ;;
    raycast) install_raycast ;;
    karabiner) install_karabiner ;;
    uninstall)
        stop_agent
        stop_embedder
        rm -f "$plist" "$embed_plist" "$service/bin/haiku-rag-serve"
        rm -rf "$env" "$src" "$ane"
        say "Removed the agents, the embedding server and the Python environment. Kept: the"
        say "launcher (its Full Disk Access grant), the configuration, folders, model, index and"
        say "queue in $app, the Raycast extension and the Karabiner rule." ;;
    *)
        sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' >&2
        exit 64 ;;
esac
