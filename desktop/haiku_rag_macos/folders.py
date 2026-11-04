"""The folders haiku-ingester indexes, kept as haiku.rag database fragments.

A folder is an `fs` source of a database. haiku.rag's `databases_include`
names a directory whose `*.yaml` files each add `ingester.databases` entries,
and a reload of haiku-ingester reads them again. This register owns one
fragment per database it adds folders to, `<database>.yaml` (or whichever
fragment already ingests that database), and nothing else in the
configuration:

    ingester:
      databases:
        files:
          sources:
            - {type: fs, id: tum, root: /Users/me/Documents/TUM,
               include_patterns: [], ignore_patterns: ['.*', '*.json']}

A database whose last folder is removed loses its fragment. Removing a folder
takes it out of the fragment; unless its documents are kept it is also listed
in `.retired.yaml` (a dotfile, which haiku.rag skips), and the `macos-folders`
hook deletes its documents inside haiku-ingester when that database next
starts, as haiku.rag's own writer (see hook.py).
"""

import fcntl
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RETIRED_FILE = ".retired.yaml"
LOCK_FILE = ".lock"

_FRAGMENT_HEADER = (
    "# The folders haiku-ingester indexes into database {database}, as fs sources.\n"
    "# Managed by `haiku-rag-macos folders` and the search service's /folders;\n"
    "# hand edits to the values are kept, comments are not.\n"
)
_RETIRED_HEADER = (
    "# Folders removed with their documents: the macos-folders hook deletes the\n"
    "# documents when haiku-ingester next starts the database, then the entry.\n"
)


class FolderRegisterError(ValueError):
    """The register cannot take the change."""


class FolderConflictError(FolderRegisterError):
    """The id or the directory is registered already."""


class UnknownFolderError(FolderRegisterError, KeyError):
    """No folder has the id."""

    def __str__(self) -> str:
        return str(self.args[0])


@dataclass
class Folder:
    """A local directory indexed into `database` as the fs source `id`.

    `include` and `exclude` are gitignore-style patterns matched against each
    file's absolute path; an empty `include` takes every supported file.
    """

    id: str
    database: str
    path: Path
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def source(self, base: dict[str, Any] | None = None) -> dict[str, Any]:
        """The folder as an fs source, over the source's other settings."""
        return {
            **(base or {}),
            "type": "fs",
            "id": self.id,
            "root": str(self.path),
            "include_patterns": list(self.include),
            "ignore_patterns": list(self.exclude),
        }

    @classmethod
    def from_source(cls, database: str, source: dict[str, Any]) -> "Folder | None":
        if source.get("type") != "fs" or not source.get("id"):
            return None
        return cls(
            id=str(source["id"]),
            database=database,
            path=Path(str(source["root"])),
            include=list(source.get("include_patterns") or []),
            exclude=list(source.get("ignore_patterns") or []),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "database": self.database,
            "path": str(self.path),
            "include": list(self.include),
            "exclude": list(self.exclude),
        }


@dataclass(frozen=True)
class RetiredFolder:
    id: str
    database: str


@dataclass
class _Fragment:
    """One fragment file: its content and the sources of `database` in it."""

    path: Path
    content: dict[str, Any]

    def sources(self, database: str) -> list[dict[str, Any]]:
        entry = ((self.content.get("ingester") or {}).get("databases") or {}).get(database)
        return list((entry or {}).get("sources") or [])

    def databases(self) -> list[str]:
        return list(((self.content.get("ingester") or {}).get("databases") or {}))

    def set_sources(self, database: str, sources: list[dict[str, Any]]) -> None:
        ingester = self.content.setdefault("ingester", {}) or {}
        self.content["ingester"] = ingester
        databases = ingester.setdefault("databases", {}) or {}
        ingester["databases"] = databases
        if sources:
            entry = databases.get(database) or {}
            databases[database] = {**entry, "sources": sources}
        else:
            databases.pop(database, None)
            if not databases:
                ingester.pop("databases")
            if not ingester:
                self.content.pop("ingester")

    @property
    def empty(self) -> bool:
        return not any(value for value in self.content.values())


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "folder"


def _unique(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        content = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise FolderRegisterError(f"{path}: {error}") from None
    if not isinstance(content, dict):
        raise FolderRegisterError(f"{path}: not a mapping")
    return content


def _write_yaml(path: Path, header: str, content: dict[str, Any]) -> None:
    body = yaml.safe_dump(content, sort_keys=False, allow_unicode=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(header + body)
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


class FolderRegister:
    """The fragments in `directory` as a register of folders.

    `databases` are the configured databases a folder may go to. `check`, when
    given, is called after every change with the register's directory and
    raises when the configuration no longer loads; the change is then undone.
    Every change is a locked read-modify-write that replaces whole files.
    """

    def __init__(
        self,
        directory: Path,
        databases: list[str],
        *,
        reserved_ids: set[str] | None = None,
        check=None,
    ):
        self.directory = directory
        self.databases = databases
        self.reserved_ids = reserved_ids or set()
        self._check = check

    @classmethod
    def of(cls, settings) -> "FolderRegister":
        """The register of the configuration in `settings`, its non-folder
        source ids reserved, checking every change by loading it again."""
        from haiku.rag.config import AppConfig, load_yaml_config

        register = cls(settings.fragments, settings.databases)
        folders = {folder.id for folder in register.folders()}
        register.reserved_ids = {
            source.id
            for entry in settings.config.ingester.databases.values()
            for source in entry.sources
            if source.id is not None and source.id not in folders
        }

        def check(_: Path) -> None:
            AppConfig.model_validate(load_yaml_config(settings.path))

        register._check = check
        return register

    # --- reading -------------------------------------------------------------

    def _fragments(self) -> list[_Fragment]:
        if not self.directory.is_dir():
            return []
        return [
            _Fragment(path, _read_yaml(path))
            for path in sorted(self.directory.glob("*.yaml"))
            if not path.name.startswith(".") and path.is_file()
        ]

    def folders(self) -> list[Folder]:
        found = []
        for fragment in self._fragments():
            for database in fragment.databases():
                for source in fragment.sources(database):
                    if (folder := Folder.from_source(database, source)) is not None:
                        found.append(folder)
        return found

    def get(self, folder_id: str) -> Folder:
        for folder in self.folders():
            if folder.id == folder_id:
                return folder
        raise UnknownFolderError(f"No folder {folder_id!r} is registered")

    def retired(self) -> list[RetiredFolder]:
        content = _read_yaml(self.directory / RETIRED_FILE)
        return [
            RetiredFolder(id=str(entry["id"]), database=str(entry["database"]))
            for entry in content.get("retired") or []
        ]

    # --- changing ------------------------------------------------------------

    def add(
        self,
        path: Path | str,
        *,
        database: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        folder_id: str | None = None,
    ) -> Folder:
        directory = Path(path).expanduser().resolve()
        if not directory.is_dir():
            raise FolderRegisterError(f"{directory} is not a directory")
        database = self._database(database)
        with self._locked():
            existing = self.folders()
            for folder in existing:
                if folder.path == directory:
                    raise FolderConflictError(
                        f"{directory} is registered already as {folder.id!r}"
                    )
                if folder.path.is_relative_to(directory) or directory.is_relative_to(
                    folder.path
                ):
                    raise FolderConflictError(
                        f"{directory} overlaps folder {folder.id!r} at "
                        f"{folder.path}: a file must belong to one folder"
                    )
            taken = {folder.id for folder in existing} | self.reserved_ids
            if folder_id is None:
                folder_id = _unique(_slug(directory.name), taken)
            elif folder_id in taken:
                raise FolderConflictError(f"The id {folder_id!r} is taken")
            folder = Folder(
                id=folder_id,
                database=database,
                path=directory,
                include=list(include or []),
                exclude=list(exclude or []),
            )
            fragment = self._fragment_for(database)
            fragment.set_sources(database, [*fragment.sources(database), folder.source()])
            with self._transaction():
                self._save(fragment, database)
                self._set_retired([r for r in self.retired() if r.id != folder_id])
        return folder

    def update(
        self,
        folder_id: str,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> Folder:
        with self._locked():
            folder = self.get(folder_id)
            if include is not None:
                folder.include = list(include)
            if exclude is not None:
                folder.exclude = list(exclude)
            fragment = self._fragment_for(folder.database)
            fragment.set_sources(
                folder.database,
                [
                    folder.source(source) if source.get("id") == folder_id else source
                    for source in fragment.sources(folder.database)
                ],
            )
            with self._transaction():
                self._save(fragment, folder.database)
        return folder

    def remove(self, folder_id: str, *, keep_documents: bool = False) -> Folder:
        """Unregister a folder. Unless `keep_documents`, its documents leave
        the index when haiku-ingester next starts its database."""
        with self._locked():
            folder = self.get(folder_id)
            fragment = self._fragment_for(folder.database)
            fragment.set_sources(
                folder.database,
                [s for s in fragment.sources(folder.database) if s.get("id") != folder_id],
            )
            with self._transaction():
                self._save(fragment, folder.database)
                if not keep_documents:
                    retired = [r for r in self.retired() if r.id != folder_id]
                    self._set_retired([*retired, RetiredFolder(folder.id, folder.database)])
        return folder

    def retire(self, folder_id: str, database: str) -> None:
        """List a folder that is not registered for its documents' deletion."""
        with self._locked():
            retired = [r for r in self.retired() if r.id != folder_id]
            self._set_retired([*retired, RetiredFolder(folder_id, database)])

    def settle_retired(self, database: str, done: set[str]) -> None:
        """Drop the retired folders of `database` in `done`."""
        with self._locked():
            self._set_retired(
                [r for r in self.retired() if not (r.database == database and r.id in done)]
            )

    # --- internals -----------------------------------------------------------

    def _database(self, name: str | None) -> str:
        if name is None:
            if len(self.databases) > 1:
                raise FolderRegisterError(
                    "Several databases are configured; name the one to index "
                    f"the folder into: {', '.join(self.databases)}"
                )
            return self.databases[0]
        if name not in self.databases:
            raise FolderRegisterError(
                f"Unknown database {name!r}; configured: {', '.join(self.databases)}"
            )
        return name

    def _fragment_for(self, database: str) -> _Fragment:
        """The fragment ingesting `database`, or a new `<database>.yaml`."""
        for fragment in self._fragments():
            if database in fragment.databases():
                return fragment
        path = self.directory / f"{database}.yaml"
        if path.exists():
            # A fragment of that name adds something else; extend it.
            return _Fragment(path, _read_yaml(path))
        return _Fragment(path, {})

    def _save(self, fragment: _Fragment, database: str) -> None:
        if fragment.empty:
            fragment.path.unlink(missing_ok=True)
        else:
            _write_yaml(
                fragment.path, _FRAGMENT_HEADER.format(database=database), fragment.content
            )

    def _set_retired(self, retired: list[RetiredFolder]) -> None:
        path = self.directory / RETIRED_FILE
        if not retired:
            path.unlink(missing_ok=True)
            return
        _write_yaml(
            path,
            _RETIRED_HEADER,
            {"retired": [{"id": r.id, "database": r.database} for r in retired]},
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Undo the files changed inside when they leave the configuration
        unloadable, and raise the reason."""
        before = {
            path: path.read_bytes()
            for path in self.directory.glob("*.yaml")
        }
        retired = self.directory / RETIRED_FILE
        retired_before = retired.read_bytes() if retired.exists() else None
        yield
        if self._check is None:
            return
        try:
            self._check(self.directory)
        except Exception as error:
            for path in self.directory.glob("*.yaml"):
                if path not in before:
                    path.unlink(missing_ok=True)
            for path, data in before.items():
                path.write_bytes(data)
            if retired_before is None:
                retired.unlink(missing_ok=True)
            else:
                retired.write_bytes(retired_before)
            raise FolderRegisterError(f"the configuration would not load: {error}") from None

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / LOCK_FILE, "a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
