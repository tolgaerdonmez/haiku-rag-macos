#!/bin/sh
# Build the launcher and render the service's files into OUT, installing nothing.
#
#   service/macos/build.sh --out DIR --ingester PATH [--home DIR] [--app-dir DIR] [--port N]
#                          [--label LABEL]
#
# The rendered files name their installed paths under APP_DIR (default
# HOME/Library/Application Support/haiku.rag, HOME defaulting to $HOME). OUT
# receives:
#
#   bin/haiku-rag-service       the launcher, installed to APP_DIR/service/bin
#   launcher.c.sha256           the launcher's source hash: replace an installed
#                               launcher only when it changes (Full Disk Access)
#   bin/haiku-rag-serve         the command the launcher runs, APP_DIR/service/bin
#   com.haiku-rag.service.plist the agent, for ~/Library/LaunchAgents
#   haiku.rag.yaml              the configuration, APP_DIR/haiku.rag.yaml
#   folders.yaml                the empty folder register, installed only if missing
set -eu

here=$(cd "$(dirname "$0")" && pwd)
out=
ingester=
home=$HOME
app=
port=8765
label=com.haiku-rag.service

while [ $# -gt 0 ]; do
    case "$1" in
        --out) out=$2; shift 2 ;;
        --ingester) ingester=$2; shift 2 ;;
        --home) home=$2; shift 2 ;;
        --app-dir) app=$2; shift 2 ;;
        --port) port=$2; shift 2 ;;
        --label) label=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 64 ;;
    esac
done
[ -n "$out" ] || { echo "pass --out" >&2; exit 64; }
[ -n "$ingester" ] || { echo "pass --ingester" >&2; exit 64; }
[ -n "$app" ] || app="$home/Library/Application Support/haiku.rag"

service="$app/service"
mkdir -p "$out/bin"
cc -O2 -Wall -o "$out/bin/haiku-rag-service" "$here/launcher.c"
shasum -a 256 "$here/launcher.c" | cut -d' ' -f1 > "$out/launcher.c.sha256"

escape() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
render() {
    sed -e "s|@LAUNCHER@|$(escape "$service/bin/haiku-rag-service")|g" \
        -e "s|@SERVE@|$(escape "$service/bin/haiku-rag-serve")|g" \
        -e "s|@INGESTER@|$(escape "$ingester")|g" \
        -e "s|@CONFIG@|$(escape "$app/haiku.rag.yaml")|g" \
        -e "s|@MODEL_DIR@|$(escape "$app/models/jina-nano-coreml")|g" \
        -e "s|@HOME@|$(escape "$home")|g" \
        -e "s|@PORT@|$port|g" \
        -e "s|@LABEL@|$(escape "$label")|g" \
        -e "s|@STATE_DIR@|$(escape "$service")|g" \
        "$1" > "$2"
}
render "$here/haiku-rag-serve.in" "$out/bin/haiku-rag-serve"
chmod 755 "$out/bin/haiku-rag-serve"
render "$here/com.haiku-rag.service.plist.in" "$out/com.haiku-rag.service.plist"
plutil -lint "$out/com.haiku-rag.service.plist" >/dev/null
cp "$here/haiku.rag.yaml" "$here/folders.yaml" "$out/"

echo "Rendered into $out"
