"""haiku-rag-macos: the desktop parts around haiku.rag.

    haiku-rag-macos [--config FILE] reader [--host H] [--port N] [--idle-exit S]
    haiku-rag-macos [--config FILE] ingester [--idle-exit S]
    haiku-rag-macos [--config FILE] folders list
    haiku-rag-macos [--config FILE] folders add DIR [--id ID] [--database DB]
                                           [--include PATTERN]... [--exclude PATTERN]...
    haiku-rag-macos [--config FILE] folders update ID [--include ...] [--exclude ...]
    haiku-rag-macos [--config FILE] folders remove ID [--keep-documents]
    haiku-rag-macos [--config FILE] migrate-register FOLDERS_YAML

FILE defaults to HAIKU_RAG_CONFIG_PATH, then the installed configuration.
`folders` prints one folder per line: id, database and path, tab-separated.
A change asks a running haiku-ingester to reload; a stopped one reads it when
it starts.
"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

from haiku_rag_macos.folders import FolderRegister, FolderRegisterError
from haiku_rag_macos.settings import Settings, SettingsError


def _print_folder(folder) -> None:
    print(f"{folder.id}\t{folder.database}\t{folder.path}")


def _reload(settings: Settings) -> None:
    from haiku_rag_macos.ingester_api import IngesterAPI, IngesterError

    api = IngesterAPI(settings.ingester_url, settings.auth_token)
    try:
        report = api.reload(timeout=10)
    except TimeoutError:
        print("haiku-ingester is applying the change", file=sys.stderr)
        return
    except IngesterError as error:
        print(f"haiku-ingester did not reload: {error.detail}", file=sys.stderr)
        return
    if report is None:
        print("haiku-ingester is not running; it applies the change when it starts", file=sys.stderr)
    elif report.get("outcome") != "applied":
        print(f"haiku-ingester's reload: {report}", file=sys.stderr)


def folders(settings: Settings, args: argparse.Namespace) -> None:
    register = FolderRegister.of(settings)
    if args.action == "list":
        for folder in register.folders():
            _print_folder(folder)
        return
    if args.action == "add":
        folder = register.add(
            args.path,
            database=args.database,
            include=args.include,
            exclude=args.exclude,
            folder_id=args.id,
        )
    elif args.action == "update":
        folder = register.update(
            args.id,
            include=args.include if args.include is not None else None,
            exclude=args.exclude if args.exclude is not None else None,
        )
    else:
        folder = register.remove(args.id, keep_documents=args.keep_documents)
    _print_folder(folder)
    _reload(settings)


def migrate_register(settings: Settings, path: Path) -> None:
    """Register the folders of the earlier fork's `folders.yaml` as
    fragments, with the same ids, so their documents stay theirs. Its retired
    folders are retired here too. Does nothing once folders are registered."""
    register = FolderRegister.of(settings)
    if register.folders():
        print("Folders are registered already; nothing to migrate")
        return
    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for entry in content.get("folders") or []:
        try:
            folder = register.add(
                entry["path"],
                database=entry.get("database"),
                include=entry.get("include") or [],
                exclude=entry.get("exclude") or [],
                folder_id=entry["id"],
            )
        except FolderRegisterError as error:
            print(f"Not migrated: {entry.get('id')} ({entry.get('path')}): {error}", file=sys.stderr)
            continue
        _print_folder(folder)
    for entry in content.get("retired") or []:
        register.retire(entry["id"], entry["database"])


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="haiku-rag-macos", description=__doc__.split("\n")[0])
    top.add_argument("--config", help="haiku.rag configuration file")
    commands = top.add_subparsers(dest="command", required=True)

    reader = commands.add_parser("reader", help="serve searches, folders and status")
    reader.add_argument("--host", default="127.0.0.1")
    reader.add_argument("--port", type=int, default=8765)
    reader.add_argument("--idle-exit", type=float, default=900, help="seconds; 0 never")
    reader.add_argument("--search-idle", type=float, default=300, help="seconds")
    reader.add_argument("--ingester-label", help="the ingester's launchd label, to start it")

    ingester = commands.add_parser("ingester", help="run haiku-ingester serve until idle")
    ingester.add_argument("--idle-exit", type=float, default=600, help="seconds; 0 never")
    ingester.add_argument("--check-every", type=float, default=30, help="seconds")

    folder = commands.add_parser("folders", help="the indexed folders")
    actions = folder.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    add = actions.add_parser("add")
    add.add_argument("path")
    add.add_argument("--id")
    add.add_argument("--database")
    add.add_argument("--include", action="append", default=[])
    add.add_argument("--exclude", action="append", default=[])
    update = actions.add_parser("update")
    update.add_argument("id")
    update.add_argument("--include", action="append")
    update.add_argument("--exclude", action="append")
    remove = actions.add_parser("remove")
    remove.add_argument("id")
    remove.add_argument("--keep-documents", action="store_true")

    migrate = commands.add_parser("migrate-register", help="import the fork's folders.yaml")
    migrate.add_argument("path", type=Path)
    return top


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        settings = Settings.load(args.config)
        if args.command == "reader":
            from haiku_rag_macos.reader import serve

            serve(
                settings,
                host=args.host,
                port=args.port,
                idle_exit_s=args.idle_exit,
                search_idle_s=args.search_idle,
                ingester_label=args.ingester_label,
            )
        elif args.command == "ingester":
            from haiku_rag_macos.ingester import ingester_command, run
            from haiku_rag_macos.ingester_api import IngesterAPI

            sys.exit(
                run(
                    ingester_command(),
                    settings.path,
                    IngesterAPI(settings.ingester_url, settings.auth_token),
                    idle_exit_s=args.idle_exit,
                    maintenance=settings.config.ingester.maintenance.enabled,
                    check_every_s=args.check_every,
                )
            )
        elif args.command == "folders":
            folders(settings, args)
        else:
            migrate_register(settings, args.path)
    except (SettingsError, FolderRegisterError) as error:
        sys.exit(f"haiku-rag-macos: {error}")


if __name__ == "__main__":
    main()
