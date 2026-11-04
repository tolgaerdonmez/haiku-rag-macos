"""A small client for haiku-ingester's HTTP API (upstream's control plane):
`/health`, `/stats`, `/sources`, `/dlq`, `/reload` and source retries."""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urlencode


class IngesterUnavailable(RuntimeError):
    """haiku-ingester does not answer."""


class IngesterError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class IngesterAPI:
    def __init__(self, base_url: str, token: str | None = None, label: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        # The ingester's launchd label, to start it when it is not running.
        self.label = label

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urlencode(
                {k: v for k, v in params.items() if v is not None}, doseq=True
            )
        request = urllib.request.Request(url, method=method)
        request.add_header("Accept", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            detail = error.reason
            try:
                payload = json.loads(error.read())
                detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            except (ValueError, OSError):
                pass
            raise IngesterError(error.code, str(detail)) from None
        except TimeoutError:
            # Connected, and no answer in time: the ingester is busy with it.
            raise
        except (urllib.error.URLError, OSError) as error:
            raise IngesterUnavailable(f"haiku-ingester at {self.base_url}: {error}") from None
        return json.loads(body) if body else None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def running(self) -> bool:
        try:
            self.request("GET", "/health", timeout=1.0)
        except (IngesterUnavailable, IngesterError):
            return False
        return True

    def kick(self) -> bool:
        """Have launchd start the ingester's agent unless it runs. False when
        there is no agent to start."""
        if not self.label:
            return False
        result = subprocess.run(
            ["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{self.label}"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def ensure_running(self, wait_s: float = 3.0) -> bool:
        """Whether the ingester answers, starting it and waiting up to
        `wait_s` seconds for it when it does not."""
        if self.running():
            return True
        if not self.kick():
            return False
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if self.running():
                return True
        return False

    def reload(self, timeout: float = 5.0) -> dict | None:
        """Ask a running ingester to read its configuration again. The reload
        goes on when the caller stops waiting. None when it is not running:
        it reads the configuration when it starts."""
        try:
            return self.request("POST", "/reload", timeout=timeout)
        except IngesterUnavailable:
            return None
        except TimeoutError:
            return {"outcome": "pending"}

    def stats(self) -> dict:
        return self.get("/stats")

    def sources(self) -> list[dict]:
        return self.get("/sources")

    def dead_jobs(self, source_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        return self.get("/dlq", source_id=source_id, limit=limit, offset=offset)

    def retry_job(self, job_id: str) -> dict:
        return self.request("POST", f"/dlq/{quote(job_id, safe='')}/retry")

    def retry_source(self, source_id: str) -> dict:
        return self.request(
            "POST", f"/sources/{quote(source_id, safe='')}/retry", {"status": "dead"}
        )
