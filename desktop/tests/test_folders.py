from pathlib import Path

import pytest
import yaml
from haiku.rag.config import AppConfig, load_yaml_config

from haiku_rag_macos import cli
from haiku_rag_macos.folders import (
    FolderConflictError,
    FolderRegister,
    FolderRegisterError,
    RetiredFolder,
    UnknownFolderError,
)
from haiku_rag_macos.settings import Settings, SettingsError



def register() -> FolderRegister:
    return FolderRegister.of(Settings.load())


def fragment(app: Path) -> dict:
    return yaml.safe_load((app / "databases.d" / "files.yaml").read_text(encoding="utf-8"))


def test_a_folder_becomes_an_fs_source_haiku_rag_loads(config, app, folder):
    docs = folder("Course Notes")

    added = register().add(docs, exclude=[".*", "*.json"])

    assert added.id == "course-notes" and added.database == "files"
    sources = fragment(app)["ingester"]["databases"]["files"]["sources"]
    assert sources == [
        {
            "type": "fs",
            "id": "course-notes",
            "root": str(docs.resolve()),
            "include_patterns": [],
            "ignore_patterns": [".*", "*.json"],
        }
    ]
    loaded = AppConfig.model_validate(load_yaml_config(config))
    [source] = loaded.ingester.databases["files"].sources
    assert source.id == "course-notes" and source.root == docs.resolve()


def test_folders_lists_add_order_and_ids_stay_unique(config, folder):
    first = register().add(folder("a/notes"))
    second = register().add(folder("b/notes"))

    assert [f.id for f in register().folders()] == ["notes", "notes-2"]
    assert register().get("notes-2").path == second.path
    assert first.path != second.path


@pytest.mark.parametrize("where", ["same", "inside", "around"])
def test_overlapping_folders_are_refused(config, folder, where):
    outer = folder("outer")
    (outer / "inner").mkdir()
    register().add(outer / "inner" if where == "around" else outer)
    target = {"same": outer, "inside": outer / "inner", "around": outer}[where]
    with pytest.raises(FolderConflictError):
        register().add(target)


def test_a_taken_or_missing_folder_is_refused(config, folder, tmp_path):
    register().add(folder("x"), folder_id="mine")
    with pytest.raises(FolderConflictError, match="taken"):
        register().add(folder("y"), folder_id="mine")
    with pytest.raises(FolderRegisterError, match="not a directory"):
        register().add(tmp_path / "missing")
    with pytest.raises(FolderRegisterError, match="Unknown database"):
        register().add(folder("z"), database="elsewhere")
    with pytest.raises(UnknownFolderError):
        register().remove("nope")


def test_update_changes_only_the_patterns_and_keeps_other_settings(config, app, folder):
    register().add(folder("x"), folder_id="x")
    content = fragment(app)
    content["ingester"]["databases"]["files"]["sources"][0]["poll_interval_s"] = 60.0
    (app / "databases.d" / "files.yaml").write_text(yaml.safe_dump(content), encoding="utf-8")

    updated = register().update("x", include=["*.pdf"])

    assert updated.include == ["*.pdf"]
    [source] = fragment(app)["ingester"]["databases"]["files"]["sources"]
    assert source["include_patterns"] == ["*.pdf"]
    assert source["poll_interval_s"] == 60.0


def test_removing_retires_the_folder_unless_its_documents_stay(config, app, folder):
    register().add(folder("x"), folder_id="x")
    register().add(folder("y"), folder_id="y")

    register().remove("x")
    register().remove("y", keep_documents=True)

    assert register().folders() == []
    assert register().retired() == [RetiredFolder("x", "files")]
    # The last folder took its database's fragment with it.
    assert not (app / "databases.d" / "files.yaml").exists()
    AppConfig.model_validate(load_yaml_config(config))


def test_adding_a_retired_folder_again_keeps_its_documents(config, folder):
    docs = folder("x")
    register().add(docs, folder_id="x")
    register().remove("x")

    register().add(docs, folder_id="x")

    assert register().retired() == []


def test_a_change_the_configuration_refuses_is_undone(config, app, folder):
    register().add(folder("x"), folder_id="x")
    before = (app / "databases.d" / "files.yaml").read_bytes()
    reg = register()
    reg.databases = ["files", "ghost"]  # a database the configuration lacks
    with pytest.raises(FolderRegisterError, match="would not load"):
        reg.add(folder("y"), database="ghost")
    assert (app / "databases.d" / "files.yaml").read_bytes() == before
    assert not (app / "databases.d" / "ghost.yaml").exists()


def test_settings_need_a_databases_include(tmp_path, monkeypatch):
    path = tmp_path / "haiku.rag.yaml"
    path.write_text("environment: production\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="databases_include"):
        Settings.load(path)
    with pytest.raises(SettingsError, match="no such"):
        Settings.load(tmp_path / "missing.yaml")


def test_settings_name_the_ingester_api(config):
    settings = Settings.load()
    assert settings.ingester_url == "http://127.0.0.1:9"
    assert settings.databases == ["files"]
    assert settings.auth_token is None


def test_migrate_register_keeps_ids_and_retired_folders(config, app, folder, capsys):
    guitar, tum = folder("Guitar Books & Sheets"), folder("TUM")
    old = app / "folders.yaml"
    old.write_text(
        yaml.safe_dump(
            {
                "folders": [
                    {"id": "guitar-books-sheets", "database": "files", "path": str(guitar),
                     "include": [], "exclude": [".*"]},
                    {"id": "tum", "database": "files", "path": str(tum)},
                    {"id": "gone", "database": "files", "path": str(app / "missing")},
                ],
                "retired": [{"id": "old", "database": "files"}],
            }
        ),
        encoding="utf-8",
    )  # fmt: skip

    cli.main(["migrate-register", str(old)])

    assert [f.id for f in register().folders()] == ["guitar-books-sheets", "tum"]
    assert register().get("guitar-books-sheets").exclude == [".*"]
    assert register().retired() == [RetiredFolder("old", "files")]
    assert "Not migrated: gone" in capsys.readouterr().err
    cli.main(["migrate-register", str(old)])
    assert "nothing to migrate" in capsys.readouterr().out


def test_the_cli_lists_adds_and_removes(config, folder, capsys):
    docs = folder("notes")
    cli.main(["folders", "add", str(docs), "--exclude", ".*"])
    cli.main(["folders", "update", "notes", "--include", "*.md"])
    cli.main(["folders", "list"])
    out = capsys.readouterr()
    assert f"notes\tfiles\t{docs.resolve()}" in out.out
    assert "not running" in out.err
    assert register().get("notes").include == ["*.md"]
    cli.main(["folders", "remove", "notes", "--keep-documents"])
    assert register().folders() == [] and register().retired() == []
    with pytest.raises(SystemExit, match="No folder"):
        cli.main(["folders", "remove", "notes"])
