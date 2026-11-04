#!/bin/sh
# Build the launcher and render the service's files into OUT, installing nothing.
#
#   service/build.sh --out DIR --bin DIR [--ane-server PATH] [--home DIR]
#                    [--app-dir DIR] [--cache-dir DIR] [--port N] [--label LABEL]
#                    [--ingester-port N] [--ingester-label LABEL]
#                    [--ingester-idle-exit SECONDS] [--embed-port N]
#                    [--embed-label LABEL] [--idle-exit SECONDS]
#
# --bin is the bin directory of the Python environment desktop/ installs
# (haiku-rag-macos, haiku-ingester, haiku-rag); --ingester PATH, naming its
# haiku-ingester, says the same. The rendered files name their installed paths
# under APP_DIR (default HOME/Library/Application Support/haiku.rag, HOME
# defaulting to $HOME). The ingester downloads document-conversion models under
# CACHE_DIR (default HOME/Library/Caches/haiku.rag), which the uninstaller's
# --purge deletes. OUT receives:
#
#   bin/haiku-rag-service        the launcher, installed to APP_DIR/service/bin
#   launcher.c.sha256            the launcher's source hash: replace an installed
#                                launcher only when it changes (Full Disk Access)
#   bin/haiku-rag-reader         what the launcher runs for the search service
#   bin/haiku-rag-ingester       what the launcher runs for the ingester
#   com.haiku-rag.service.plist  the search service's agent (LABEL, PORT)
#   com.haiku-rag.ingester.plist the ingester's agent (INGESTER_LABEL)
#   haiku.rag.yaml               the configuration, APP_DIR/haiku.rag.yaml
#
# and, with --ane-server (the haiku-rag-ane-server command of its environment):
#
#   com.haiku-rag.ane-server.plist  the embedding server's agent
set -eu

here=$(cd "$(dirname "$0")" && pwd)
out=
bin=
ane_server=
home=$HOME
app=
cache=
port=8765
label=com.haiku-rag.service
ingester_port=8766
ingester_label=com.haiku-rag.ingester
ingester_idle_exit=600
embed_port=58741
embed_label=com.haiku-rag.ane-server
idle_exit=300

while [ $# -gt 0 ]; do
    case "$1" in
        --out) out=$2; shift 2 ;;
        --bin) bin=$2; shift 2 ;;
        --ingester) bin=$(dirname "$2"); shift 2 ;;
        --ane-server) ane_server=$2; shift 2 ;;
        --home) home=$2; shift 2 ;;
        --app-dir) app=$2; shift 2 ;;
        --cache-dir) cache=$2; shift 2 ;;
        --port) port=$2; shift 2 ;;
        --label) label=$2; shift 2 ;;
        --ingester-port) ingester_port=$2; shift 2 ;;
        --ingester-label) ingester_label=$2; shift 2 ;;
        --ingester-idle-exit) ingester_idle_exit=$2; shift 2 ;;
        --embed-port) embed_port=$2; shift 2 ;;
        --embed-label) embed_label=$2; shift 2 ;;
        --idle-exit) idle_exit=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 64 ;;
    esac
done
[ -n "$out" ] || { echo "pass --out" >&2; exit 64; }
[ -n "$bin" ] || { echo "pass --bin" >&2; exit 64; }
[ -n "$app" ] || app="$home/Library/Application Support/haiku.rag"
[ -n "$cache" ] || cache="$home/Library/Caches/haiku.rag"

service="$app/service"
mkdir -p "$out/bin"
cc -O2 -Wall -o "$out/bin/haiku-rag-service" "$here/launcher.c"
shasum -a 256 "$here/launcher.c" | cut -d' ' -f1 > "$out/launcher.c.sha256"

escape() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
render() {
    sed -e "s|@LAUNCHER@|$(escape "$service/bin/haiku-rag-service")|g" \
        -e "s|@READER@|$(escape "$service/bin/haiku-rag-reader")|g" \
        -e "s|@INGESTER@|$(escape "$service/bin/haiku-rag-ingester")|g" \
        -e "s|@BIN@|$(escape "$bin")|g" \
        -e "s|@ANE_SERVER@|$(escape "$ane_server")|g" \
        -e "s|@ANE_DIR@|$(escape "$app/ane-server")|g" \
        -e "s|@CONFIG@|$(escape "$app/haiku.rag.yaml")|g" \
        -e "s|@FRAGMENTS@|$(escape "$app/databases.d")|g" \
        -e "s|@MODEL_DIR@|$(escape "$app/models/jina-nano-coreml")|g" \
        -e "s|@HOME@|$(escape "$home")|g" \
        -e "s|@CACHE_DIR@|$(escape "$cache")|g" \
        -e "s|@PORT@|$port|g" \
        -e "s|@LABEL@|$(escape "$label")|g" \
        -e "s|@INGESTER_PORT@|$ingester_port|g" \
        -e "s|@INGESTER_LABEL@|$(escape "$ingester_label")|g" \
        -e "s|@INGESTER_IDLE_EXIT@|$ingester_idle_exit|g" \
        -e "s|@EMBED_PORT@|$embed_port|g" \
        -e "s|@EMBED_LABEL@|$(escape "$embed_label")|g" \
        -e "s|@IDLE_EXIT@|$idle_exit|g" \
        -e "s|@STATE_DIR@|$(escape "$service")|g" \
        "$1" > "$2"
}
for script in haiku-rag-reader haiku-rag-ingester; do
    render "$here/$script.in" "$out/bin/$script"
    chmod 755 "$out/bin/$script"
done
render "$here/com.haiku-rag.service.plist.in" "$out/com.haiku-rag.service.plist"
render "$here/com.haiku-rag.ingester.plist.in" "$out/com.haiku-rag.ingester.plist"
render "$here/haiku.rag.yaml.in" "$out/haiku.rag.yaml"
plutil -lint "$out/com.haiku-rag.service.plist" "$out/com.haiku-rag.ingester.plist" >/dev/null
if [ -n "$ane_server" ]; then
    render "$here/com.haiku-rag.ane-server.plist.in" "$out/com.haiku-rag.ane-server.plist"
    plutil -lint "$out/com.haiku-rag.ane-server.plist" >/dev/null
fi

echo "Rendered into $out"
