# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "questionary==2.1.1",
#     "prompt-toolkit==3.0.53",
#     "wcwidth==0.9.4",
# ]
# ///
#
# The menu of install.sh, run by it with `uv run --script`:
#
#   install-tui.py SPEC RESULT
#
# SPEC holds what the installer found, RESULT receives what was chosen. Both are
# key=value lines; a component line is `component=` and tab-separated fields:
# key, checked, available, description, unavailable reason, installed note.
# RESULT ends with `action=install` or `action=cancel`; without that line the
# menu did not run and the installer falls back to its numbered menu.
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ADD_FOLDER = "\0add"
SOURCE_KEEP = "keep"
SOURCE_DOWNLOAD = "download"
SOURCE_REPO = "repo"
SOURCE_FOLDER = "folder"
BOLD = "bold"
ON = "fg:ansigreen"
OFF = "fg:ansibrightblack"
INDENT = " " * 29
NAMES = {
    "service": "Search service",
    "model": "Embedding model",
    "raycast": "Raycast",
    "hotkey": "Hotkey",
    "folders": "Folders",
    "fda": "Full Disk Access",
}


@dataclass
class Component:
    key: str
    checked: bool
    available: bool
    description: str
    reason: str = ""
    installed: str = ""


@dataclass
class Spec:
    home: str
    dry_run: bool = False
    components: dict[str, Component] = field(default_factory=dict)
    model_installed: bool = False
    model_download: str = ""
    default_repo: str = ""
    hf_endpoint: str = "https://huggingface.co"
    model_repo: str = ""
    model_revision: str = "main"
    model_from: str = ""
    registered: list[str] = field(default_factory=list)
    given: list[str] = field(default_factory=list)


@dataclass
class Selection:
    want: dict[str, bool]
    source: str = SOURCE_DOWNLOAD
    model_repo: str = ""
    model_revision: str = "main"
    model_from: str = ""
    folders: list[str] = field(default_factory=list)


def read_spec(text: str) -> Spec:
    """Parse the installer's SPEC lines."""
    spec = Spec(home=str(Path.home()))
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key == "component":
            name, checked, available, description, reason, installed = (
                value.split("\t") + [""] * 6
            )[:6]
            spec.components[name] = Component(
                name, checked == "1", available == "1", description, reason, installed
            )
        elif key == "dry_run":
            spec.dry_run = value == "1"
        elif key == "home":
            spec.home = value
        elif key == "model.installed":
            spec.model_installed = value == "1"
        elif key == "model.download":
            spec.model_download = value
        elif key == "model.default_repo":
            spec.default_repo = value
        elif key == "hf_endpoint":
            spec.hf_endpoint = value
        elif key == "model.repo":
            spec.model_repo = value
        elif key == "model.revision":
            spec.model_revision = value or "main"
        elif key == "model.from":
            spec.model_from = value
        elif key == "folder.registered" and value:
            spec.registered.append(value)
        elif key == "folder.given" and value:
            spec.given.append(value)
    return spec


def initial_selection(spec: Spec) -> Selection:
    """The installer's recommendation, as the menu's starting point."""
    want = {c.key: c.checked and c.available for c in spec.components.values()}
    if spec.model_from:
        source = SOURCE_FOLDER
    elif spec.model_repo and spec.model_repo != spec.default_repo:
        source = SOURCE_REPO
    elif spec.model_installed and not spec.model_repo:
        source = SOURCE_KEEP
    else:
        source = SOURCE_DOWNLOAD
    if spec.given:
        folders = list(spec.given)
    elif not spec.registered:
        folders = [os.path.join(spec.home, "Documents")]
    else:
        folders = []
    return Selection(
        want=want,
        source=source,
        model_repo=spec.model_repo or spec.default_repo,
        model_revision=spec.model_revision,
        model_from=spec.model_from,
        folders=[f for f in folders if f not in spec.registered],
    )


def check_components(spec: Spec, chosen: list[str]) -> bool | str:
    """True, or why the checked components cannot be installed together."""
    if not chosen:
        return "Check at least one component, or press Ctrl-C to cancel"
    service = "service" in chosen or bool(spec.components["service"].installed)
    if "folders" in chosen and not service:
        return "Folders to index need the service: check service too"
    if "fda" in chosen and not service:
        return "Full Disk Access is for the service: check service too"
    return True


def candidate_folders(spec: Spec, selection: Selection) -> list[str]:
    """Folders worth offering: the usual places that exist, and those chosen."""
    home = Path(spec.home)
    usual = [
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "Library" / "Mobile Documents" / "com~apple~CloudDocs",
    ]
    offered = [str(p) for p in usual if p.is_dir()]
    for folder in [*spec.given, *selection.folders]:
        if folder not in offered:
            offered.append(folder)
    return [f for f in offered if f not in spec.registered]


def display_path(path: str, home: str) -> str:
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        path = "~" + path[len(home) :]
    return path.replace(
        "~/Library/Mobile Documents/com~apple~CloudDocs", "iCloud Drive"
    )


def expand_folder(text: str, home: str) -> str:
    text = text.strip()
    if text == "~" or text.startswith("~/"):
        text = home + text[1:]
    return os.path.abspath(text)


def check_folder(text: str, home: str) -> bool | str:
    """True for Return or an existing directory."""
    if not text.strip():
        return True
    path = expand_folder(text, home)
    if "\n" in path:
        return "A folder name cannot hold a line break"
    if not os.path.isdir(path):
        return f"{display_path(path, home)} is not a folder"
    return True


def model_line(spec: Spec, selection: Selection) -> str:
    if selection.source == SOURCE_KEEP:
        return "keep the installed model"
    if selection.source == SOURCE_FOLDER:
        return f"copy from {display_path(selection.model_from, spec.home)}"
    revision = selection.model_revision or "main"
    return f"download {spec.hf_endpoint}/{selection.model_repo} ({revision}), checksum-verified"


def name(key: str) -> str:
    return NAMES.get(key, key)


def plan_lines(spec: Spec, selection: Selection) -> list[tuple[str, str]]:
    """The plan as (style, text) lines."""
    lines = [
        (
            BOLD,
            "The plan" + (" (dry run: nothing is installed)" if spec.dry_run else ""),
        )
    ]
    for component in spec.components.values():
        on = selection.want.get(component.key, False)
        mark = "install" if on else "skip   "
        lines.append(
            (
                ON if on else OFF,
                f"  {mark}  {name(component.key):<17} {component.description}",
            )
        )
        if on and component.key == "model":
            lines.append(("", f"{INDENT}source: {model_line(spec, selection)}"))
        if on and component.key == "folders":
            for folder in spec.registered:
                lines.append(
                    (
                        OFF,
                        f"{INDENT}indexed already: {display_path(folder, spec.home)}",
                    )
                )
            for folder in selection.folders:
                lines.append(("", f"{INDENT}add: {display_path(folder, spec.home)}"))
            if not spec.registered and not selection.folders:
                lines.append((OFF, f"{INDENT}no folder chosen"))
    return lines


def result_lines(selection: Selection, action: str) -> list[str]:
    """RESULT lines for the installer."""
    if action != "install":
        return [f"action={action}"]
    lines = [f"want.{key}={int(on)}" for key, on in selection.want.items()]
    if selection.want.get("model"):
        if selection.source == SOURCE_FOLDER:
            lines.append(f"model.from={selection.model_from}")
        elif selection.source in (SOURCE_DOWNLOAD, SOURCE_REPO):
            lines.append(f"model.repo={selection.model_repo}")
            lines.append(f"model.revision={selection.model_revision or 'main'}")
    if selection.want.get("folders"):
        lines.extend(f"folder={folder}" for folder in selection.folders)
    lines.append(f"action={action}")
    return lines


def write_result(path: str, lines: list[str]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(f"{line}\n" for line in lines))
    os.replace(tmp, path)


def restyle_rows(tokens: list[tuple]) -> list[tuple]:
    """Checkbox rows with a coloured check mark and the focus colour on the pointed row only."""
    rows: list[list[tuple]] = [[]]
    for token in tokens:
        rows[-1].append(token)
        if token[:2] == ("", "\n"):
            rows.append([])
    out = []
    for row in rows:
        pointed = any(t[0] == "class:pointer" for t in row)
        for token in row:
            if token[0] == "class:selected":
                if token[1].strip() == "●":
                    cls = "class:checked"
                else:
                    cls = "class:highlighted" if pointed else "class:text"
                token = (cls, *token[1:])
            out.append(token)
    return out


# --- the screens -------------------------------------------------------------


def ui():
    """questionary, which only the script's own environment has."""
    import questionary  # ty: ignore[unresolved-import]
    from questionary.prompts.common import (  # ty: ignore[unresolved-import]
        InquirerControl,
    )

    tokens = InquirerControl._get_choice_tokens
    if tokens.__name__ != "restyled":

        def restyled(self):
            return restyle_rows(tokens(self))

        InquirerControl._get_choice_tokens = restyled
    return questionary


# No background anywhere: rows read as plain text on dark and light themes.
STYLE = [
    ("qmark", "fg:ansiblue bold"),
    ("question", "bold"),
    ("answer", "fg:ansiblue"),
    ("pointer", "fg:ansiblue bold"),
    ("highlighted", "fg:ansiblue noreverse bg:default"),
    ("selected", "noreverse bg:default"),
    ("checked", "fg:ansigreen noreverse bg:default"),
    ("text", "noreverse bg:default"),
    ("disabled", f"{OFF} italic noreverse bg:default"),
    ("instruction", OFF),
    ("bottom-toolbar", "noreverse bg:default"),
    ("validation-toolbar", "fg:ansired bold noreverse bg:default"),
    ("completion-menu", "fg:default noreverse bg:default"),
    ("completion-menu.completion", "fg:default noreverse bg:default"),
    ("completion-menu.completion.current", "fg:ansiblue bold noreverse bg:default"),
    ("completion-menu.meta.completion", f"{OFF} noreverse bg:default"),
    ("completion-menu.meta.completion.current", "fg:ansiblue noreverse bg:default"),
    ("completion-menu.multi-column-meta", f"{OFF} noreverse bg:default"),
    ("scrollbar.background", "noreverse bg:default"),
    ("scrollbar.button", "noreverse bg:default"),
]


def style():
    return ui().Style(STYLE)


def ask_path(message: str, validate, default: str = "") -> str:
    """A folder path; Tab completes it, Return takes it as typed."""
    from prompt_toolkit.completion import PathCompleter  # ty: ignore[unresolved-import]

    return (
        ui()
        .text(
            message,
            default=default,
            validate=validate,
            completer=PathCompleter(only_directories=True, expanduser=True),
            complete_while_typing=False,
            style=style(),
        )
        .unsafe_ask()
    )


def ask_components(spec: Spec, selection: Selection) -> None:
    questionary = ui()

    choices = []
    for c in spec.components.values():
        title = f"{name(c.key):<17} {c.description}"
        if c.installed:
            title += f"  [{c.installed}]"
        choices.append(
            questionary.Choice(
                title,
                value=c.key,
                checked=selection.want.get(c.key, False),
                disabled=None if c.available else c.reason,
            )
        )
    chosen = questionary.checkbox(
        "What should be installed?",
        choices=choices,
        validate=lambda chosen: check_components(spec, chosen),
        instruction="(↑↓ move, space checks or unchecks, a all, i invert, Return continues)",
        style=style(),
    ).unsafe_ask()
    selection.want = {key: key in chosen for key in spec.components}


def ask_model(spec: Spec, selection: Selection) -> None:
    questionary = ui()

    choices = []
    if spec.model_installed:
        choices.append(
            questionary.Choice("Keep the installed model", value=SOURCE_KEEP)
        )
    choices += [
        questionary.Choice(
            f"Download from Hugging Face ({spec.model_download})",
            value=SOURCE_DOWNLOAD,
            description=f"{spec.hf_endpoint}/{spec.default_repo}, every file checked against its SHA-256",
        ),
        questionary.Choice(
            "Download from another Hugging Face repository…",
            value=SOURCE_REPO,
            description="You name the repository and its revision next",
        ),
        questionary.Choice(
            "Copy a model folder from this Mac…",
            value=SOURCE_FOLDER,
            description="A folder holding model.mlpackage and tokenizer.json",
        ),
    ]
    default = next((c for c in choices if c.value == selection.source), choices[0])
    selection.source = questionary.select(
        "Where should the embedding model come from?",
        choices=choices,
        default=default,
        style=style(),
    ).unsafe_ask()
    if selection.source == SOURCE_DOWNLOAD:
        selection.model_repo = spec.default_repo
        selection.model_revision = "main"
    elif selection.source == SOURCE_REPO:
        repo = selection.model_repo if selection.model_repo != spec.default_repo else ""
        selection.model_repo = (
            questionary.text(
                "Hugging Face repository (owner/name):",
                default=repo,
                validate=lambda t: (
                    "/" in t.strip() and " " not in t.strip() or "An id like owner/name"
                ),
                style=style(),
            )
            .unsafe_ask()
            .strip()
        )
        selection.model_revision = (
            questionary.text(
                "Its branch, tag or commit:",
                default=selection.model_revision or "main",
                validate=lambda t: bool(t.strip()) or "A revision, e.g. main",
                style=style(),
            )
            .unsafe_ask()
            .strip()
        )
    elif selection.source == SOURCE_FOLDER:

        def check_model(text: str) -> bool | str:
            path = expand_folder(text, spec.home)
            if os.path.isdir(os.path.join(path, "model.mlpackage")) and os.path.isfile(
                os.path.join(path, "tokenizer.json")
            ):
                return True
            return "That folder holds no model.mlpackage and tokenizer.json"

        selection.model_from = expand_folder(
            ask_path(
                "Model folder (Tab completes):", check_model, selection.model_from
            ),
            spec.home,
        )


def ask_folders(spec: Spec, selection: Selection) -> None:
    questionary = ui()

    choices = [
        questionary.Choice(
            display_path(f, spec.home), value=f, disabled="indexed already"
        )
        for f in spec.registered
    ]
    choices += [
        questionary.Choice(
            display_path(f, spec.home), value=f, checked=f in selection.folders
        )
        for f in candidate_folders(spec, selection)
    ]
    choices.append(questionary.Choice("Another folder…", value=ADD_FOLDER))
    chosen = questionary.checkbox(
        "Which folders should be indexed?",
        choices=choices,
        instruction="(space checks or unchecks, Return continues)",
        style=style(),
    ).unsafe_ask()
    folders = [f for f in chosen if f != ADD_FOLDER]
    if ADD_FOLDER in chosen:
        while True:
            text = ask_path(
                "Folder to index (Tab completes, Return when done):",
                lambda t: check_folder(t, spec.home),
            )
            if not text.strip():
                break
            folder = expand_folder(text, spec.home)
            if folder not in folders and folder not in spec.registered:
                folders.append(folder)
    selection.folders = folders


def confirm(spec: Spec, selection: Selection) -> str:
    questionary = ui()

    questionary.print("")
    for line_style, text in plan_lines(spec, selection):
        questionary.print(text, style=line_style)
    questionary.print("")
    go = "Show the plan (dry run: nothing is installed)" if spec.dry_run else "Install"
    return questionary.select(
        "Go ahead?",
        choices=[
            questionary.Choice(go, value="install"),
            questionary.Choice("Change the selection", value="change"),
            questionary.Choice("Cancel: install nothing", value="cancel"),
        ],
        style=style(),
    ).unsafe_ask()


def run(spec: Spec) -> tuple[Selection, str]:
    """Ask until the plan is confirmed or cancelled."""
    questionary = ui()

    selection = initial_selection(spec)
    if spec.dry_run:
        questionary.print("Dry run: choose freely, nothing is installed.", style=OFF)
    while True:
        ask_components(spec, selection)
        if selection.want.get("model"):
            ask_model(spec, selection)
        if selection.want.get("folders"):
            ask_folders(spec, selection)
        action = confirm(spec, selection)
        if action != "change":
            return selection, action


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} SPEC RESULT", file=sys.stderr)
        return 64
    spec = read_spec(Path(argv[1]).read_text(encoding="utf-8"))
    try:
        selection, action = run(spec)
    except KeyboardInterrupt:
        selection, action = initial_selection(spec), "cancel"
    write_result(argv[2], result_lines(selection, action))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
