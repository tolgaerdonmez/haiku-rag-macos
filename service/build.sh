#!/bin/sh
# Build the launcher and render the service's files into OUT, installing nothing.
#
#   service/build.sh --out DIR --ingester PATH [--ane-server PATH] [--home DIR]
#                    [--app-dir DIR] [--cache-dir DIR] [--port N] [--label LABEL]
#                    [--embed-port N] [--embed-label LABEL] [--idle-exit SECONDS]
#
# The rendered files name their installed paths under APP_DIR (default
# HOME/Library/Application Support/haiku.rag, HOME defaulting to $HOME). The
# service downloads document-conversion models under CACHE_DIR (default
# HOME/Library/Caches/haiku.rag), which the uninstaller's --purge deletes. OUT
# receives:
#
#   bin/haiku-rag-service       the launcher, installed to APP_DIR/service/bin
#   launcher.c.sha256           the launcher's source hash: replace an installed
#                               launcher only when it changes (Full Disk Access)
#   bin/haiku-rag-serve         the command the launcher runs, APP_DIR/service/bin
#   com.haiku-rag.service.plist the search service's agent, for ~/Library/LaunchAgents
#   haiku.rag.yaml              the configuration, APP_DIR/haiku.rag.yaml
#   folders.yaml                the empty folder register, installed only if missing
#
# and, with --ane-server (the haiku-rag-ane-server command of its environment):
#
#   com.haiku-rag.ane-server.plist  the embedding server's agent
set -eu

here=$(cd "$(dirname "$0")" && pwd)
out=
ingester=
ane_server=
home=$HOME
app=
cache=
port=8765
label=com.haiku-rag.service
embed_port=58741
embed_label=com.haiku-rag.ane-server
idle_exit=300

while [ $# -gt 0 ]; do
    case "$1" in
        --out) out=$2; shift 2 ;;
        --ingester) ingester=$2; shift 2 ;;
        --ane-server) ane_server=$2; shift 2 ;;
        --home) home=$2; shift 2 ;;
        --app-dir) app=$2; shift 2 ;;
        --cache-dir) cache=$2; shift 2 ;;
        --port) port=$2; shift 2 ;;
        --label) label=$2; shift 2 ;;
        --embed-port) embed_port=$2; shift 2 ;;
        --embed-label) embed_label=$2; shift 2 ;;
        --idle-exit) idle_exit=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 64 ;;
    esac
done
[ -n "$out" ] || { echo "pass --out" >&2; exit 64; }
[ -n "$ingester" ] || { echo "pass --ingester" >&2; exit 64; }
[ -n "$app" ] || app="$home/Library/Application Support/haiku.rag"
[ -n "$cache" ] || cache="$home/Library/Caches/haiku.rag"

service="$app/service"
mkdir -p "$out/bin"
cc -O2 -Wall -o "$out/bin/haiku-rag-service" "$here/launcher.c"
shasum -a 256 "$here/launcher.c" | cut -d' ' -f1 > "$out/launcher.c.sha256"

escape() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
render() {
    sed -e "s|@LAUNCHER@|$(escape "$service/bin/haiku-rag-service")|g" \
        -e "s|@SERVE@|$(escape "$service/bin/haiku-rag-serve")|g" \
        -e "s|@INGESTER@|$(escape "$ingester")|g" \
        -e "s|@ANE_SERVER@|$(escape "$ane_server")|g" \
        -e "s|@ANE_DIR@|$(escape "$app/ane-server")|g" \
        -e "s|@CONFIG@|$(escape "$app/haiku.rag.yaml")|g" \
        -e "s|@MODEL_DIR@|$(escape "$app/models/jina-nano-coreml")|g" \
        -e "s|@HOME@|$(escape "$home")|g" \
        -e "s|@CACHE_DIR@|$(escape "$cache")|g" \
        -e "s|@PORT@|$port|g" \
        -e "s|@LABEL@|$(escape "$label")|g" \
        -e "s|@EMBED_PORT@|$embed_port|g" \
        -e "s|@EMBED_LABEL@|$(escape "$embed_label")|g" \
        -e "s|@IDLE_EXIT@|$idle_exit|g" \
        -e "s|@STATE_DIR@|$(escape "$service")|g" \
        "$1" > "$2"
}
render "$here/haiku-rag-serve.in" "$out/bin/haiku-rag-serve"
chmod 755 "$out/bin/haiku-rag-serve"
render "$here/com.haiku-rag.service.plist.in" "$out/com.haiku-rag.service.plist"
plutil -lint "$out/com.haiku-rag.service.plist" >/dev/null
render "$here/haiku.rag.yaml.in" "$out/haiku.rag.yaml"
cp "$here/folders.yaml" "$out/"
if [ -n "$ane_server" ]; then
    render "$here/com.haiku-rag.ane-server.plist.in" "$out/com.haiku-rag.ane-server.plist"
    plutil -lint "$out/com.haiku-rag.ane-server.plist" >/dev/null
fi

echo "Rendered into $out"
