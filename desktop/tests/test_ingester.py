import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from haiku_rag_macos.ingester import IdleWatch, ingester_command, run
from haiku_rag_macos.ingester_api import IngesterUnavailable

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def stats(depth=0, busy=0, finished=None):
    return {
        "queue_depth_by_source": {"tum": depth} if depth else {},
        "workers": {"busy": busy, "total": 4},
        "maintenance_by_database": {
            "files": {"finished_at": finished.isoformat() if finished else None}
        },
    }


def test_idle_only_after_the_quiet_period_and_a_later_maintenance_pass():
    watch = IdleWatch(600, True, now=0, wall=T0)
    assert not watch.observe(stats(), 300, T0 + timedelta(seconds=300))
    # Work arrives: the quiet period starts again.
    assert not watch.observe(stats(depth=3, busy=2), 400, T0 + timedelta(seconds=400))
    assert not watch.observe(stats(finished=T0), 1100, T0 + timedelta(seconds=1100))
    # Maintenance ran after the last work.
    done = T0 + timedelta(seconds=500)
    assert watch.observe(stats(finished=done), 1100, T0 + timedelta(seconds=1100))


def test_without_maintenance_quiet_is_enough():
    watch = IdleWatch(60, False, now=0, wall=T0)
    assert watch.observe(stats(), 61, T0)
    stamped = IdleWatch(60, True, now=0, wall=T0)
    assert stamped.observe(
        {**stats(), "maintenance_by_database": {"files": {"finished_at": "2026-11-04T12:05:00Z"}}},
        61,
        T0,
    )


class FakeAPI:
    def __init__(self, answers):
        self.answers = list(answers)

    def stats(self):
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def fake_ingester(tmp_path: Path) -> Path:
    """A haiku-ingester that serves until SIGTERM, or exits with $EXIT_WITH."""
    script = tmp_path / "haiku-ingester"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "if 'EXIT_WITH' in os.environ: sys.exit(int(os.environ['EXIT_WITH']))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_an_idle_ingester_is_stopped_and_the_exit_is_successful(fake_ingester, tmp_path):
    api = FakeAPI([IngesterUnavailable("starting"), stats()])
    code = run(
        fake_ingester, tmp_path / "c.yaml", api, idle_exit_s=0.01, maintenance=False,
        check_every_s=0.05,
    )  # fmt: skip
    assert code == 0


def test_any_other_exit_is_passed_on(fake_ingester, tmp_path, monkeypatch):
    monkeypatch.setenv("EXIT_WITH", "1")
    code = run(
        fake_ingester, tmp_path / "c.yaml", FakeAPI([stats()]), idle_exit_s=0,
        maintenance=False, check_every_s=0.05,
    )  # fmt: skip
    assert code == 1


def test_signals_reach_the_ingester(fake_ingester, tmp_path):
    busy = FakeAPI([stats(depth=1)])
    timer = threading.Timer(0.5, lambda: signal.raise_signal(signal.SIGTERM))
    previous = signal.getsignal(signal.SIGTERM)
    timer.start()
    try:
        code = run(
            fake_ingester, tmp_path / "c.yaml", busy, idle_exit_s=3600, maintenance=False,
            check_every_s=0.05,
        )  # fmt: skip
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert code == 0


def test_the_ingester_command_is_beside_the_interpreter():
    assert ingester_command() == Path(sys.executable).parent / "haiku-ingester"
