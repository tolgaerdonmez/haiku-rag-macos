#!/bin/sh
# Install haiku.rag's local search on macOS: asks what to install, then installs it.
#
#   curl -fsSL https://raw.githubusercontent.com/tolgaerdonmez/haiku-rag-macos/main/install.sh | sh
#   ./install.sh [--yes] [--dry-run] [component options]
#   ./install.sh --uninstall [--yes] [--dry-run] [--delete-index] [--keep-model] [--purge]
#   ./install.sh --update [--ref REF] [--dry-run]   # snapshot, update, check, roll back on failure
#   ./install.sh --rollback [--yes]                 # back to the latest snapshot and its version
#
# Components (recommended ones are preselected):
#   --service / --no-service   background search service: launchd agent and launcher
#   --model / --no-model       embedding model and the server running it on the Neural Engine
#   --raycast / --no-raycast   Raycast extension (recommended when Raycast is installed)
#   --hotkey / --no-hotkey     Cmd+Shift+F through Karabiner-Elements (offered when installed)
#   --folder PATH              register PATH for indexing (repeatable)
#   --no-folders               do not ask for folders to index
# The model (downloaded from Hugging Face; this repository holds no conversion code):
#   --model-repo ID            Hugging Face model repository to download from
#   --model-revision REV       its branch, tag or commit (default main)
#   --model-sha256 HEX         checksum of the repository's SHA256SUMS
#   --model-from DIR           a model directory you have (model.mlpackage, tokenizer.json)
# Other:
#   -y, --yes                  ask nothing: install the recommended set
#   --dry-run                  check this Mac, show the menu (on a terminal, without
#                              --yes) and print the plan; change nothing. With
#                              --uninstall, list every path it would delete, with sizes
#   --uninstall                remove what this installer installs; the index is
#                              kept unless --delete-index, indexed files are never touched
#   --purge                    with --uninstall, also delete the caches: uv's packages and
#                              docling's models in ~/Library/Caches/haiku.rag, the
#                              Raycast extension's page previews, and the update snapshots
# Updating:
#   --update                   update what is installed to the latest version: snapshot the
#                              index, queue, folder list and configuration (an APFS clone,
#                              instant and taking no extra disk), fetch the version into the
#                              source checkout, install the components installed now, then
#                              check that the agents are loaded, no document went missing and
#                              a test search answers. If the install or the check fails, it
#                              restores the snapshot and reinstalls the version before
#                              (HAIKU_RAG_MACOS_REPO and HAIKU_RAG_MACOS_REF as below)
#   --ref REF                  with --update, the branch, tag or commit to update to
#   --rollback                 restore the latest snapshot and reinstall the version it was
#                              taken from. The last 2 snapshots are kept, in
#                              ~/Library/Application Support/haiku.rag/snapshots
#
# Run from a checkout it installs that checkout. Run on its own (piped to sh) it
# clones this repository, or updates the clone, in HAIKU_RAG_MACOS_SOURCE (default
# ~/Library/Application Support/haiku.rag/source) and runs the installer found
# there. HAIKU_RAG_MACOS_REPO and HAIKU_RAG_MACOS_REF pick another repository or
# branch, HAIKU_RAG_MODEL_REPO another model repository and HF_ENDPOINT another
# Hugging Face host. haiku.rag itself comes from PyPI at the version
# desktop/uv.lock pins. Prompts read the terminal, so they work when the script itself arrives on
# stdin. The menu is scripts/install-tui.py, run with uv; without uv, without a
# terminal it can draw on, or with HAIKU_RAG_TUI=0, a numbered menu asks instead.
set -eu

repo_url=${HAIKU_RAG_MACOS_REPO:-https://github.com/tolgaerdonmez/haiku-rag-macos.git}
repo_ref=${HAIKU_RAG_MACOS_REF:-main}
hf_endpoint=${HF_ENDPOINT:-https://huggingface.co}
default_model_repo=tolgaerdonmez/jina-embeddings-v5-omni-nano-coreml
# The SHA256SUMS of default_model_repo at main, written by the repository's packaging script.
default_model_sums=122dfdf9d6d4197477195b513c4bb0e2114b25533fc6c3a3ce983a17f4b79af7

[ -n "${HOME:-}" ] || { echo "haiku-rag-macos: HOME is not set" >&2; exit 1; }
app="$HOME/Library/Application Support/haiku.rag"
source_dir=${HAIKU_RAG_MACOS_SOURCE:-$app/source}
model="$app/models/jina-nano-coreml"
launcher="$app/service/bin/haiku-rag-service"
env="$app/service/env"
ane="$app/ane-server"
label=${HAIKU_RAG_SERVICE_LABEL:-com.haiku-rag.service}
port=${HAIKU_RAG_SERVICE_PORT:-8765}
plist="$HOME/Library/LaunchAgents/$label.plist"
ingester_label=${HAIKU_RAG_INGESTER_LABEL:-com.haiku-rag.ingester}
ingester_plist="$HOME/Library/LaunchAgents/$ingester_label.plist"
embed_label=${HAIKU_RAG_EMBED_LABEL:-com.haiku-rag.ane-server}
embed_plist="$HOME/Library/LaunchAgents/$embed_label.plist"
raycast_ext="$HOME/.config/raycast/extensions/haiku-rag"
# The Raycast extension's support directory: its rendered page previews.
raycast_support="$HOME/Library/Application Support/com.raycast.macos/extensions/haiku-rag"
# This install's own caches: uv's packages (here and in scripts/service.sh) and the
# models the service downloads (service/com.haiku-rag.service.plist.in). The caches
# in ~/.cache are shared with other tools, so --purge could not tell what is ours.
cache="$HOME/Library/Caches/haiku.rag"
# The version installed (see record_install) and the snapshots --update takes.
record="$app/installed.info"
snapshots="$app/snapshots"
UV_CACHE_DIR="$cache/uv"
export UV_CACHE_DIR
karabiner_json="$HOME/.config/karabiner/karabiner.json"
karabiner_rule="Cmd+Shift+F opens haiku.rag Search Documents in Raycast"
fda_pane="x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
excludes=".* *.json *.yaml *.yml"

say() { printf '%s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
die() { printf 'haiku-rag-macos: %s\n' "$*" >&2; exit 1; }
heading() { printf '\n%s\n' "$*"; }

# --- options -----------------------------------------------------------------

yes=0
dry_run=0
uninstall=0
delete_index=0
keep_model=0
purge=0
update=0
rollback=0
want_service=
want_model=
want_raycast=
want_hotkey=
want_folders=
want_fda=
folders=
folders_chosen=0
model_repo=${HAIKU_RAG_MODEL_REPO:-}
model_revision=main
model_sha256=
model_from=

# Folders travel newline-separated, so a path may hold spaces.
nl='
'
add_folder() { folders="$folders$1$nl"; }

parse_options() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -y | --yes) yes=1 ;;
            --dry-run) dry_run=1 ;;
            --uninstall) uninstall=1 ;;
            --delete-index) delete_index=1 ;;
            --keep-model) keep_model=1 ;;
            --purge) purge=1 ;;
            --update) update=1 ;;
            --rollback) rollback=1 ;;
            --ref)
                [ $# -ge 2 ] || die "--ref needs a branch, tag or commit"
                repo_ref=$2; shift ;;
            --service) want_service=1 ;;
            --no-service) want_service=0 ;;
            --model) want_model=1 ;;
            --no-model) want_model=0 ;;
            --raycast) want_raycast=1 ;;
            --no-raycast) want_raycast=0 ;;
            --hotkey) want_hotkey=1 ;;
            --no-hotkey) want_hotkey=0 ;;
            --folder)
                [ $# -ge 2 ] || die "--folder needs a path"
                add_folder "$2"; want_folders=1; shift ;;
            --no-folders) want_folders=0 ;;
            --model-repo)
                [ $# -ge 2 ] || die "--model-repo needs a repository id"
                model_repo=$2; shift ;;
            --model-revision)
                [ $# -ge 2 ] || die "--model-revision needs a revision"
                model_revision=$2; shift ;;
            --model-sha256)
                [ $# -ge 2 ] || die "--model-sha256 needs a checksum"
                model_sha256=$2; shift ;;
            --model-from)
                [ $# -ge 2 ] || die "--model-from needs a directory"
                model_from=$2; shift ;;
            -h | --help) usage 0 ;;
            *) say "haiku-rag-macos: unknown option: $1" >&2; usage 64 ;;
        esac
        shift
    done
}

usage() {
    if [ -f "${0:-}" ]; then
        sed -n '2,/^set -eu$/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' >&2
    else
        say "See the header of install.sh for its options." >&2
    fi
    exit "$1"
}

# --- the terminal ------------------------------------------------------------

interactive=0
choose_mode() {
    if [ "$yes" = 0 ] && { [ "$dry_run" = 0 ] || [ "$uninstall" = 0 ]; } && (: </dev/tty) 2>/dev/null; then
        interactive=1
    fi
}

# ask QUESTION y|n: the answer, or the default without a terminal.
ask() {
    if [ "$interactive" = 0 ]; then
        [ "$2" = y ]
        return
    fi
    if [ "$2" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    while :; do
        printf '%s %s ' "$1" "$hint" >/dev/tty
        reply=
        read -r reply </dev/tty || reply=
        case "$reply" in
            [Yy] | [Yy][Ee][Ss]) return 0 ;;
            [Nn] | [Nn][Oo]) return 1 ;;
            '') [ "$2" = y ]; return ;;
        esac
    done
}

# prompt QUESTION: a line read from the terminal, empty without one.
prompt() {
    if [ "$interactive" = 0 ]; then
        printf ''
        return
    fi
    printf '%s ' "$1" >/dev/tty
    reply=
    read -r reply </dev/tty || reply=
    printf '%s' "$reply"
}

# --- running standalone ------------------------------------------------------

check_platform() {
    [ "$(uname -s)" = Darwin ] || die "this installer is for macOS"
    macos=$(sw_vers -productVersion)
    [ "${macos%%.*}" -ge 15 ] || die "macOS 15 or later is needed (the Core ML model targets it); this is $macos"
}

check_command_line_tools() {
    if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
        say "The Xcode Command Line Tools (git and a C compiler) are missing."
        say "macOS opens their installer now; run this script again once it has finished."
        /usr/bin/xcode-select --install >/dev/null 2>&1 || true
        exit 1
    fi
    command -v git >/dev/null || die "git not found"
}

# Without a checkout around it the script fetches one and runs the copy there.
bootstrap() {
    check_platform
    check_command_line_tools
    say "haiku-rag-macos: fetching $repo_url ($repo_ref) into $source_dir"
    if [ -d "$source_dir/.git" ]; then
        [ -z "$(git -C "$source_dir" status --porcelain)" ] \
            || die "$source_dir has local changes; commit or remove them, or set HAIKU_RAG_MACOS_SOURCE"
        # An earlier installer cloned haiku.rag itself here: replace that clone.
        if [ "$(git -C "$source_dir" config --get remote.origin.url || true)" != "$repo_url" ] \
            && [ -f "$source_dir/haiku_rag_slim/pyproject.toml" ]; then
            say "  replacing the haiku.rag clone an earlier installer made there"
            rm -rf "$source_dir"
        fi
    fi
    if [ -d "$source_dir/.git" ]; then
        git -C "$source_dir" fetch --quiet --depth 1 "$repo_url" "$repo_ref"
        git -C "$source_dir" checkout --quiet --detach FETCH_HEAD
    else
        [ ! -e "$source_dir" ] || die "$source_dir exists and is not a git checkout"
        mkdir -p "$(dirname "$source_dir")"
        git clone --quiet --depth 1 --branch "$repo_ref" "$repo_url" "$source_dir"
    fi
    [ -f "$source_dir/install.sh" ] || die "$repo_url ($repo_ref) has no install.sh"
    HAIKU_RAG_MACOS_SOURCE=$source_dir
    export HAIKU_RAG_MACOS_SOURCE
    exec /bin/sh "$source_dir/install.sh" "$@"
}

parse_options "$@"
case "$update$rollback$uninstall" in
    000 | 100 | 010 | 001) ;;
    *) die "--update, --rollback and --uninstall go alone" ;;
esac

# Updating checks a version out over the source checkout, which may hold this very
# script while the shell still reads it: run a copy instead. The copy has no
# checkout around it, which updating does not need.
if [ "$update$rollback" != 00 ] && [ -z "${HAIKU_RAG_INSTALL_COPY:-}" ] && [ -f "${0:-}" ]; then
    HAIKU_RAG_INSTALL_COPY=$(mktemp "${TMPDIR:-/tmp}/haiku-rag-install.sh.XXXXXX")
    cp "$0" "$HAIKU_RAG_INSTALL_COPY"
    export HAIKU_RAG_INSTALL_COPY
    exec /bin/sh "$HAIKU_RAG_INSTALL_COPY" "$@"
fi

repo=
if [ -f "${0:-}" ] && [ -z "${HAIKU_RAG_INSTALL_COPY:-}" ]; then
    here=$(cd "$(dirname "$0")" && pwd)
    if [ -f "$here/scripts/service.sh" ] && [ -f "$here/service/build.sh" ]; then
        repo=$here
    fi
fi
[ -n "$repo" ] || [ "$update$rollback" != 00 ] || bootstrap "$@"
service_sh="$repo/scripts/service.sh"

# --- what is on this Mac -----------------------------------------------------

app_installed() {
    [ -d "/Applications/$1.app" ] || [ -d "$HOME/Applications/$1.app" ] \
        || [ -n "$(/usr/bin/mdfind "kMDItemCFBundleIdentifier == '$2'" 2>/dev/null | head -n 1)" ]
}

human_kb() {
    awk -v kb="$1" 'BEGIN {
        if (kb >= 1048576) printf "%.1f GB", kb / 1048576
        else if (kb >= 1024) printf "%d MB", kb / 1024
        else printf "%d KB", kb
    }'
}

has_raycast=0
has_karabiner=0
has_node=0
has_uv=0
apple_silicon=0
free_kb=0

detect() {
    [ "$(uname -m)" = arm64 ] && apple_silicon=1
    command -v uv >/dev/null && has_uv=1
    { command -v node && command -v npm; } >/dev/null 2>&1 && has_node=1
    app_installed Raycast com.raycast.macos && has_raycast=1
    { app_installed Karabiner-Elements org.pqrs.Karabiner-Elements.Settings || [ -f "$karabiner_json" ]; } \
        && has_karabiner=1
    free_kb=$(df -Pk "$HOME" 2>/dev/null | awk 'NR == 2 { print $4 }')
    free_kb=${free_kb:-0}
}

service_installed() { [ -f "$plist" ] && [ -x "$env/bin/haiku-rag-macos" ]; }
model_installed() { [ -e "$model/model.mlpackage" ] && [ -e "$model/tokenizer.json" ]; }
embedder_installed() { model_installed && [ -f "$embed_plist" ] && [ -x "$ane/env/bin/haiku-rag-ane-server" ]; }
raycast_installed() { [ -f "$raycast_ext/package.json" ]; }
hotkey_installed() { [ -f "$karabiner_json" ] && grep -qF "$karabiner_rule" "$karabiner_json"; }

preflight() {
    heading "Checks"
    if [ "$apple_silicon" = 1 ]; then
        note "ok  macOS $macos on Apple Silicon: embeddings run on the Neural Engine"
    else
        warn "macOS $macos on Intel: no Neural Engine, embeddings run on the CPU (slower)"
    fi
    note "ok  Command Line Tools and git"
    if [ "$has_uv" = 1 ]; then
        note "ok  uv $(uv --version 2>/dev/null | awk '{ print $2 }')"
    else
        warn "uv is missing: it builds the service's Python environment"
    fi
    if [ "$has_raycast" = 1 ]; then
        if [ "$has_node" = 1 ]; then
            note "ok  Raycast, Node.js $(node --version 2>/dev/null)"
        else
            warn "Raycast is installed but Node.js is not: the extension needs node and npm (https://nodejs.org)"
        fi
    else
        note "--  Raycast is not installed: no extension (https://raycast.com)"
    fi
    if [ "$has_karabiner" = 1 ]; then
        note "ok  Karabiner-Elements"
    else
        note "--  Karabiner-Elements is not installed: no Cmd+Shift+F hotkey"
    fi
    note "ok  $(human_kb "$free_kb") free"
}

ensure_uv() {
    [ "$has_uv" = 0 ] || return 0
    if ! ask "Install uv now with its official installer (https://astral.sh/uv)?" y; then
        die "uv is needed; install it (https://docs.astral.sh/uv) and run this again"
    fi
    /usr/bin/curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    command -v uv >/dev/null || die "uv did not install"
    has_uv=1
}

# --- choosing components -----------------------------------------------------

# Sizes in KB: the service's environment, the installed model and its download,
# the embedding server's environment, and the Raycast extension's working copy and build.
size_service=2000000
size_model=870000
size_model_download=430000
size_ane=120000
size_raycast=150000

available() {
    case "$1" in
        raycast) [ "$has_raycast" = 1 ] && [ "$has_node" = 1 ] ;;
        hotkey) [ "$has_karabiner" = 1 ] ;;
        *) true ;;
    esac
}

unavailable_reason() {
    case "$1" in
        raycast)
            if [ "$has_raycast" = 0 ]; then printf 'Raycast is not installed'; else printf 'needs Node.js'; fi ;;
        hotkey) printf 'Karabiner-Elements is not installed' ;;
    esac
}

get() { eval "printf '%s' \"\$want_$1\""; }
set_want() { eval "want_$1=\$2"; }

# Unset choices take the recommendation; an unavailable component is never chosen.
apply_defaults() {
    [ -n "$want_service" ] || want_service=1
    [ -n "$want_model" ] || want_model=1
    [ -n "$want_raycast" ] || want_raycast=$has_raycast
    if [ -z "$want_hotkey" ]; then
        if hotkey_installed; then want_hotkey=1; else want_hotkey=0; fi
    fi
    if [ -z "$want_folders" ]; then
        if [ "$interactive" = 1 ]; then want_folders=1; else want_folders=0; fi
    fi
    for c in raycast hotkey; do
        if [ "$(get $c)" = 1 ] && ! available $c; then
            warn "$c: $(unavailable_reason $c), skipped"
            set_want $c 0
        fi
    done
}

describe() {
    case "$1" in
        service) printf 'Background search service: launchd agent, launcher, Python env (%s)' "$(human_kb $size_service)" ;;
        model) printf 'Embedding model and its Neural Engine server (%s, %s download)' \
            "$(human_kb $((size_model + size_ane)))" "$(human_kb $size_model_download)" ;;
        raycast) printf 'Raycast extension: Search Documents, Manage Indexed Folders (%s)' "$(human_kb $size_raycast)" ;;
        hotkey) printf 'Cmd+Shift+F opens the search, through Karabiner (takes Cmd+Shift+F from other apps)' ;;
        folders) printf 'Folders to index, asked next' ;;
        fda) printf 'Afterwards, open System Settings to grant the service Full Disk Access (for ~/Documents, ~/Desktop, ~/Downloads, iCloud)' ;;
    esac
}

installed_mark() {
    case "$1" in
        service) service_installed && printf ' (installed)' ;;
        model) embedder_installed && printf ' (installed)' ;;
        raycast) raycast_installed && printf ' (installed)' ;;
        hotkey) hotkey_installed && printf ' (installed)' ;;
        folders) service_installed && printf ' (%s registered)' "$(registered_folders | grep -c . || true)" ;;
    esac
    return 0
}

components="service model raycast hotkey folders"

show_menu() {
    heading "Components"
    i=1
    for c in $components; do
        if ! available $c; then
            printf '  %d [-] %-8s %s\n' $i $c "$(unavailable_reason $c)"
        else
            if [ "$(get $c)" = 1 ]; then mark=x; else mark=' '; fi
            printf '  %d [%s] %-8s %s%s\n' $i "$mark" $c "$(describe $c)" "$(installed_mark $c)"
        fi
        i=$((i + 1))
    done
}

# --- the menu ----------------------------------------------------------------

# The facts the menu shows, as key=value lines (see scripts/install-tui.py).
tui_spec() {
    printf 'dry_run=%s\nhome=%s\nhf_endpoint=%s\n' "$dry_run" "$HOME" "$hf_endpoint"
    if [ -x "$launcher" ]; then fda_default=0; else fda_default=1; fi
    present=$(registered_folders)
    for c in $components fda; do
        if available $c; then a=1; r=; else a=0; r=$(unavailable_reason $c); fi
        if [ "$c" = fda ]; then checked=$fda_default; else checked=$(get $c); fi
        if [ "$c" = folders ]; then
            mark=
            if service_installed; then mark="$(printf '%s' "$present" | grep -c . || true) registered"; fi
        else
            mark=$(installed_mark $c | sed 's/^ (//; s/)$//')
        fi
        printf 'component=%s\t%s\t%s\t%s\t%s\t%s\n' "$c" "$checked" "$a" "$(describe $c)" "$r" "$mark"
    done
    if model_installed; then printf 'model.installed=1\n'; fi
    printf 'model.default_repo=%s\nmodel.download=%s\n' "$default_model_repo" "$(human_kb $size_model_download)"
    printf 'model.repo=%s\nmodel.revision=%s\nmodel.from=%s\n' "$model_repo" "$model_revision" "$model_from"
    printf '%s\n' "$present" | while IFS= read -r f; do [ -z "$f" ] || printf 'folder.registered=%s\n' "$f"; done
    printf '%s' "$folders" | while IFS= read -r f; do [ -z "$f" ] || printf 'folder.given=%s\n' "$f"; done
}

read_tui_result() {
    folders=
    folders_chosen=1
    while IFS= read -r line; do
        k=${line%%=*}
        v=${line#*=}
        case "$k=$v" in
            want.service=[01] | want.model=[01] | want.raycast=[01] | want.hotkey=[01] \
                | want.folders=[01] | want.fda=[01]) set_want "${k#want.}" "$v" ;;
            folder=?*) add_folder "$v" ;;
            model.repo=*) model_repo=$v ;;
            model.revision=?*) model_revision=$v ;;
            model.from=*) model_from=$v ;;
        esac
    done <"$work/tui.out"
}

# The menu: returns once a plan is confirmed, exits when it is cancelled, and
# fails when it cannot run.
run_tui() {
    [ "${HAIKU_RAG_TUI:-1}" != 0 ] || return 1
    tui="$repo/scripts/install-tui.py"
    [ -f "$tui" ] || return 1
    if [ "$has_uv" = 0 ]; then
        note "(uv is not installed yet, so a numbered menu asks)"
        return 1
    fi
    workdir
    tui_spec >"$work/tui.in"
    rm -f "$work/tui.out"
    # The terminal's own device: macOS cannot poll /dev/tty, which the menu needs.
    term=/dev/$(ps -o tty= -p $$ 2>/dev/null | tr -d ' ')
    [ -c "$term" ] || term=/dev/tty
    note "Opening the menu..."
    uv run --quiet --no-config --script "$tui" "$work/tui.in" "$work/tui.out" \
        <"$term" >"$term" 2>"$work/tui.log" || true
    case "$(sed -n 's/^action=//p' "$work/tui.out" 2>/dev/null)" in
        install) read_tui_result; return 0 ;;
        cancel) say "Cancelled: nothing changed."; exit 0 ;;
    esac
    warn "the menu did not start ($(tail -n 1 "$work/tui.log" 2>/dev/null)); a numbered menu asks instead"
    return 1
}

choose() {
    if [ "$interactive" = 1 ] && run_tui; then return 0; fi
    show_menu
    [ "$interactive" = 1 ] || return 0
    while ! ask "Install the checked components?" y; do
        line=$(prompt "Numbers to switch on or off (e.g. 3 4):")
        for n in $line; do
            case "$n" in
                *[!0-9]*) c= ;;
                *) c=$(printf '%s\n' $components | sed -n "${n}p") ;;
            esac
            if [ -z "$c" ]; then
                warn "no component $n"
            elif ! available "$c"; then
                warn "$c: $(unavailable_reason "$c")"
            elif [ "$(get "$c")" = 1 ]; then
                set_want "$c" 0
            else
                set_want "$c" 1
            fi
        done
        show_menu
    done
}

check_space() {
    need=0
    [ "$want_service" = 0 ] || service_installed || need=$((need + size_service))
    if [ "$want_model" = 1 ] && ! model_installed; then
        need=$((need + size_model + size_model_download))
    fi
    [ "$want_model" = 0 ] || embedder_installed || need=$((need + size_ane))
    [ "$want_raycast" = 0 ] || raycast_installed || need=$((need + size_raycast))
    [ "$need" -gt 0 ] || return 0
    note "Needs about $(human_kb $need) of disk, $(human_kb "$free_kb") free; the index grows with the documents."
    if [ "$need" -gt "$free_kb" ]; then
        ask "Not enough free space. Continue anyway?" n || die "not enough free space"
    fi
}

# --- the model ---------------------------------------------------------------

work=
cleanup() {
    status=$?
    [ -z "$work" ] || rm -rf "$work"
    [ -z "${HAIKU_RAG_INSTALL_COPY:-}" ] || rm -f "$HAIKU_RAG_INSTALL_COPY"
    if [ "$status" != 0 ] && [ -n "${updating:-}" ]; then
        printf '\nhaiku-rag-macos: the update stopped before it finished. The snapshot taken first is in\n  %s\nRestore it and the version before with: install.sh --rollback\n' "$updating" >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

workdir() {
    [ -n "$work" ] || work=$(mktemp -d "${TMPDIR:-/tmp}/haiku-rag-install.XXXXXX")
}

# download_model REPO REVISION: downloads the files SHA256SUMS lists, each
# verified, and prints the model directory. SHA256SUMS itself is checked against
# --model-sha256, or the pinned sum for the default repository at main.
download_model() {
    base="$hf_endpoint/$1/resolve/$2"
    want=$model_sha256
    if [ -z "$want" ] && [ "$1" = "$default_model_repo" ] && [ "$2" = main ]; then
        want=$default_model_sums
    fi
    dest="$work/downloaded"
    rm -rf "$dest"
    mkdir -p "$dest"
    note "Downloading $hf_endpoint/$1 ($2)" >&2
    /usr/bin/curl -fsSL -o "$dest/SHA256SUMS" "$base/SHA256SUMS" \
        || { warn "no SHA256SUMS at $base" >&2; return 1; }
    if [ -n "$want" ]; then
        have=$(shasum -a 256 "$dest/SHA256SUMS" | cut -d' ' -f1)
        [ "$have" = "$want" ] || { warn "SHA256SUMS checksum mismatch: expected $want, got $have" >&2; return 1; }
    else
        warn "no pinned checksum for $1 ($2): trusting its SHA256SUMS" >&2
    fi
    while read -r sum path; do
        case "$path" in
            model.mlpackage/* | tokenizer.json) ;;
            *) warn "SHA256SUMS lists an unexpected path: $path" >&2; return 1 ;;
        esac
        case "$path" in *..*) warn "SHA256SUMS lists an unsafe path: $path" >&2; return 1 ;; esac
        mkdir -p "$dest/$(dirname "$path")"
        note "  $path" >&2
        /usr/bin/curl -fL --progress-bar -o "$dest/$path" "$base/$path" || return 1
        [ "$(shasum -a 256 "$dest/$path" | cut -d' ' -f1)" = "$sum" ] \
            || { warn "checksum mismatch for $path" >&2; return 1; }
    done <"$dest/SHA256SUMS"
    [ -d "$dest/model.mlpackage" ] && [ -f "$dest/tokenizer.json" ] \
        || { warn "the repository holds no model.mlpackage with tokenizer.json" >&2; return 1; }
    rm -f "$dest/SHA256SUMS"
    printf '%s' "$dest"
}

# Leaves the model directory to install in model_dir, or nothing to do.
model_dir=
fetch_model() {
    workdir
    if [ -n "$model_from" ]; then
        model_dir=$(cd "$model_from" && pwd)
        return 0
    fi
    if model_installed && [ -z "$model_repo" ]; then
        note "Model: installed, kept"
        return 0
    fi
    repo_id=${model_repo:-$default_model_repo}
    if model_dir=$(download_model "$repo_id" "$model_revision"); then
        return 0
    fi
    model_dir=
    warn "The model could not be downloaded from $hf_endpoint/$repo_id."
    if [ "$interactive" = 1 ]; then
        line=$(prompt "A model folder you already have (model.mlpackage, tokenizer.json), or Return to stop:")
        if [ -n "$line" ]; then
            line=$(expand_path "$line")
            if [ -d "$line/model.mlpackage" ] && [ -f "$line/tokenizer.json" ]; then
                model_dir=$(cd "$line" && pwd)
                return 0
            fi
            warn "$line holds no model.mlpackage and tokenizer.json"
        fi
    fi
    die "no embedding model. Check the connection and run this again, or pass --model-from DIR (a folder with model.mlpackage and tokenizer.json, e.g. a download of $hf_endpoint/$default_model_repo)"
}

# --- the steps ---------------------------------------------------------------

run_service() { "$service_sh" "$@"; }

registered_folders() {
    [ -x "$env/bin/haiku-rag-macos" ] || return 0
    run_service folders list 2>/dev/null | awk -F '\t' 'NF >= 3 { print $3 }'
}

expand_path() {
    case "$1" in
        "~") printf '%s' "$HOME" ;;
        "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
        *) printf '%s' "$1" ;;
    esac
}

ask_folders() {
    [ "$interactive" = 1 ] && [ "$folders_chosen" = 0 ] || return 0
    heading "Folders to index"
    present=$(registered_folders)
    if [ -n "$present" ]; then
        note "Registered:"
        printf '%s\n' "$present" | sed 's/^/    /'
    elif [ -z "$folders" ] && ask "Index ~/Documents?" y; then
        add_folder "$HOME/Documents"
    fi
    while :; do
        line=$(prompt "Another folder to index (Return to finish):")
        [ -n "$line" ] || break
        add_folder "$(expand_path "$line")"
    done
}

register_folders() {
    [ -n "$folders" ] || return 0
    [ -x "$env/bin/haiku-rag-macos" ] || { warn "folders need the service; skipped"; return 0; }
    printf '%s' "$folders" | while IFS= read -r folder; do
        [ -n "$folder" ] || continue
        set --
        set -f
        for pattern in $excludes; do set -- "$@" --exclude "$pattern"; done
        set +f
        workdir
        if out=$(run_service folders add "$folder" "$@" 2>"$work/folder.err"); then
            note "Folder: $(printf '%s' "$out" | head -n 1 | awk -F '\t' '{ print $3 " (" $1 ")" }')"
        else
            warn "Folder $folder: $(sed 's/^haiku-rag-macos: //' "$work/folder.err" | tail -n 1)"
        fi
    done
}

# Raycast registers a local extension only while it runs.
start_raycast() {
    [ -n "$(/usr/bin/lsappinfo find bundleid=com.raycast.macos 2>/dev/null)" ] && return 0
    /usr/bin/open -g -b com.raycast.macos 2>/dev/null || return 0
    i=0
    while [ $i -lt 20 ] && [ -z "$(/usr/bin/lsappinfo find bundleid=com.raycast.macos 2>/dev/null)" ]; do
        sleep 1
        i=$((i + 1))
    done
}

install_steps() {
    if [ "$want_model" = 1 ]; then
        heading "Model"
        fetch_model
    fi
    [ "$want_raycast" = 0 ] || start_raycast
    if [ "$want_model" = 1 ]; then
        heading "Embedding server"
        if [ -n "$model_dir" ]; then
            run_service embedder --model-from "$model_dir"
        else
            run_service embedder
        fi
    fi
    if [ "$want_service" = 1 ]; then
        heading "Service"
        set -- install
        [ "$want_raycast" = 1 ] || set -- "$@" --no-raycast
        [ "$want_hotkey" = 1 ] || set -- "$@" --no-karabiner
        run_service "$@"
    else
        if [ "$want_raycast" = 1 ]; then heading "Raycast"; run_service raycast; fi
        if [ "$want_hotkey" = 1 ]; then heading "Hotkey"; run_service karabiner; fi
    fi
    if [ "$want_folders" = 1 ]; then
        if [ -x "$env/bin/haiku-rag-macos" ]; then
            ask_folders
            if [ -n "$folders" ]; then heading "Folders"; register_folders; fi
        else
            warn "folders: they need the service, which is not installed; skipped"
        fi
    fi
}

# --- after installing --------------------------------------------------------

api() {
    max=$1 path=$2
    shift 2
    /usr/bin/curl -fsS --max-time "$max" "$@" "http://127.0.0.1:$port$path"
}
field() { /usr/bin/plutil -extract "$1" raw -o - - 2>/dev/null; }

documents() {
    status=$(api 60 /status) || return 1
    n=0
    i=0
    while d=$(printf '%s' "$status" | field "databases.$i.documents"); do
        n=$((n + d))
        i=$((i + 1))
    done
    printf '%s' "$n"
}

full_disk_access() {
    heading "Full Disk Access"
    note "Folders under ~/Documents, ~/Desktop, ~/Downloads and iCloud Drive are readable"
    note "only once the service's launcher has Full Disk Access. Grant it once:"
    note "  1. System Settings > Privacy & Security > Full Disk Access"
    note "  2. Click +, press Cmd+Shift+G, paste the path below, press Return, click Open"
    note "     $launcher"
    note "  3. Turn its switch on (if it is listed already: off, then on again)"
    note "  4. $service_sh restart"
    [ "$interactive" = 1 ] || return 0
    if [ -n "$want_fda" ]; then
        open_fda=$want_fda
    elif ask "Open that System Settings pane, and the launcher in Finder, now?" "$1"; then
        open_fda=1
    else
        open_fda=0
    fi
    if [ "$open_fda" = 1 ]; then
        /usr/bin/open "$fda_pane" || true
        /usr/bin/open -R "$launcher" || true
        prompt "Press Return once the switch is on:" >/dev/null
        run_service restart && note "Service restarted"
    fi
}

verify() {
    heading "Check"
    if ! n=$(documents); then
        warn "the service does not answer on 127.0.0.1:$port; see $app/service/reader.log"
        return 0
    fi
    note "ok  the service answers on 127.0.0.1:$port, $n documents indexed"
    if [ "$n" -gt 0 ]; then
        hit=$(api 60 "/search?q=document&limit=1") || { warn "the test search failed"; return 0; }
        title=$(printf '%s' "$hit" | field hits.0.title) || title=
        path=$(printf '%s' "$hit" | field hits.0.path) || path=
        note "ok  test search: $title${path:+ ($path)}"
    elif [ -n "$(registered_folders)" ]; then
        note "Indexing the registered folders now; search from Raycast as documents arrive."
    fi
}

summary() {
    heading "Done"
    if service_installed; then
        note "Search:     Raycast > Search Documents, or curl 'http://127.0.0.1:$port/search?q=...'"
        note "Folders:    Raycast > Manage Indexed Folders, or $service_sh folders add DIR"
        note "Pause:      $service_sh pause    (resume: $service_sh resume)"
        note "Status:     $service_sh status    (log: $service_sh logs)"
    fi
    note "Update:     $repo/install.sh --update    (back again: --rollback)"
    note "Uninstall:  $repo/install.sh --uninstall"
    note "Docs:       https://github.com/tolgaerdonmez/haiku-rag-macos#readme"
}

install() {
    preflight
    apply_defaults
    choose
    check_space
    if [ "$dry_run" = 1 ]; then
        heading "Dry run: nothing changed"
        for c in $components; do note "$c: $(if [ "$(get $c)" = 1 ]; then printf install; else printf skip; fi)"; done
        if [ "$want_model" = 1 ]; then
            if [ -n "$model_from" ]; then
                note "model from: $model_from"
            elif model_installed && [ -z "$model_repo" ]; then
                note "model from: the installed model, kept"
            else
                note "model from: $hf_endpoint/${model_repo:-$default_model_repo} ($model_revision)"
            fi
        fi
        printf '%s' "$folders" | while IFS= read -r f; do [ -z "$f" ] || note "folder: $f"; done
        [ -z "$want_fda" ] || note "full disk access: $(if [ "$want_fda" = 1 ]; then printf 'open System Settings afterwards'; else printf skip; fi)"
        return 0
    fi
    if [ "$want_service" = 1 ] || [ "$want_model" = 1 ]; then
        ensure_uv
    fi
    launcher_before=$(shasum -a 256 "$launcher" 2>/dev/null || true)
    install_steps
    record_install
    if service_installed; then
        if [ "$launcher_before" = "$(shasum -a 256 "$launcher" 2>/dev/null || true)" ]; then
            full_disk_access n
        else
            full_disk_access y
        fi
        verify
    fi
    summary
}

# --- uninstalling ------------------------------------------------------------

remove_hotkey() {
    [ -f "$karabiner_json" ] && grep -qF "$karabiner_rule" "$karabiner_json" || return 0
    py="$env/bin/python"
    [ -x "$py" ] || py=/usr/bin/python3
    "$py" - "$karabiner_json" "$karabiner_rule" <<'EOF'
import json, os, sys, tempfile
path, description = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    config = json.load(f)
removed = 0
for profile in config.get("profiles", []):
    rules = profile.get("complex_modifications", {}).get("rules")
    if rules:
        kept = [r for r in rules if r.get("description") != description]
        removed += len(rules) - len(kept)
        profile["complex_modifications"]["rules"] = kept
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=4, ensure_ascii=False)
    f.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
print(f"  Karabiner: Cmd+Shift+F rule removed from {removed} profile(s)")
EOF
}

# The paths uninstalling deletes, one per line, as the options stand.
removal_paths() {
    printf '%s\n' "$plist" "$ingester_plist" "$embed_plist" "$env" "$app/service/haiku.rag" \
        "$ane" "$launcher" "$app/service/bin/.launcher.c.sha256" \
        "$app/service/bin/haiku-rag-serve" "$app/service/bin/haiku-rag-reader" \
        "$app/service/bin/haiku-rag-ingester" "$app/service/service.log" \
        "$app/service/reader.log" "$app/service/ingester.log" "$app/haiku.rag.yaml" \
        "$app/haiku.rag.yaml.previous" "$raycast_ext" "$app/raycast" "$record"
    [ "$keep_model" = 1 ] || printf '%s\n' "$model" "$model.previous" "$model.new"
    if [ "$delete_index" = 1 ]; then
        printf '%s\n' "$app/files.lancedb" "$app/ingester.db" "$app/ingester.db-shm" \
            "$app/ingester.db-wal" "$app/databases.d" "$app/folders.yaml" \
            "$app/folders.yaml.previous" "$app/.folders.yaml.lock"
    fi
    case "$repo" in
        "$app/source") printf '%s\n' "$app/source" ;;
    esac
    [ "$purge" = 0 ] || printf '%s\n' "$cache" "$raycast_support" "$snapshots"
}

# size_kb PATH...: the disk the existing ones take, in KB.
size_kb() {
    total=0
    for p in "$@"; do
        [ -e "$p" ] || [ -L "$p" ] || continue
        total=$((total + $(du -sk "$p" 2>/dev/null | awk '{ print $1 }')))
    done
    printf '%s' "$total"
}

dry_run_uninstall() {
    heading "Dry run: nothing changed. Uninstalling would delete:"
    removal_paths | while IFS= read -r p; do
        [ -e "$p" ] || [ -L "$p" ] || continue
        note "$(human_kb "$(size_kb "$p")")	$p"
    done
    if hotkey_installed; then note "and remove the Cmd+Shift+F rule from $karabiner_json"; fi
}

uninstall() {
    heading "Uninstall"
    note "Removes the agents, launcher, Python environments, embedding server, configuration,"
    note "Raycast extension, Cmd+Shift+F rule$( [ "$keep_model" = 1 ] || printf ', model') and the fetched sources in $app."
    [ "$purge" = 0 ] || note "With --purge also the caches in $cache, the Raycast page previews and the update snapshots."
    note "Indexed folders and their files are never touched."
    if [ "$interactive" = 1 ]; then
        ask "Uninstall?" y || exit 0
        [ "$delete_index" = 1 ] || ! ask "Also delete the index, the queue and the folder list?" n || delete_index=1
        [ "$keep_model" = 1 ] || ask "Also delete the embedding model ($(human_kb $size_model))?" y || keep_model=1
        [ "$purge" = 1 ] || ! ask "Also delete the caches: downloaded packages and document models, Raycast page previews, update snapshots ($(human_kb "$(size_kb "$cache" "$raycast_support" "$snapshots")"))?" n || purge=1
    fi
    [ "$dry_run" = 0 ] || { dry_run_uninstall; return 0; }

    remove_hotkey
    if [ -f "$plist" ] || [ -f "$ingester_plist" ] || [ -d "$env" ] || [ -f "$embed_plist" ] \
        || [ -d "$ane" ]; then
        run_service uninstall >/dev/null
        note "Agents, embedding server and Python environments removed"
    fi
    rm -f "$launcher" "$app/service/bin/.launcher.c.sha256" "$app/service/bin/haiku-rag-serve" \
        "$app/service/bin/haiku-rag-reader" "$app/service/bin/haiku-rag-ingester" \
        "$app/service/service.log" "$app/service/reader.log" "$app/service/ingester.log"
    rmdir "$app/service/bin" "$app/service" 2>/dev/null || true
    rm -f "$app/haiku.rag.yaml" "$app/haiku.rag.yaml.previous" "$record"
    if [ -e "$raycast_ext" ] || [ -e "$app/raycast" ]; then
        rm -rf "$raycast_ext" "$app/raycast"
        note "Raycast extension removed (if Raycast still lists it: Settings > Extensions > Haiku RAG > Remove)"
    fi
    if [ "$keep_model" = 0 ]; then
        rm -rf "$model" "$model.previous" "$model.new"
        rmdir "$app/models" 2>/dev/null || true
    fi
    if [ "$delete_index" = 1 ]; then
        rm -rf "$app/files.lancedb" "$app/databases.d"
        rm -f "$app/ingester.db" "$app/ingester.db-shm" "$app/ingester.db-wal" \
            "$app/folders.yaml" "$app/folders.yaml.previous" "$app/.folders.yaml.lock"
        note "Index, queue and folder list deleted"
    else
        note "Kept the index, the queue and the folder list in $app"
    fi
    case "$repo" in
        "$app/source") rm -rf "$app/source" ;;
    esac
    if [ "$purge" = 1 ]; then
        rm -rf "$cache" "$raycast_support" "$snapshots"
        note "Caches deleted: $cache, the Raycast page previews and the update snapshots"
    elif [ -d "$snapshots" ]; then
        note "Kept the update snapshots in $snapshots (--purge deletes them)"
    fi
    rmdir "$app" 2>/dev/null || true
    note "Remove haiku-rag-service from System Settings > Privacy & Security > Full Disk Access."
}

# --- updating ----------------------------------------------------------------

# record_install: notes the commit just installed, which --update rolls back to.
record_install() {
    commit=$(git -C "$repo" rev-parse --verify --quiet HEAD 2>/dev/null) || return 0
    dirty=0
    [ -z "$(git -C "$repo" status --porcelain --untracked-files=no 2>/dev/null)" ] || dirty=1
    write_record "$commit" "$repo" "$dirty"
}

# write_record COMMIT SOURCE [DIRTY]
write_record() {
    mkdir -p "$app"
    printf 'commit=%s\nsource=%s\ndirty=%s\ninstalled=%s\n' "$1" "$2" "${3:-0}" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$record.new"
    mv -f "$record.new" "$record"
}

# info_get FILE KEY: a key=value from a record or a snapshot's snapshot.info.
info_get() { sed -n "s/^$2=//p" "$1" 2>/dev/null | head -n 1; }

# The files a snapshot holds: the index, the queue, the folder register (the
# earlier folders.yaml and today's databases.d) and the configuration.
data_items="files.lancedb ingester.db ingester.db-wal ingester.db-shm folders.yaml
folders.yaml.previous .folders.yaml.lock databases.d haiku.rag.yaml haiku.rag.yaml.previous"
keep_snapshots=2

uid=$(id -u)
agent_loaded() { /bin/launchctl print "gui/$uid/$1" >/dev/null 2>&1; }

# Stops the search service and the ingester, which write the index and the queue,
# and waits for their processes to exit.
stop_data_agents() {
    for l in "$label" "$ingester_label"; do
        if agent_loaded "$l"; then /bin/launchctl bootout "gui/$uid/$l" 2>/dev/null || true; fi
    done
    i=0
    while { agent_loaded "$label" || agent_loaded "$ingester_label" \
        || /usr/bin/pgrep -f -- "$env/bin/" >/dev/null; } && [ $i -lt 150 ]; do
        sleep 1
        i=$((i + 1))
    done
    [ $i -lt 150 ] || die "the search service or the ingester did not stop within 150 s"
}

start_data_agents() {
    for p in "$plist" "$ingester_plist"; do
        l=$(basename "$p" .plist)
        [ -f "$p" ] && ! agent_loaded "$l" || continue
        /bin/launchctl bootstrap "gui/$uid" "$p" 2>/dev/null || warn "could not load $p"
    done
}

# Takes the agents out entirely, for a version that may not have them all.
remove_agents() {
    stop_data_agents
    if agent_loaded "$embed_label"; then /bin/launchctl bootout "gui/$uid/$embed_label" 2>/dev/null || true; fi
    rm -f "$plist" "$ingester_plist" "$embed_plist"
}

# snapshot COMMIT SOURCE DOCUMENTS: copies the data items into a new snapshot
# directory, an APFS clone where the disk allows it, and prints its path.
snapshot() {
    name=$(date +%Y%m%d-%H%M%S)
    dir="$snapshots/$name"
    n=1
    while [ -e "$dir" ]; do dir="$snapshots/$name-$n"; n=$((n + 1)); done
    mkdir -p "$dir.partial"
    for item in $data_items; do
        [ -e "$app/$item" ] || continue
        if ! /bin/cp -cpR "$app/$item" "$dir.partial/" 2>/dev/null; then
            rm -rf "${dir:?}.partial/$item"
            /bin/cp -pR "$app/$item" "$dir.partial/" \
                || { rm -rf "$dir.partial"; die "could not copy $app/$item into the snapshot"; }
        fi
    done
    printf 'commit=%s\nsource=%s\ndocuments=%s\ntaken=%s\n' "$1" "$2" "$3" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$dir.partial/snapshot.info"
    mv "$dir.partial" "$dir"
    printf '%s' "$dir"
}

# Keeps the newest keep_snapshots snapshots.
prune_snapshots() {
    [ -d "$snapshots" ] || return 0
    rm -rf "$snapshots"/*.partial
    ls -1 "$snapshots" | LC_ALL=C sort -r | sed "1,${keep_snapshots}d" | while IFS= read -r old; do
        [ -n "$old" ] || continue
        rm -rf "${snapshots:?}/$old"
        note "Snapshot $old deleted (the last $keep_snapshots are kept)"
    done
}

latest_snapshot() {
    [ -d "$snapshots" ] || return 1
    last=$(ls -1 "$snapshots" | grep -v '\.partial$' | LC_ALL=C sort | tail -n 1)
    [ -n "$last" ] && [ -f "$snapshots/$last/snapshot.info" ] || return 1
    printf '%s' "$snapshots/$last"
}

# restore SNAPSHOT: the data items exactly as the snapshot holds them; items it
# does not hold (a later version's) are removed. The agents must be stopped.
restore() {
    for item in $data_items; do
        rm -rf "${app:?}/$item"
        [ -e "$1/$item" ] || continue
        /bin/cp -cpR "$1/$item" "$app/" 2>/dev/null || /bin/cp -pR "$1/$item" "$app/" \
            || die "could not restore $item from $1"
    done
}

# The source checkout --update installs from, made ready to fetch into.
prepare_source() {
    if [ -d "$source_dir/.git" ]; then
        [ -z "$(git -C "$source_dir" status --porcelain)" ] \
            || die "$source_dir has local changes; commit or remove them, or set HAIKU_RAG_MACOS_SOURCE"
        if [ -f "$source_dir/haiku_rag_slim/pyproject.toml" ]; then
            say "  replacing the haiku.rag clone an earlier installer made there"
            rm -rf "$source_dir"
        fi
    fi
    if [ ! -d "$source_dir/.git" ]; then
        [ ! -e "$source_dir" ] || die "$source_dir exists and is not a git checkout"
        mkdir -p "$source_dir"
        git init --quiet "$source_dir"
    fi
}

# fetch URL REF: fetches REF (a branch, tag or commit) and prints its commit.
fetch() {
    git -C "$source_dir" fetch --quiet --depth 1 "$1" "$2" </dev/null || return 1
    git -C "$source_dir" rev-parse FETCH_HEAD
}

# checkout_commit COMMIT SOURCE: checks COMMIT out in the source checkout,
# fetching it from SOURCE (a checkout it came from) or the repository if needed.
checkout_commit() {
    if ! git -C "$source_dir" cat-file -e "$1^{commit}" 2>/dev/null; then
        if [ -n "$2" ] && [ "$2" != "$source_dir" ] && [ -d "$2/.git" ]; then
            fetch "$2" "$1" >/dev/null 2>&1 || true
        fi
        git -C "$source_dir" cat-file -e "$1^{commit}" 2>/dev/null \
            || fetch "$repo_url" "$1" >/dev/null || return 1
    fi
    git -C "$source_dir" checkout --quiet --detach "$1"
    # Keeps the commit installed reachable, so a later rollback finds it here.
    git -C "$source_dir" update-ref refs/haiku-rag/installed "$1"
}

# The install options that reinstall the components installed now.
component_options() {
    if [ -f "$plist" ]; then printf ' --service'; else printf ' --no-service'; fi
    if model_installed; then printf ' --model'; else printf ' --no-model'; fi
    if raycast_installed; then printf ' --raycast'; else printf ' --no-raycast'; fi
    if hotkey_installed; then printf ' --hotkey'; else printf ' --no-hotkey'; fi
}

# run_installer OPTIONS: the installer of the source checkout, without questions.
run_installer() {
    # shellcheck disable=SC2086
    env -u HAIKU_RAG_INSTALL_COPY HAIKU_RAG_MACOS_SOURCE="$source_dir" \
        /bin/sh "$source_dir/install.sh" --yes --no-folders $1 </dev/null
}

# check_install SERVICE DOCUMENTS: the agents installed are loaded, the service
# answers with at least DOCUMENTS documents (when known) and a test search answers.
check_install() {
    heading "Check"
    failed=
    for l in "$label:$plist" "$ingester_label:$ingester_plist" "$embed_label:$embed_plist"; do
        [ -f "${l#*:}" ] || continue
        if agent_loaded "${l%%:*}"; then note "ok  ${l%%:*} loaded"; else failed="${l%%:*} is not loaded"; fi
    done
    [ -z "$failed" ] || { warn "$failed"; return 1; }
    [ "$1" = 1 ] || return 0
    [ -f "$plist" ] || { warn "the search service's agent is not installed"; return 1; }
    i=0
    until n=$(documents); do
        i=$((i + 1))
        [ $i -lt 3 ] || { warn "the service does not answer on 127.0.0.1:$port"; return 1; }
        sleep 5
    done
    if [ -n "$2" ] && [ "$n" -lt "$2" ]; then
        warn "$n documents indexed, $2 before: documents went missing"
        return 1
    fi
    note "ok  the service answers on 127.0.0.1:$port, $n documents indexed${2:+ ($2 before)}"
    [ "$n" -gt 0 ] || return 0
    hit=$(api 120 "/search?q=document&limit=1") && title=$(printf '%s' "$hit" | field hits.0.title) \
        || { warn "the test search failed"; return 1; }
    note "ok  test search: $title"
}

# reinstall_snapshot SNAPSHOT: back to the snapshot's data and the version it was
# taken from. Fails when that version is unknown or does not install and check.
reinstall_snapshot() {
    commit=$(info_get "$1/snapshot.info" commit)
    from=$(info_get "$1/snapshot.info" source)
    documents_then=$(info_get "$1/snapshot.info" documents)
    remove_agents
    restore "$1"
    note "Restored the index, queue, folder list and configuration from $1"
    if [ -z "$commit" ]; then
        warn "the snapshot does not say which version it was taken from; the agents stay"
        warn "removed so nothing writes to the restored index. Install the version you had"
        warn "from its checkout (./install.sh), or update again."
        return 1
    fi
    prepare_source
    checkout_commit "$commit" "$from" || { warn "could not fetch $commit from $from or $repo_url"; return 1; }
    note "Reinstalling $(git -C "$source_dir" log -1 --format='%h %s' "$commit")"
    run_installer "$2" || { warn "the installer of $commit failed"; return 1; }
    check_install "$3" "$documents_then" || return 1
    write_record "$commit" "$source_dir"
}

# installed_version: the commit installed now and the checkout it came from, in
# prev_commit and prev_source; from the source checkout for an install made
# before installs were recorded.
installed_version() {
    prev_commit=$(info_get "$record" commit)
    prev_source=$(info_get "$record" source)
    if [ -z "$prev_commit" ] && [ -f "$source_dir/install.sh" ] && [ -f "$source_dir/scripts/service.sh" ]; then
        prev_commit=$(git -C "$source_dir" rev-parse --verify --quiet HEAD 2>/dev/null || true)
        prev_source=$source_dir
    fi
}

update() {
    heading "Update"
    [ -f "$plist" ] || model_installed || die "nothing is installed in $app; install with install.sh first"
    options=$(component_options)
    has_service=0
    [ ! -f "$plist" ] || has_service=1
    installed_version
    note "Installed: ${prev_commit:-unknown version}${prev_source:+ (from $prev_source)}"
    if [ "$dry_run" = 1 ]; then
        target=$(git ls-remote "$repo_url" "$repo_ref" </dev/null 2>/dev/null | head -n 1 | cut -f1)
        note "Update to: $repo_url $repo_ref${target:+ ($target)}"
        note "Snapshot:  $(printf '%s ' $data_items | sed 's/ $//') into $snapshots"
        note "Install:  $options"
        heading "Dry run: nothing changed"
        return 0
    fi
    prepare_source
    target=$(fetch "$repo_url" "$repo_ref") || die "could not fetch $repo_ref from $repo_url"
    if [ "$target" = "$prev_commit" ]; then
        note "Already at $repo_ref ($(git -C "$source_dir" log -1 --format='%h %s' "$target")): nothing to update"
        return 0
    fi
    note "Update to: $repo_url $repo_ref: $(git -C "$source_dir" log -1 --format='%h %s' "$target")"
    [ -n "$prev_commit" ] || warn "no record of the version installed: if the update fails, only the data can be restored"
    before=
    [ "$has_service" = 0 ] || before=$(documents) || before=
    stop_data_agents
    updating=$(snapshot "$prev_commit" "$prev_source" "$before")
    note "Snapshot: $updating"
    prune_snapshots
    checkout_commit "$target" "" || die "could not check out $target"
    heading "Installing $(git -C "$source_dir" log -1 --format='%h' "$target")"
    reason=
    if ! run_installer "$options"; then
        reason="the installer failed"
    elif ! check_install "$has_service" "$before"; then
        reason="the check after installing failed"
    fi
    if [ -z "$reason" ]; then
        write_record "$target" "$source_dir"
        updating=
        heading "Updated"
        note "Now at $(git -C "$source_dir" log -1 --format='%h %s' "$target")"
        note "Snapshot of the data before: $snapshots (install.sh --rollback goes back)"
        return 0
    fi
    snap=$updating
    updating=
    heading "Update FAILED: $reason. Rolling back"
    if reinstall_snapshot "$snap" "$options" "$has_service"; then
        heading "Rolled back"
        note "The update to $repo_ref failed ($reason); the version before ($commit) is"
        note "installed again with the data as it was before the update."
    else
        heading "Rollback FAILED"
        note "The update to $repo_ref failed ($reason) and so did the rollback. The data as it"
        note "was before the update is restored from $snap."
    fi
    exit 1
}

rollback() {
    heading "Rollback"
    snap=$(latest_snapshot) || die "no snapshot in $snapshots: --update takes one before it updates"
    commit=$(info_get "$snap/snapshot.info" commit)
    note "Snapshot: $snap"
    note "  taken $(info_get "$snap/snapshot.info" taken) from ${commit:-an unknown version}"
    note "Restores the index, queue, folder list and configuration it holds, replacing"
    note "the current ones, and reinstalls that version."
    options=$(component_options)
    has_service=0
    [ ! -f "$plist" ] || has_service=1
    [ "$dry_run" = 0 ] || { heading "Dry run: nothing changed"; return 0; }
    [ "$interactive" = 0 ] || ask "Roll back?" y || exit 0
    if reinstall_snapshot "$snap" "$options" "$has_service"; then
        heading "Rolled back to $commit"
    else
        heading "Rollback FAILED"
        note "The data is restored from $snap; see the messages above."
        exit 1
    fi
}

# --- main --------------------------------------------------------------------

choose_mode
check_platform
check_command_line_tools
if [ "$update" = 1 ]; then
    detect
    update
elif [ "$rollback" = 1 ]; then
    detect
    rollback
elif [ "$uninstall" = 1 ]; then
    say "haiku.rag for macOS, from $repo"
    uninstall
else
    say "haiku.rag for macOS, from $repo"
    detect
    install
fi
