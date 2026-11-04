"""Runs `haiku-ingester serve` and stops it once it has been idle.

haiku-ingester runs until it is stopped. On a laptop it need not stay up
between bursts of work, so this parent runs it as its child and watches its
`/stats`: once no job has been queued or in flight for `idle_exit_s`, and every
maintained database has had its maintenance pass since then, it stops the
ingester and exits successfully. launchd starts it again at login, on its
interval, when the folder fragments change, and when the search service
starts. Any other exit of the ingester, such as the one after a stalled
conversion, is passed on, so launchd's KeepAlive restarts it.
"""

import logging
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from haiku_rag_macos.ingester_api import IngesterAPI, IngesterError, IngesterUnavailable

logger = logging.getLogger(__name__)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class IdleWatch:
    """Decides from successive `/stats` whether the ingester is idle."""

    def __init__(self, idle_exit_s: float, maintenance: bool, now: float, wall: datetime):
        self.idle_exit_s = idle_exit_s
        self.maintenance = maintenance
        self.last_active = now
        self.last_active_wall = wall

    def observe(self, stats: dict, now: float, wall: datetime) -> bool:
        """Whether, given these stats, the ingester may stop."""
        depth = sum((stats.get("queue_depth_by_source") or {}).values())
        busy = (stats.get("workers") or {}).get("busy", 0)
        if depth or busy:
            self.last_active, self.last_active_wall = now, wall
            return False
        if now - self.last_active < self.idle_exit_s:
            return False
        if self.maintenance:
            for database in (stats.get("maintenance_by_database") or {}).values():
                finished = _parse(database.get("finished_at"))
                if finished is None or finished < self.last_active_wall:
                    return False
        return True


def run(
    ingester: Path,
    config: Path,
    api: IngesterAPI,
    *,
    idle_exit_s: float,
    maintenance: bool,
    check_every_s: float = 30.0,
) -> int:
    child = subprocess.Popen([str(ingester), "--config", str(config), "serve"])

    def forward(signum, _frame) -> None:
        if child.poll() is None:
            child.send_signal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)

    watch = IdleWatch(idle_exit_s, maintenance, time.monotonic(), datetime.now(UTC))
    while True:
        try:
            return child.wait(timeout=check_every_s)
        except subprocess.TimeoutExpired:
            pass
        if idle_exit_s <= 0:
            continue
        try:
            stats = api.stats()
        except (IngesterUnavailable, IngesterError, TimeoutError):
            continue  # starting, or busy answering: not idle
        if watch.observe(stats, time.monotonic(), datetime.now(UTC)):
            logger.info("Idle for %.0f s; stopping until launchd starts it again", idle_exit_s)
            child.send_signal(signal.SIGTERM)
            code = child.wait()
            return 0 if code in (0, -signal.SIGTERM) else code


def ingester_command() -> Path:
    """haiku-ingester of this environment."""
    return Path(sys.executable).parent / "haiku-ingester"
