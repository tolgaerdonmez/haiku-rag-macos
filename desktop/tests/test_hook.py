import asyncio
import logging
from types import SimpleNamespace

from haiku.rag.config.models import HookConfig
from haiku.rag.hooks import Hook, build_hooks, load_hooks

from haiku_rag_macos.folders import FolderRegister, RetiredFolder
from haiku_rag_macos.hook import FolderRetirement, source_filter


class FakeClient:
    """The writable client haiku-ingester hands an ingester lifespan."""

    def __init__(self, source: str, documents: dict[str, str]):
        self.source = source
        self.documents = dict(documents)  # id -> source_id
        self.filters: list[str] = []
        self.fail = False

    async def list_documents(self, filter=None):
        self.filters.append(filter)
        if self.fail:
            raise RuntimeError("store unreachable")
        return [
            SimpleNamespace(id=doc_id, metadata={"source_id": owner})
            for doc_id, owner in self.documents.items()
        ]

    async def delete_document(self, document_id, *, source_id=None):
        assert self.documents[document_id] == source_id
        del self.documents[document_id]
        return True


def enter(hook: Hook, client) -> None:
    async def run():
        async with hook.ingester_lifespan(client):
            pass

    asyncio.run(run())


def test_the_hook_is_registered_for_haiku_rag():
    [hook] = build_hooks(
        [HookConfig(name="macos-folders", options={"directory": "/tmp/x"})], load_hooks()
    )
    assert isinstance(hook, FolderRetirement)


def test_source_filter_escapes_like_and_quotes():
    assert source_filter("tum") == "metadata LIKE '%\"source_id\": \"tum\"%' ESCAPE '\\'"
    assert "o''brien\\_100\\%" in source_filter("o'brien_100%")


def test_a_retired_folders_documents_are_deleted_once(tmp_path, folder, caplog):
    directory = tmp_path / "databases.d"
    register = FolderRegister(directory, ["files"])
    register.add(folder("kept"), folder_id="kept")
    register.retire("gone", "files")
    register.retire("other-db", "elsewhere")
    client = FakeClient("files", {"a": "gone", "b": "gone", "c": "kept"})

    with caplog.at_level(logging.INFO, "haiku.rag.hooks.macos_folders"):
        enter(FolderRetirement(str(directory)), client)

    assert client.documents == {"c": "kept"}
    assert register.retired() == [RetiredFolder("other-db", "elsewhere")]
    assert "deleted its 2 document(s)" in caplog.text
    enter(FolderRetirement(str(directory)), client)
    assert len(client.filters) == 1


def test_a_folder_registered_again_keeps_its_documents(tmp_path, folder):
    directory = tmp_path / "databases.d"
    register = FolderRegister(directory, ["files"])
    register.retire("back", "files")
    register.add(folder("back"), folder_id="back")
    register.retire("back", "files")  # retired, then the same id added again
    register.add(folder("back2"), folder_id="other")
    client = FakeClient("files", {"a": "back"})
    # add() drops a retired entry; write one back as a racing removal would.
    register.retire("back", "files")

    enter(FolderRetirement(str(directory)), client)

    assert client.documents == {"a": "back"}
    assert register.retired() == []


def test_a_failure_is_logged_and_the_database_still_starts(tmp_path, caplog):
    directory = tmp_path / "databases.d"
    FolderRegister(directory, ["files"]).retire("gone", "files")
    client = FakeClient("files", {"a": "gone"})
    client.fail = True

    enter(FolderRetirement(str(directory)), client)

    assert "Could not delete" in caplog.text
    assert FolderRegister(directory, ["files"]).retired() == [RetiredFolder("gone", "files")]
