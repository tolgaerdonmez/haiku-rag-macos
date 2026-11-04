"""install.sh --update and --rollback, against a repository of fake versions.

Each fake version's installer loads a stand-in search service under the test
label: a small HTTP server answering /status and /search from a document count
kept in the index directory, so a snapshot and its restore carry the count.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from test_install import TEST_ENV, app_dir, macos_only, run

SERVER = '''
import http.server, json, pathlib, sys
app, port = pathlib.Path(sys.argv[1]), int(sys.argv[2])
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        n = int((app / "files.lancedb" / "docs").read_text())
        if self.path.startswith("/status"):
            body = {"databases": [{"name": "files", "documents": n}]}
        else:
            body = {"hits": [{"title": "notes", "path": "/notes.md"}] if n else []}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''

# A version's installer: loads the stand-in service, then runs EXTRA.
INSTALLER = """#!/bin/sh
set -eu
app="$HOME/Library/Application Support/haiku.rag"
label=$HAIKU_RAG_SERVICE_LABEL
plist="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$app/files.lancedb" "$HOME/Library/LaunchAgents"
/bin/launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
[ -f "$app/files.lancedb/docs" ] || echo 3 >"$app/files.lancedb/docs"
echo @VERSION@ >"$app/version"
cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>$label</string>
<key>ProgramArguments</key><array>
<string>$FAKE_PYTHON</string><string>$FAKE_SERVER</string>
<string>$app</string><string>$HAIKU_RAG_SERVICE_PORT</string>
</array>
<key>RunAtLoad</key><true/>
</dict></plist>
EOF
@EXTRA@
/bin/launchctl bootstrap "gui/$(id -u)" "$plist"
sleep 1
"""

VERSIONS = {
    "v1": "",
    # The index format changes: an older version could not read it.
    "v2": 'touch "$app/files.lancedb/written-by-v2"',
    "v3": 'touch "$app/files.lancedb/written-by-v3"',
    # Loses documents, though it installs.
    "drop": 'touch "$app/files.lancedb/written-by-drop"; echo 1 >"$app/files.lancedb/docs"',
    # Damages the index and fails.
    "broken": 'echo 0 >"$app/files.lancedb/docs"; echo "install failed" >&2; exit 1',
}


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def versions(tmp_path: Path) -> tuple[str, dict[str, str]]:
    """A repository with a branch per version, and the commit of each."""
    repo = tmp_path / "versions"
    repo.mkdir()
    git("init", "--quiet", "--initial-branch", "v1", cwd=repo)
    git("config", "user.email", "test@example.com", cwd=repo)
    git("config", "user.name", "test", cwd=repo)
    commits = {}
    for name, extra in VERSIONS.items():
        if name != "v1":
            git("checkout", "--quiet", "-B", name, "v1", cwd=repo)
        (repo / "scripts").mkdir(exist_ok=True)
        (repo / "service").mkdir(exist_ok=True)
        (repo / "scripts" / "service.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (repo / "service" / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        installer = INSTALLER.replace("@VERSION@", name).replace("@EXTRA@", extra)
        (repo / "install.sh").write_text(installer, encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "--quiet", "-m", name, cwd=repo)
        commits[name] = git("rev-parse", "HEAD", cwd=repo)
    git("checkout", "--quiet", "v1", cwd=repo)
    return repo.as_uri(), commits


@pytest.fixture
def unload_test_agents():
    yield
    uid = os.getuid()
    for key in ("HAIKU_RAG_SERVICE_LABEL", "HAIKU_RAG_INGESTER_LABEL", "HAIKU_RAG_EMBED_LABEL"):
        subprocess.run(
            ["/bin/launchctl", "bootout", f"gui/{uid}/{TEST_ENV[key]}"],
            capture_output=True,
        )


@pytest.fixture
def installed(tmp_path: Path, versions, unload_test_agents):
    """v1, installed the way the installer before --update did: a shallow clone
    in the source directory, no record of the version installed."""
    url, commits = versions
    server = tmp_path / "server.py"
    server.write_text(textwrap.dedent(SERVER), encoding="utf-8")
    home = tmp_path / "home"
    source = app_dir(home) / "source"
    source.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--depth", "1", "--branch", "v1", url, str(source)],
        check=True,
    )
    env = {"FAKE_PYTHON": sys.executable, "FAKE_SERVER": str(server), "HAIKU_RAG_MACOS_REPO": url}
    subprocess.run(
        ["/bin/sh", str(source / "install.sh")],
        env={**os.environ, "HOME": str(home), **TEST_ENV, **env},
        check=True,
    )
    for name, text in (
        ("ingester.db", "queue"),
        ("folders.yaml", "folders: []\n"),
        ("haiku.rag.yaml", "v1 config\n"),
    ):
        (app_dir(home) / name).write_text(text, encoding="utf-8")
    return home, env, commits


def update(home: Path, env: dict[str, str], *args: str):
    return run(home, "--update", *args, **env)


def snapshots(home: Path) -> list[Path]:
    root = app_dir(home) / "snapshots"
    return sorted(root.iterdir()) if root.exists() else []


def info(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def state(home: Path) -> tuple[str, str, list[str]]:
    app = app_dir(home)
    return (
        (app / "version").read_text(encoding="utf-8").strip(),
        (app / "files.lancedb" / "docs").read_text(encoding="utf-8").strip(),
        sorted(p.name for p in (app / "files.lancedb").iterdir()),
    )


@macos_only
@pytest.mark.integration
def test_update_snapshots_installs_checks_and_records(installed):
    home, env, commits = installed

    result = update(home, env, "--ref", "v2")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok  the service answers on 127.0.0.1:18799, 3 documents indexed (3 before)" in result.stdout
    assert "ok  test search: notes" in result.stdout
    assert state(home) == ("v2", "3", ["docs", "written-by-v2"])
    assert info(app_dir(home) / "installed.info")["commit"] == commits["v2"]
    [snap] = snapshots(home)
    taken = info(snap / "snapshot.info")
    assert taken["commit"] == commits["v1"]
    assert taken["documents"] == "3"
    assert sorted(p.name for p in snap.iterdir()) == [
        "files.lancedb", "folders.yaml", "haiku.rag.yaml", "ingester.db", "snapshot.info",
    ]  # fmt: skip
    assert sorted(p.name for p in (snap / "files.lancedb").iterdir()) == ["docs"]

    again = update(home, env, "--ref", "v2")
    assert again.returncode == 0
    assert "nothing to update" in again.stdout
    assert len(snapshots(home)) == 1


@macos_only
@pytest.mark.integration
def test_update_rolls_back_when_documents_go_missing(installed):
    home, env, commits = installed

    result = update(home, env, "--ref", "drop")

    assert result.returncode == 1
    assert "1 documents indexed, 3 before: documents went missing" in result.stdout
    assert "Update FAILED: the check after installing failed. Rolling back" in result.stdout
    assert "Rolled back" in result.stdout
    assert state(home) == ("v1", "3", ["docs"])
    assert info(app_dir(home) / "installed.info")["commit"] == commits["v1"]
    assert (app_dir(home) / "haiku.rag.yaml").read_text(encoding="utf-8") == "v1 config\n"


@macos_only
@pytest.mark.integration
def test_update_rolls_back_when_the_installer_fails(installed):
    home, env, commits = installed

    result = update(home, env, "--ref", "broken")

    assert result.returncode == 1
    assert "Update FAILED: the installer failed. Rolling back" in result.stdout
    assert state(home) == ("v1", "3", ["docs"])
    assert info(app_dir(home) / "installed.info")["commit"] == commits["v1"]


@macos_only
@pytest.mark.integration
def test_rollback_restores_the_latest_snapshot_and_its_version(installed):
    home, env, commits = installed
    assert update(home, env, "--ref", "v2").returncode == 0
    (app_dir(home) / "folders.yaml").write_text("changed after the update\n", encoding="utf-8")

    result = run(home, "--rollback", "--yes", **env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Rolled back to {commits['v1']}" in result.stdout
    assert state(home) == ("v1", "3", ["docs"])
    assert (app_dir(home) / "folders.yaml").read_text(encoding="utf-8") == "folders: []\n"
    assert info(app_dir(home) / "installed.info")["commit"] == commits["v1"]
    # The snapshot stays, so a rollback can be repeated.
    assert len(snapshots(home)) == 1


@macos_only
@pytest.mark.integration
def test_update_keeps_the_last_two_snapshots(installed):
    home, env, commits = installed
    for ref in ("v2", "v3", "v2"):
        result = update(home, env, "--ref", ref)
        assert result.returncode == 0, result.stdout + result.stderr

    kept = [info(s / "snapshot.info")["commit"] for s in snapshots(home)]
    assert kept == [commits["v2"], commits["v3"]]


@macos_only
def test_dry_run_update_changes_nothing(installed):
    home, env, commits = installed
    before = sorted(str(p) for p in home.rglob("*"))

    result = update(home, env, "--ref", "v2", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert f"Installed: {commits['v1']}" in result.stdout
    assert f"({commits['v2']})" in result.stdout
    assert sorted(str(p) for p in home.rglob("*")) == before


@macos_only
def test_update_with_nothing_installed_is_refused(tmp_path):
    result = run(tmp_path, "--update")
    assert result.returncode == 1
    assert "nothing is installed" in result.stderr


@macos_only
def test_rollback_without_a_snapshot_is_refused(tmp_path):
    result = run(tmp_path, "--rollback", "--yes")
    assert result.returncode == 1
    assert "no snapshot" in result.stderr


@macos_only
def test_update_goes_alone(tmp_path):
    result = run(tmp_path, "--update", "--uninstall")
    assert result.returncode == 1
    assert "go alone" in result.stderr


def fake_snapshot(home: Path) -> Path:
    snap = app_dir(home) / "snapshots" / "20261104-120000"
    (snap / "files.lancedb").mkdir(parents=True)
    (snap / "snapshot.info").write_text("commit=abc\n", encoding="utf-8")
    (app_dir(home) / "installed.info").write_text("commit=abc\n", encoding="utf-8")
    return snap


@macos_only
def test_uninstall_keeps_the_snapshots_and_purge_deletes_them(tmp_path):
    home = tmp_path / "home"
    snap = fake_snapshot(home)

    kept = run(home, "--uninstall", "--yes")

    assert kept.returncode == 0, kept.stderr
    assert snap.exists()
    assert not (app_dir(home) / "installed.info").exists()
    assert "Kept the update snapshots" in kept.stdout

    purged = run(home, "--uninstall", "--yes", "--purge")

    assert purged.returncode == 0, purged.stderr
    assert not app_dir(home).exists()
