"""Infrastructure -> Backups: the admin API must speak one contract regardless
of which engine is behind it.

The bug this file pins: backup_center (SQLite) and pg_backup (PostgreSQL)
store manifests in different shapes -- files/total_bytes/kind/
verification.state vs file/bytes/reason/verification.status. The admin
panel's JS (static/admin/js/infra_backups.js) was written only for the
SQLite shape. Production defaults to DB_BACKEND=postgres
(app/config.py:DB_BACKEND), so GET /admin/api/infra/backups returned
pg_backup's raw manifests, renderRows()/filesCell() threw a TypeError on the
missing `files` key, and the backup list never rendered -- the page always
showed "خطای ارتباط با سرور" instead.

These tests run against a *fake* pg engine (no real pg_dump/pg_restore) and
assert the router normalizes its shape into what the frontend actually reads.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    return tmp_path


@pytest.fixture
def client(paths):
    from app.main import app
    from app.routers import backups as backups_router
    if not any(str(getattr(r, "path", "")).startswith("/admin/api/infra/backups")
               for r in app.routes):
        app.include_router(backups_router.router)
    with TestClient(app) as c:
        yield c


def _login(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
        (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)


def _csrf(client):
    return client.get("/admin/csrf").json()["csrf_token"]


BACKUP_ID = "pg_20260830_120000_ab12cd"


class FakePgEngine:
    """Stands in for app.services.pg_backup without touching pg_dump/pg_restore."""

    def __init__(self, dump_path):
        self.manifest = {
            "backup_id": BACKUP_ID,
            "created_at": "2026-08-30T12:00:00+00:00",
            "created_by": "tester",
            "engine": "postgresql",
            "database": "padyar",
            "format": "custom",
            "file": "padyar.dump",
            "bytes": 42,
            "sha256": "deadbeef",
            "duration_ms": 500,
            "reason": "manual",
            "verification": {"status": "verified",
                              "checked_at": "2026-08-30T12:05:00+00:00",
                              "problems": []},
        }
        self.dump_path = dump_path

    def list_backups(self):
        return [self.manifest]

    def verify(self, backup_id, actor=""):
        return self.manifest

    def member_path(self, backup_id, name):
        return self.dump_path if name == "padyar.dump" else None


@pytest.fixture
def fake_pg(monkeypatch, tmp_path):
    dump = tmp_path / "padyar.dump"
    dump.write_bytes(b"fake-dump-bytes")
    engine = FakePgEngine(str(dump))
    from app.routers import backups as backups_router
    monkeypatch.setattr(backups_router, "_engine", lambda: (engine, True))
    return engine


def test_list_returns_the_shape_the_frontend_reads(client, fake_pg):
    _login(client)
    res = client.get("/admin/api/infra/backups")
    assert res.status_code == 200
    body = res.json()
    assert body["engine"] == "postgresql"
    row = body["backups"][0]
    # Exactly the keys static/admin/js/infra_backups.js reads off each row --
    # renderRows()/filesCell() throw a TypeError without them, which is why
    # the list never rendered at all.
    assert row["kind"] == "manual"
    assert row["total_bytes"] == 42
    assert row["files"] == [{
        "name": "padyar.dump",
        "label": "پایگاه‌دادهٔ پستگرس (pg_dump)",
        "bytes": 42,
    }]
    assert row["verification"]["state"] == "verified"


def test_a_row_with_an_unreadable_manifest_still_renders(client, fake_pg):
    fake_pg.list_backups = lambda: [{"backup_id": "pg_broken", "error": "manifest unreadable"}]
    _login(client)
    row = client.get("/admin/api/infra/backups").json()["backups"][0]
    assert row["files"] == []
    assert row["verification"]["state"] == "unknown"


def test_verify_returns_ok_not_status(client, fake_pg):
    _login(client)
    res = client.post(f"/admin/api/infra/backups/{BACKUP_ID}/verify",
                      headers={"X-CSRF-Token": _csrf(client)})
    assert res.status_code == 200
    body = res.json()
    # verifyBackup() in infra_backups.js reads `data.ok`; pg_backup.verify()
    # only ever set verification.status, so this key was always missing.
    assert body["ok"] is True
    assert body["problems"] == []


def test_download_serves_the_pg_dump_file(client, fake_pg):
    _login(client)
    res = client.get(f"/admin/api/infra/backups/{BACKUP_ID}/download")
    assert res.status_code == 200
    assert res.content == b"fake-dump-bytes"


def test_download_rejects_a_name_outside_the_manifest(client, fake_pg):
    _login(client)
    res = client.get(f"/admin/api/infra/backups/{BACKUP_ID}/download",
                     params={"file": "../../etc/passwd"})
    assert res.status_code == 404
