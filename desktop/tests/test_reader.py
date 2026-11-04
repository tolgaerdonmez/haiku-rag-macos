import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from haiku_rag_macos import reader as reader_module
from haiku_rag_macos.folders import FolderRegister
from haiku_rag_macos.hits import Hit, SearchResponse
from haiku_rag_macos.ingester_api import IngesterError, IngesterUnavailable
from haiku_rag_macos.reader import Reader, _exit_when_idle, build_app
from haiku_rag_macos.settings import Settings


class FakeSearch:
    loaded = True

    def __init__(self):
        self.calls = []

    async def search(self, query, **options):
        self.calls.append((query, options))
        hit = Hit(
            rank=1, score=0.5, chunk_id="c1", document_id="d1", database="files",
            folder_id="notes", title="t", path="/x/t.md", uri="file:///x/t.md",
            document_type="markdown", content_type=None, page_numbers=[], line=3,
            headings=None, labels=["text"], snippet="a b", snippet_highlights=[(0, 1)],
            text="a b", pages=[],
        )  # fmt: skip
        return SearchResponse(query=query, took_ms=1.0, hits=[hit])

    async def folder_documents(self, database, folder_id):
        return {"notes": 7}.get(folder_id, 0)

    async def document_counts(self):
        return [{"name": "files", "documents": 9}]

    async def aclose(self):
        pass


class FakeIngester:
    label = None

    def __init__(self, running=True):
        self.is_running = running
        self.reloads = 0
        self.dead = [
            {"id": "j1", "source_id": "notes", "uri": "file:///x/bad.pdf",
             "last_error": "bad pdf", "attempts": 2, "completed_at": "2026-11-04T10:00:00Z"},
            {"id": "j2", "source_id": "removed", "uri": "file:///y/z.pdf",
             "last_error": None, "attempts": 1, "completed_at": "2026-11-04T11:00:00Z"},
        ]  # fmt: skip
        self.retried = []

    def ensure_running(self):
        return self.is_running

    def kick(self):
        return False

    def reload(self, timeout=5.0):
        self.reloads += 1
        return {"outcome": "applied"}

    def stats(self):
        return {
            "queue_depth_by_source": {"notes": 3},
            "workers": {"busy": 1, "total": 4},
            "dlq_by_source": {"notes": 1, "removed": 5},
        }

    def sources(self):
        return [{"source_id": "notes", "last_polled_at": "2026-11-04T09:00:00Z"}]

    def dead_jobs(self, source_id=None, limit=50, offset=0):
        return [j for j in self.dead if source_id in (None, j["source_id"])]

    def retry_job(self, job_id):
        if job_id == "missing":
            raise IngesterError(404, "job not found")
        if job_id == "down":
            raise IngesterUnavailable("down")
        self.retried.append(job_id)
        return {"id": job_id}

    def retry_source(self, source_id):
        self.retried.append(source_id)
        return {"retried": 1}


@pytest.fixture
def service(config, folder):
    settings = Settings.load()
    FolderRegister.of(settings).add(folder("notes"), folder_id="notes")
    reader = Reader(settings)
    reader.search = FakeSearch()
    reader.ingester = FakeIngester()
    with TestClient(build_app(reader)) as client:
        client.reader = reader
        yield client


def test_search_passes_every_filter(service):
    response = service.get(
        "/search",
        params={"q": "chain rule", "limit": 5, "type": "fts", "database": "files",
                "folder": ["notes", "other"], "context": "true"},
    )  # fmt: skip
    assert response.status_code == 200
    assert response.json()["hits"][0]["line"] == 3
    query, options = service.reader.search.calls[-1]
    assert query == "chain rule"
    assert options == {
        "limit": 5, "search_type": "fts", "databases": ["files"],
        "folders": ["notes", "other"], "document_id": None, "context": True,
    }  # fmt: skip
    service.get("/documents/d1/search", params={"q": "x"})
    assert service.reader.search.calls[-1][1]["document_id"] == "d1"
    assert service.get("/search", params={"q": "x", "limit": 51}).status_code == 422


def test_folders_carry_the_ingesters_view(service, folder):
    [listed] = service.get("/folders").json()["folders"]
    assert listed["id"] == "notes"
    assert listed["status"] == {
        "watching": True, "documents": 7, "queued": 3, "failed": 1,
        "last_scan_at": "2026-11-04T09:00:00Z",
    }  # fmt: skip
    service.reader.ingester.is_running = False
    [stopped] = service.get("/folders").json()["folders"]
    assert stopped["status"]["watching"] is False and stopped["status"]["queued"] == 0


def test_adding_updating_and_removing_folders_reload_the_ingester(service, folder):
    docs = folder("papers")
    added = service.post("/folders", json={"path": str(docs), "exclude": [".*"]})
    assert added.status_code == 201
    assert added.json()["id"] == "papers" and added.json()["status"] is None
    assert service.post("/folders", json={"path": str(docs)}).status_code == 409
    assert service.post("/folders", json={"path": str(docs / "nope")}).status_code == 400
    patched = service.patch("/folders/papers", json={"include": ["*.pdf"]})
    assert patched.json()["include"] == ["*.pdf"]
    assert service.patch("/folders/nope", json={}).status_code == 404
    removed = service.delete("/folders/papers", params={"keep_documents": "false"})
    assert removed.json() == {"removed": "papers"}
    assert service.delete("/folders/papers").status_code == 404
    time.sleep(0.2)
    assert service.reader.ingester.reloads == 3


def test_status_counts_only_registered_folders_failures(service):
    status = service.get("/status").json()
    assert status == {
        "indexing": True, "queued": 2, "in_flight": 1, "failed": 1, "paused": False,
        "pause_reason": None, "ingester_running": True, "embedder_loaded": True,
        "databases": [{"name": "files", "documents": 9}],
    }  # fmt: skip


def test_failures_come_from_the_dead_letter_queue(service):
    [failure] = service.get("/failures").json()["failures"]
    assert failure == {
        "job_id": "j1", "folder_id": "notes", "database": "files", "path": "/x/bad.pdf",
        "uri": "file:///x/bad.pdf", "reason": "bad pdf", "attempts": 2,
        "failed_at": "2026-11-04T10:00:00Z",
    }  # fmt: skip
    assert service.get("/failures", params={"folder": "removed"}).json()["failures"][0][
        "reason"
    ] == "failed"
    service.reader.ingester.is_running = False
    assert service.get("/failures").status_code == 503


def test_retries_go_to_the_ingester(service):
    assert service.post("/failures/j1/retry").json() == {"retried": ["j1"]}
    assert service.post("/failures/missing/retry").status_code == 404
    assert service.post("/failures/down/retry").status_code == 503
    assert service.post("/failures/retry").json() == {"retried": ["j1"]}
    assert service.reader.ingester.retried == ["j1", "notes"]


def test_a_token_guards_everything_but_health(app, folder, monkeypatch):
    from .conftest import write_config

    path = write_config(app)
    text = path.read_text(encoding="utf-8").replace("port: 9\n", "port: 9\n    auth_token: s3cret\n")
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("HAIKU_RAG_CONFIG_PATH", str(path))
    reader = Reader(Settings.load())
    reader.search = FakeSearch()
    reader.ingester = FakeIngester()
    with TestClient(build_app(reader)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/status").status_code == 401
        ok = client.get("/status", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_the_service_exits_once_idle(config):
    reader = Reader(Settings.load())
    reader.last_request = time.monotonic() - 10

    class Server:
        should_exit = False

    server = Server()
    asyncio.run(asyncio.wait_for(_exit_when_idle(server, reader, 0.1), 5))
    assert server.should_exit


def test_serve_uses_launchds_socket(config, monkeypatch):
    import socket

    seen = {}

    class Server:
        should_exit = False

        def __init__(self, config):
            seen["config"] = config

        async def serve(self, sockets=None):
            seen["sockets"] = sockets

    monkeypatch.setattr("uvicorn.Server", Server)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    monkeypatch.setenv("HAIKU_RAG_LISTEN_FD", str(listener.fileno()))
    reader_module.serve(Settings.load(), idle_exit_s=0)
    assert seen["sockets"][0].fileno() == listener.fileno()
    monkeypatch.delenv("HAIKU_RAG_LISTEN_FD")
    reader_module.serve(Settings.load(), port=18765, idle_exit_s=60)
    assert seen["sockets"] is None and seen["config"].port == 18765
