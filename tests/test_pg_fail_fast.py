"""pg.set_unavailable(): a process-local "the database is down, stop waiting".

WHY THIS EXISTS
---------------
Every PostgreSQL access goes through pg.pool(), and a checkout from a pool
that cannot reach the server waits DB_CONNECT_TIMEOUT (10 s in production)
before it gives up. One SMS sent through app/services/sms.py during an outage
made 11 such attempts (eight gateway settings, two log-level settings, one log
write): about 110 s. The watchdog sends two, inside a 300 s unit timeout.

The watchdog is a one-shot process. Once its own strict settings read has
failed, it calls set_unavailable(), and every later checkout in that process
fails at once. The app server never calls it, and the flag lives only in this
process's memory: nothing is written to the environment, a file or the
database.
"""
import ast
import pathlib
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def pg_module(monkeypatch):
    import app.config as config
    from app.db import pg

    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://padyar:x@127.0.0.1:1/padyar")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "1")
    monkeypatch.setattr(pg, "_pool", None)
    monkeypatch.setattr(pg, "_unavailable", False)
    yield pg
    pg.close_pool()


def test_the_switch_is_off_in_a_fresh_process():
    out = subprocess.run(
        [sys.executable, "-c", "from app.db import pg; print(pg._unavailable)"],
        cwd=REPO, capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_after_the_switch_every_connection_fails_at_once(pg_module):
    pg_module.set_unavailable()

    started = time.monotonic()
    with pytest.raises(pg_module.DatabaseUnavailable):
        pg_module.connect()
    ok, detail = pg_module.healthy()
    elapsed = time.monotonic() - started

    assert ok is False and detail == "DatabaseUnavailable"
    assert elapsed < 0.5, f"waited {elapsed:.2f}s for a database already known down"
    assert pg_module._pool is None, "the switch must not open a pool"


def test_the_switch_closes_a_pool_that_is_already_open(pg_module):
    pg_module.pool()
    assert pg_module._pool is not None

    pg_module.set_unavailable()

    assert pg_module._pool is None


def test_the_switch_does_not_wait_for_the_pool_threads(pg_module):
    closed_with = []

    class StuckPool:
        def close(self, timeout=5.0):
            closed_with.append(timeout)

    pg_module._pool = StuckPool()

    pg_module.set_unavailable()

    assert closed_with == [0], \
        "a thread stuck in connect() would hold the close for its full timeout"


def test_database_unavailable_is_an_ordinary_operational_error():
    import psycopg
    from app.db import pg

    assert issubclass(pg.DatabaseUnavailable, psycopg.OperationalError)


def test_the_switch_does_not_leave_this_process(pg_module):
    pg_module.set_unavailable()

    out = subprocess.run(
        [sys.executable, "-c", "from app.db import pg; print(pg._unavailable)"],
        cwd=REPO, capture_output=True, text=True, timeout=60, check=True)

    assert out.stdout.strip().splitlines()[-1] == "False", \
        "a child process inherited the switch, so it is not process-local"


def test_nothing_in_the_app_turns_the_switch_on():
    callers = []
    for path in (REPO / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "set_unavailable":
                    callers.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert callers == [], f"the app server must never call set_unavailable: {callers}"
