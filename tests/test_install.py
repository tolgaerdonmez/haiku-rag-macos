import hashlib
import importlib.util
import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-macos.sh"
MENU = ROOT / "scripts" / "install-macos-tui.py"
SERVICE = ROOT / "scripts" / "macos-service.sh"
RULE = "Cmd+Shift+F opens haiku.rag Search Documents in Raycast"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell scripts")
macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS installer")


def run(home: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """The installer under a throwaway HOME and agent label, without a terminal."""
    environment = {
        **os.environ,
        "HOME": str(home),
        "HAIKU_RAG_SERVICE_LABEL": "com.haiku-rag.service.pytest",
        "HAIKU_RAG_SERVICE_PORT": "18799",
        **env,
    }
    return subprocess.run(
        ["/bin/sh", str(INSTALLER), *args],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        start_new_session=True,
    )


class Terminal:
    """The installer on a pseudo-terminal of its own, driven by keystrokes."""

    def __init__(self, home: Path, *args: str, **env: str):
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        environment = {
            **os.environ,
            "HOME": str(home),
            "HAIKU_RAG_SERVICE_LABEL": "com.haiku-rag.service.pytest",
            "HAIKU_RAG_SERVICE_PORT": "18799",
            "TERM": "xterm-256color",
            **env,
        }
        self.process = subprocess.Popen(
            ["/bin/sh", str(INSTALLER), *args],
            env=environment,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
        )
        os.close(slave)
        self.fd = master
        self.raw = b""
        self.seen = 0

    @property
    def text(self) -> str:
        return re.sub(
            r"\x1b\[[0-9;?]*[A-Za-z]", "", self.raw.decode("utf-8", "replace")
        )

    def _read(self, timeout: float) -> bool:
        """Reads what arrived; False once the terminal closed."""
        if not select.select([self.fd], [], [], timeout)[0]:
            return True
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            return False
        if not data:
            return False
        if b"\x1b[6n" in data:
            os.write(self.fd, b"\x1b[1;1R")
        self.raw += data
        return True

    def expect(self, text: str, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while (found := self.text.find(text, self.seen)) < 0:
            if time.monotonic() > deadline or not self._read(0.2):
                raise AssertionError(f"no {text!r} in:\n{self.text}")
        self.seen = found + len(text)
        time.sleep(0.3)

    def send(self, *keys: str) -> None:
        for key in keys:
            os.write(self.fd, key.encode())
            time.sleep(0.2)

    def wait(self, timeout: float = 120) -> int:
        deadline = time.monotonic() + timeout
        while self._read(0.2) and time.monotonic() < deadline:
            pass
        os.close(self.fd)
        return self.process.wait(timeout=10)


def menu_module():
    spec = importlib.util.spec_from_file_location("install_macos_tui", MENU)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MENU_SPEC = """dry_run=1
home=/Users/me
component=service\t1\t1\tBackground search service\t\t
component=model\t1\t1\tCore ML embedding model\t\tinstalled
component=raycast\t0\t0\tRaycast extension\tRaycast is not installed\t
component=folders\t1\t1\tFolders to index\t\t
component=fda\t1\t1\tOpen Full Disk Access afterwards\t\t
model.installed=1
model.default_repo=owner/model
folder.registered=/Users/me/Notes
folder.given=/Users/me/Papers
"""


def app_dir(home: Path) -> Path:
    return home / "Library" / "Application Support" / "haiku.rag"


def fake_model_repo(root: Path) -> tuple[str, str]:
    """A Hugging Face layout served from file://, and its SHA256SUMS checksum."""
    files = root / "test" / "model" / "resolve" / "main"
    (files / "model.mlpackage" / "Data").mkdir(parents=True)
    (files / "model.mlpackage" / "Manifest.json").write_text("{}", encoding="utf-8")
    (files / "model.mlpackage" / "Data" / "weight.bin").write_bytes(b"\0" * 64)
    (files / "tokenizer.json").write_text("{}", encoding="utf-8")
    lines = [
        f"{hashlib.sha256((files / path).read_bytes()).hexdigest()}  {path}"
        for path in (
            "model.mlpackage/Data/weight.bin",
            "model.mlpackage/Manifest.json",
            "tokenizer.json",
        )
    ]
    sums = "\n".join(lines) + "\n"
    (files / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return root.as_uri(), hashlib.sha256(sums.encode()).hexdigest()


@pytest.mark.parametrize("script", [INSTALLER, SERVICE])
def test_scripts_parse(script):
    subprocess.run(["/bin/sh", "-n", str(script)], check=True)


@macos_only
def test_help_lists_the_components(tmp_path):
    result = run(tmp_path, "--help")
    assert result.returncode == 0
    for option in (
        "--service",
        "--model",
        "--raycast",
        "--hotkey",
        "--folder",
        "--uninstall",
    ):
        assert option in result.stderr


@macos_only
def test_unknown_option_is_refused(tmp_path):
    assert run(tmp_path, "--frobnicate").returncode == 64


@macos_only
def test_dry_run_prints_the_plan_and_changes_nothing(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    result = run(home, "--dry-run", "--no-raycast", "--hotkey", "--folder", str(docs))
    assert result.returncode == 0, result.stderr
    assert "service: install" in result.stdout
    assert "raycast: skip" in result.stdout
    assert f"folder: {docs}" in result.stdout
    assert list(home.iterdir()) == []


@macos_only
def test_model_downloads_and_verifies_every_file(tmp_path):
    endpoint, sums = fake_model_repo(tmp_path / "hf")
    home = tmp_path / "home"
    home.mkdir()
    result = run(
        home,
        "--yes", "--no-service", "--no-raycast", "--no-hotkey",
        "--model-repo", "test/model", "--model-sha256", sums,
        HF_ENDPOINT=endpoint,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    model = app_dir(home) / "models" / "jina-nano-coreml"
    assert (
        model / "model.mlpackage" / "Data" / "weight.bin"
    ).read_bytes() == b"\0" * 64
    assert (model / "tokenizer.json").exists()
    assert not (model / "SHA256SUMS").exists()


@macos_only
def test_a_tampered_model_file_is_refused(tmp_path):
    endpoint, sums = fake_model_repo(tmp_path / "hf")
    weight = tmp_path / "hf/test/model/resolve/main/model.mlpackage/Data/weight.bin"
    weight.write_bytes(b"\1" * 64)
    home = tmp_path / "home"
    home.mkdir()
    result = run(
        home,
        "--yes", "--no-service", "--no-raycast", "--no-hotkey",
        "--model-repo", "test/model", "--model-sha256", sums,
        HF_ENDPOINT=endpoint,
    )  # fmt: skip
    assert result.returncode != 0
    assert "checksum mismatch for model.mlpackage/Data/weight.bin" in result.stderr
    assert not (app_dir(home) / "models").exists()


@macos_only
def test_uninstall_keeps_the_index_and_indexed_files(tmp_path):
    home = tmp_path / "home"
    app = app_dir(home)
    indexed = tmp_path / "Documents" / "notes.md"
    indexed.parent.mkdir()
    indexed.write_text("mine", encoding="utf-8")
    for path in (
        "files.lancedb/data.lance",
        "ingester.db",
        "folders.yaml",
        "haiku.rag.yaml",
        "models/jina-nano-coreml/tokenizer.json",
        "service/bin/haiku-rag-service",
        "raycast/package.json",
    ):
        (app / path).parent.mkdir(parents=True, exist_ok=True)
        (app / path).write_text("x", encoding="utf-8")
    karabiner = home / ".config" / "karabiner" / "karabiner.json"
    karabiner.parent.mkdir(parents=True)
    rules = [{"description": RULE}, {"description": "another rule"}]
    profile = {"selected": True, "complex_modifications": {"rules": rules}}
    karabiner.write_text(json.dumps({"profiles": [profile]}), encoding="utf-8")

    result = run(home, "--uninstall", "--yes")

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in app.iterdir()) == [
        "files.lancedb",
        "folders.yaml",
        "ingester.db",
    ]
    assert indexed.read_text(encoding="utf-8") == "mine"
    kept = json.loads(karabiner.read_text(encoding="utf-8"))
    assert kept["profiles"][0]["complex_modifications"]["rules"] == [
        {"description": "another rule"}
    ]


@macos_only
def test_uninstall_deletes_the_index_only_when_asked(tmp_path):
    home = tmp_path / "home"
    app = app_dir(home)
    (app / "files.lancedb").mkdir(parents=True)
    (app / "folders.yaml").write_text("folders: []\n", encoding="utf-8")

    result = run(home, "--uninstall", "--yes", "--delete-index")

    assert result.returncode == 0, result.stderr
    assert not app.exists()


def test_menu_starts_from_the_installers_recommendation():
    menu = menu_module()
    spec = menu.read_spec(MENU_SPEC)
    selection = menu.initial_selection(spec)
    assert selection.want == {
        "service": True,
        "model": True,
        "raycast": False,
        "folders": True,
        "fda": True,
    }
    assert selection.source == menu.SOURCE_KEEP
    assert selection.folders == ["/Users/me/Papers"]
    assert spec.registered == ["/Users/me/Notes"]
    assert spec.components["raycast"].reason == "Raycast is not installed"


def test_menu_suggests_documents_when_no_folder_is_registered():
    menu = menu_module()
    spec = menu.read_spec("home=/Users/me\ncomponent=folders\t1\t1\tFolders\t\t\n")
    assert menu.initial_selection(spec).folders == ["/Users/me/Documents"]


def test_menu_refuses_folders_and_full_disk_access_without_the_service():
    menu = menu_module()
    spec = menu.read_spec(MENU_SPEC)
    assert menu.check_components(spec, ["service", "folders", "fda"]) is True
    assert "need the service" in menu.check_components(spec, ["model", "folders"])
    assert "for the service" in menu.check_components(spec, ["fda"])
    assert "at least one" in menu.check_components(spec, [])


def test_menu_result_carries_every_choice():
    menu = menu_module()
    spec = menu.read_spec(MENU_SPEC)
    selection = menu.initial_selection(spec)
    selection.want["fda"] = False
    selection.source = menu.SOURCE_FOLDER
    selection.model_from = "/Users/me/model"
    assert menu.result_lines(selection, "install") == [
        "want.service=1",
        "want.model=1",
        "want.raycast=0",
        "want.folders=1",
        "want.fda=0",
        "model.from=/Users/me/model",
        "folder=/Users/me/Papers",
        "action=install",
    ]
    selection.source = menu.SOURCE_REPO
    selection.model_repo, selection.model_revision = "other/model", "v2"
    lines = menu.result_lines(selection, "install")
    assert "model.repo=other/model" in lines
    assert "model.revision=v2" in lines
    assert menu.result_lines(selection, "cancel") == ["action=cancel"]


def test_menu_plan_lists_what_is_installed_and_skipped():
    menu = menu_module()
    spec = menu.read_spec(MENU_SPEC)
    selection = menu.initial_selection(spec)
    text = "\n".join(line for _, line in menu.plan_lines(spec, selection))
    assert "dry run" in text
    assert "install  Search service" in text
    assert "skip     Raycast" in text
    assert "source: keep the installed model" in text
    assert "indexed already: ~/Notes" in text
    assert "add: ~/Papers" in text


def test_menu_rows_colour_the_check_mark_and_only_the_focused_row():
    menu = menu_module()
    tokens = [
        ("class:pointer", " » "),
        ("[SetCursorPosition]", ""),
        ("class:selected", "● "),
        ("class:selected", "Search service"),
        ("", "\n"),
        ("class:text", "   "),
        ("class:selected", "● "),
        ("class:selected", "Embedding model"),
        ("", "\n"),
        ("class:text", "   "),
        ("class:text", "○ "),
        ("class:text", "Raycast"),
    ]
    assert [t[0] for t in menu.restyle_rows(tokens)] == [
        "class:pointer",
        "[SetCursorPosition]",
        "class:checked",
        "class:highlighted",
        "",
        "class:text",
        "class:checked",
        "class:text",
        "",
        "class:text",
        "class:text",
        "class:text",
    ]


def test_menu_style_sets_no_background():
    for name, value in menu_module().STYLE:
        assert "bg:" not in value.replace("bg:default", ""), name
        assert " reverse" not in f" {value}", name


def test_menu_folder_entry_expands_home_and_refuses_a_missing_folder(tmp_path):
    menu = menu_module()
    (tmp_path / "docs").mkdir()
    assert menu.expand_folder("~/docs", str(tmp_path)) == str(tmp_path / "docs")
    assert menu.check_folder("~/docs", str(tmp_path)) is True
    assert menu.check_folder("", str(tmp_path)) is True
    assert "is not a folder" in menu.check_folder("~/missing", str(tmp_path))


@macos_only
def test_numbered_menu_on_a_terminal_switches_components(tmp_path):
    terminal = Terminal(tmp_path, "--dry-run", HAIKU_RAG_TUI="0")
    terminal.expect("Install the checked components?")
    terminal.send("n\r")
    terminal.expect("Numbers to switch on or off")
    terminal.send("1 2\r")
    terminal.expect("Install the checked components?")
    terminal.send("\r")
    assert terminal.wait() == 0
    assert "service: skip" in terminal.text
    assert "model: skip" in terminal.text
    assert list(tmp_path.iterdir()) == []


@macos_only
def test_menu_without_uv_falls_back_to_the_numbered_menu(tmp_path):
    terminal = Terminal(tmp_path, "--dry-run", PATH="/usr/bin:/bin:/usr/sbin:/sbin")
    terminal.expect("a numbered menu asks")
    terminal.expect("Install the checked components?")
    terminal.send("\r")
    assert terminal.wait() == 0
    assert "service: install" in terminal.text


def open_menu(terminal: Terminal) -> None:
    """Waits for the menu, skipping when its packages cannot be fetched."""
    try:
        terminal.expect("What should be installed?", timeout=300)
    except AssertionError:
        terminal.process.kill()
        if "the menu did not start" in terminal.text:
            pytest.skip("the menu's packages are unavailable")
        raise


@macos_only
@pytest.mark.integration
def test_menu_on_a_terminal_installs_only_what_is_checked(tmp_path):
    down, space = "\x1b[B", " "
    (tmp_path / "Documents").mkdir()
    terminal = Terminal(tmp_path, "--dry-run")
    open_menu(terminal)
    terminal.send(down, space, "\r")
    terminal.expect("Which folders should be indexed?")
    terminal.send(space, "\r")
    terminal.expect("Go ahead?")
    terminal.send("\r")
    assert terminal.wait() == 0
    plan = terminal.text[terminal.text.rfind("Dry run: nothing changed") :]
    assert "service: install" in plan
    assert "model: skip" in plan
    assert "folder:" not in plan


@macos_only
@pytest.mark.integration
def test_menu_cancel_installs_nothing(tmp_path):
    terminal = Terminal(tmp_path)
    open_menu(terminal)
    terminal.send("\x03")
    assert terminal.wait() == 0
    assert "Cancelled: nothing changed." in terminal.text
    assert not (tmp_path / "Library").exists()
