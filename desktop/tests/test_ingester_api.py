import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from haiku_rag_macos.ingester_api import IngesterAPI, IngesterError, IngesterUnavailable


class Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def answer(self, code, body):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Handler.seen.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/health":
            self.answer(200, {"status": "ok"})
        elif self.path == "/stats":
            self.answer(200, {"workers": {"busy": 0}})
        elif self.path == "/sources":
            self.answer(200, [{"source_id": "tum"}])
        elif self.path.startswith("/dlq"):
            self.answer(200, [{"id": "j1"}])
        elif self.path == "/text-error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"not json")
        else:
            self.answer(404, {"detail": "not found"})

    def do_POST(self):
        Handler.seen.append(("POST", self.path, None))
        if self.path == "/reload":
            if Handler.slow:
                time.sleep(1)
            self.answer(500, {"outcome": "failed"})
        elif self.path.startswith("/dlq/"):
            self.answer(200, {"id": "j1"})
        else:
            self.answer(200, {"retried": 2})


@pytest.fixture
def server():
    Handler.seen = []
    Handler.slow = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_the_api_calls(server):
    api = IngesterAPI(server + "/", token="t0k")
    assert api.running()
    assert api.stats() == {"workers": {"busy": 0}}
    assert api.sources() == [{"source_id": "tum"}]
    assert api.dead_jobs("tum", 10, 5) == [{"id": "j1"}]
    assert api.retry_job("a/b") == {"id": "j1"}
    assert api.retry_source("tum") == {"retried": 2}
    assert ("GET", "/dlq?source_id=tum&limit=10&offset=5", "Bearer t0k") in Handler.seen
    assert ("POST", "/dlq/a%2Fb/retry", None) in Handler.seen
    assert ("POST", "/sources/tum/retry?status=dead", None) in Handler.seen
    # A failed reload answers 500 with its report.
    with pytest.raises(IngesterError) as raised:
        api.reload()
    assert raised.value.status == 500
    with pytest.raises(IngesterError, match="not found"):
        api.get("/nothing")
    with pytest.raises(IngesterError) as text:
        api.get("/text-error")
    assert text.value.status == 500


def test_a_slow_reload_is_pending(server):
    Handler.slow = True
    assert IngesterAPI(server).reload(timeout=0.2) == {"outcome": "pending"}


def test_a_stopped_ingester(monkeypatch):
    api = IngesterAPI("http://127.0.0.1:9")
    assert not api.running()
    assert api.reload() is None
    with pytest.raises(IngesterUnavailable):
        api.stats()
    # No agent to start.
    assert not api.kick() and not api.ensure_running()


def test_ensure_running_starts_the_agent(monkeypatch):
    calls = []

    class Done:
        returncode = 0

    def fake_run(command, **options):
        calls.append(command)
        return Done()

    monkeypatch.setattr("subprocess.run", fake_run)
    api = IngesterAPI("http://127.0.0.1:9", label="com.example.ingester")
    states = iter([False, False, True])
    monkeypatch.setattr(api, "running", lambda: next(states))
    assert api.ensure_running(wait_s=2)
    assert calls[0][:2] == ["/bin/launchctl", "kickstart"]
    assert calls[0][2].endswith("/com.example.ingester")
    states = iter([False] + [False] * 100)
    monkeypatch.setattr(api, "running", lambda: next(states))
    assert not api.ensure_running(wait_s=0.3)
