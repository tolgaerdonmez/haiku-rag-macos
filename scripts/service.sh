#!/bin/sh
# Install and run the local search service on macOS from this checkout.
#
#   scripts/macos-service.sh install [--model-from DIR] [--folders-seed FILE]
#                                    [--no-raycast] [--no-karabiner]
#   scripts/macos-service.sh status | restart | pause | resume | logs
#   scripts/macos-service.sh cli ARGUMENT...   # haiku-rag with the service's config
#   scripts/macos-service.sh model DIR | raycast | karabiner   # one install step
#   scripts/macos-service.sh uninstall
#
# install builds the service's Python environment from this checkout with uv
# (non-editable, so the checkout may move afterwards), renders service/macos
# with build.sh and installs it under ~/Library/Application Support/haiku.rag:
# the launcher (replaced only when launcher.c changes, so its Full Disk Access
# grant survives), the configuration, the folder register (only if missing), the
# Core ML model (from --model-from when it differs), the launchd agent, the
# Raycast extension (developer-mode registration) and the Karabiner Cmd+Shift+F
# rule (only if missing). It refuses while a Nix-managed install is present.
# HAIKU_RAG_SERVICE_LABEL and HAIKU_RAG_SERVICE_PORT override the agent's label
# and port, so a test install under another HOME runs beside the real one.
# scripts/install-macos.sh is the interactive installer built on this script.
set -eu

repo=$(cd "$(dirname "$0")/.." && pwd)
app="$HOME/Library/Application Support/haiku.rag"
service="$app/service"
env="$service/env"
model="$app/models/jina-nano-coreml"
label=${HAIKU_RAG_SERVICE_LABEL:-com.haiku-rag.service}
plist="$HOME/Library/LaunchAgents/$label.plist"
job="gui/$(id -u)/$label"
port=${HAIKU_RAG_SERVICE_PORT:-8765}

say() { printf '%s\n' "$*"; }
die() { printf 'macos-service: %s\n' "$*" >&2; exit 1; }

loaded() { /bin/launchctl print "$job" >/dev/null 2>&1; }

running() { /usr/bin/pgrep -f -- "$env/bin/" >/dev/null; }

# bootout returns before the service has exited: it drains its jobs for up to
# ingester.workers.shutdown_grace_s, and a second instance must not start meanwhile.
stop_agent() {
    if loaded; then
        /bin/launchctl bootout "$job" 2>/dev/null || true
    fi
    i=0
    while { loaded || running; } && [ $i -lt 150 ]; do sleep 1; i=$((i + 1)); done
    ! loaded || die "the agent did not unload within 150 s"
    ! running || die "service processes still running after 150 s: $(/usr/bin/pgrep -f -- "$env/bin/" | tr '\n' ' ')"
}

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

install_env() {
    command -v uv >/dev/null || die "uv not found"
    say "Python environment: $env"
    # A uv-managed CPython: an interpreter from a profile on PATH can change under the environment.
    UV_PROJECT_ENVIRONMENT="$env" uv sync --project "$repo" --frozen --no-dev \
        --no-editable --managed-python --extra ingester --extra coreml --quiet
}

install_launcher() {
    rendered=$1
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
    rendered=$1
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
        say "Model: MISSING. The service will not start without it. Install it with:"
        say "  $repo/scripts/install-macos.sh --model --no-service"
        say "  or download huggingface.co/tolgaerdonmez/jina-embeddings-v5-omni-nano-coreml"
        say "  to DIR, then: $0 model DIR"
    fi
}

raycast_digest() {
    (cd "$repo/clients/raycast" \
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
        "$repo/clients/raycast/package.json" "$repo/clients/raycast/package-lock.json" \
        "$repo/clients/raycast/tsconfig.json" "$repo/clients/raycast/eslint.config.js" \
        "$repo/clients/raycast/src" "$repo/clients/raycast/assets" "$work/"
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
    rule="$repo/clients/raycast/hotkey/karabiner-haiku-search.json"
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
    model_from=
    folders_seed=
    raycast=1
    karabiner=1
    while [ $# -gt 0 ]; do
        case "$1" in
            --model-from) model_from=$(cd "$2" && pwd); shift 2 ;;
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
    rendered=$(mktemp -d)
    trap 'rm -rf "$rendered"' EXIT
    "$repo/service/macos/build.sh" --out "$rendered" --ingester "$env/bin/haiku-ingester" \
        --app-dir "$app" --port "$port" --label "$label" >/dev/null
    install_launcher "$rendered"
    install -m 755 "$rendered/bin/haiku-rag-serve" "$service/bin/haiku-rag-serve"
    install_config "$rendered"
    install_model
    install -m 644 "$rendered/com.haiku-rag.service.plist" "$plist"
    start_agent
    say "Agent: $label loaded (127.0.0.1:$port, log $service/service.log)"
    [ "$raycast" = 0 ] || install_raycast
    [ "$karabiner" = 0 ] || install_karabiner
}

case "${1:-}" in
    install) shift; install_all "$@" ;;
    status)
        if loaded; then
            /bin/launchctl print "$job" | grep -E '^[[:space:]]*(state|pid|last exit code) =' || true
            /usr/bin/curl -fsS --max-time 30 "http://127.0.0.1:$port/status" && echo
        else
            say "not loaded (paused or not installed)"
        fi ;;
    restart) /bin/launchctl kickstart -k "$job" ;;
    pause) stop_agent; say "stopped until the next login or '$0 resume'" ;;
    resume) start_agent; say "loaded; starts now (RunAtLoad) and on the next request" ;;
    logs) exec tail -n 100 -f "$service/service.log" ;;
    cli) shift; HAIKU_RAG_CONFIG_PATH="$app/haiku.rag.yaml" exec "$env/bin/haiku-rag" "$@" ;;
    model)
        [ $# -eq 2 ] || die "usage: $0 model DIR"
        model_from=$(cd "$2" && pwd)
        mkdir -p "$app/models"
        was_loaded=0
        if loaded; then was_loaded=1; stop_agent; fi
        install_model
        [ "$was_loaded" = 0 ] || start_agent ;;
    raycast) install_raycast ;;
    karabiner) install_karabiner ;;
    uninstall)
        stop_agent
        rm -f "$plist" "$service/bin/haiku-rag-serve"
        rm -rf "$env"
        say "Removed the agent and the Python environment. Kept: the launcher (its Full Disk"
        say "Access grant), the configuration, folders, model, index and queue in $app,"
        say "the Raycast extension and the Karabiner rule." ;;
    *)
        sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2
        exit 64 ;;
esac
