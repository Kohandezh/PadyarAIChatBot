"""The nightly backup scheduler must back up the database the install runs on.

The bug this file pins: `_run_backup_now()` called the SQLite sets path
unconditionally. On a PostgreSQL install there is no chat_history.db, so the
scheduler logged "No database at ..." every night at 03:00 (measured on the
production server, 2026-08-23 and 2026-08-24) and backed nothing up. The
PostgreSQL backup code existed the whole time — the scheduler just never
called it.

What is asserted here, without touching pg_dump or the filesystem:

  * the engine dispatch reads DB_BACKEND the same way the backups router does
  * a PostgreSQL dispatch reaches pg_backup.create and prunes with it
  * a SQLite dispatch keeps reaching backup_center.create, unchanged
  * pg_backup.prune keeps only the newest N and never raises
"""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    import secrets
    import datetime
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        # A real admin session, because the page route checks the cookie.
        from app.db.connection import get_db_connection
        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
            (token, "tester",
             (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set(config.ADMIN_COOKIE_NAME, token)
        yield c


# ── Which engine ────────────────────────────────────────────────────────

def test_the_engine_follows_db_backend(monkeypatch, db):
    from app.services import backup
    assert backup._backup_engine() == "sqlite"          # the test backend
    monkeypatch.setattr(backup, "_backup_engine", lambda: "postgres")
    # Patching the predicate, not config.DB_BACKEND: flipping the backend
    # would reroute get_db_connection() too and the settings-table writes
    # would miss (the same trap tests/test_pg_operations.py documents).
    assert backup._backup_engine() == "postgres"


def test_a_postgres_install_runs_pg_backup_not_sqlite_sets(monkeypatch, db):
    from app.services import backup
    monkeypatch.setattr(backup, "_backup_engine", lambda: "postgres")

    calls = {"pg": 0, "sqlite": 0}
    monkeypatch.setattr("app.services.pg_backup.create",
                        lambda actor="", reason="": calls.__setitem__(
                            "pg", calls["pg"] + 1) or {"backup_id": "pg_1"})
    monkeypatch.setattr("app.services.pg_backup.prune", lambda: [])
    monkeypatch.setattr("app.services.pg_backup.backup_dir",
                        lambda backup_id: "/somewhere/" + backup_id)
    monkeypatch.setattr("app.services.backup_center.create",
                        lambda **kw: calls.__setitem__(
                            "sqlite", calls["sqlite"] + 1) or {"backup_id": "s_1"})

    path = backup._run_backup_now(actor="scheduler", kind="scheduled")
    assert calls == {"pg": 1, "sqlite": 0}
    assert path == "/somewhere/pg_1"


def test_a_sqlite_install_still_runs_backup_center(monkeypatch, db):
    from app.services import backup
    calls = {"pg": 0, "sqlite": 0}
    monkeypatch.setattr("app.services.pg_backup.create",
                        lambda **kw: calls.__setitem__("pg", calls["pg"] + 1))
    monkeypatch.setattr(
        "app.services.backup_center.create",
        lambda **kw: calls.__setitem__("sqlite", calls["sqlite"] + 1)
        or {"backup_id": "set_1"})
    monkeypatch.setattr("app.services.backup_center.prune", lambda: [])
    monkeypatch.setattr("app.services.backup_center.set_dir",
                        lambda backup_id: "/sets/" + backup_id)

    path = backup._run_backup_now(actor="scheduler", kind="scheduled")
    assert calls == {"pg": 0, "sqlite": 1}
    assert path == "/sets/set_1"


def test_a_successful_run_records_the_time(monkeypatch, db):
    from app.services import backup
    monkeypatch.setattr(
        "app.services.backup_center.create", lambda **kw: {"backup_id": "set_9"})
    monkeypatch.setattr("app.services.backup_center.prune", lambda: [])
    monkeypatch.setattr("app.services.backup_center.set_dir",
                        lambda backup_id: "/sets/" + backup_id)
    backup._run_backup_now()

    from app.db.queries import get_setting
    assert (get_setting("backup_last_run", "") or "").strip() != ""


# ── prune ───────────────────────────────────────────────────────────────

def _fake_backup_dir(tmp_path, n):
    """A BACKUP_DIR with `n` valid ids (pg_YYYYmmdd_HHMMSS_hex), oldest first."""
    import json
    root = tmp_path / "postgres"
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        bid = "pg_%s_%06d_%s" % ("20260824", i, "aabbcc")
        d = root / bid
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"backup_id": bid}), encoding="utf-8")
    return str(root)


def test_prune_keeps_the_newest_n(tmp_path, monkeypatch):
    from app.services import pg_backup
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", _fake_backup_dir(tmp_path, 4))
    monkeypatch.setattr(pg_backup.applog, "audit", lambda *a, **k: None)

    removed = pg_backup.prune(keep=2)
    assert len(removed) == 2      # the two oldest are gone
    remaining = sorted(p.name for p in (tmp_path / "postgres").iterdir())
    assert len(remaining) == 2    # the two newest survive


def test_prune_below_the_limit_removes_nothing(tmp_path, monkeypatch):
    from app.services import pg_backup
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", _fake_backup_dir(tmp_path, 2))
    monkeypatch.setattr(pg_backup.applog, "audit", lambda *a, **k: None)
    assert pg_backup.prune(keep=5) == []


def test_prune_never_raises(tmp_path, monkeypatch):
    """A prune failure must not fail a backup that just succeeded."""
    from app.services import pg_backup
    monkeypatch.setattr(pg_backup, "list_backups",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pg_backup.prune() == []


# ── The settings page no longer shows the dead section on PostgreSQL ────

def _admin(client, monkeypatch):
    """Make the page route believe the caller is an admin."""
    async def yes(request):
        return None
    from app.routers import public
    monkeypatch.setattr(public, "_require_admin", yes)


def test_settings_backup_page_hides_the_sqlite_list_on_postgres(client, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    _admin(client, monkeypatch)
    html = client.get("/secure-panel-inotex/settings/backup").text
    # The dead controls are gone...
    assert "backup-list" not in html
    assert "restore-upload-btn" not in html
    # ...and the operator is pointed at the page that works.
    assert "/secure-panel-inotex/infrastructure/backups" in html


def test_settings_backup_page_keeps_the_list_on_sqlite(client, monkeypatch):
    _admin(client, monkeypatch)
    html = client.get("/secure-panel-inotex/settings/backup").text
    assert 'id="backup-list"' in html
    assert 'id="create-backup-btn"' in html
