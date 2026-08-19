"""Maintenance mode, timestamp coercion and the PostgreSQL admin surface.

Two of these guard bugs that took the whole admin panel down after cutover:
`fromisoformat` on a value PostgreSQL returns as a real datetime, and a
settings UPDATE that had no ON CONFLICT clause.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('ops','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "ops",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        # Admin mutations require a CSRF token. These tests exercise the
        # endpoints, not the CSRF guard itself (see tests/test_csrf.py).
        from app.auth.csrf import token_for_session
        c.headers.update({'X-CSRF-Token': token_for_session(token)})
        yield c


# ── Timestamp coercion (the bug that 500'd every admin request) ─────────

def test_as_datetime_accepts_text_and_datetime():
    """SQLite returns TEXT, PostgreSQL returns a datetime. Both must work."""
    from app.db.timeutil import as_datetime
    from datetime import datetime as dt, timezone
    assert as_datetime("2026-08-14T20:59:11").year == 2026
    aware = dt(2026, 8, 14, tzinfo=timezone.utc)
    assert as_datetime(aware) is aware
    assert as_datetime(None) is None and as_datetime("") is None


def test_as_datetime_survives_a_malformed_value():
    from app.db.timeutil import as_datetime
    assert as_datetime("not-a-timestamp") is None


def test_compare_now_matches_awareness():
    """Python refuses to compare aware with naive; the helper must not."""
    from app.db.timeutil import as_datetime, compare_now
    from datetime import datetime as dt, timezone
    aware = dt(2026, 1, 1, tzinfo=timezone.utc)
    naive = dt(2026, 1, 1)
    assert compare_now(aware) > aware
    assert compare_now(naive) > naive


def test_to_naive_utc_normalises_an_aware_value():
    from app.db.timeutil import to_naive_utc
    from datetime import datetime as dt, timezone, timedelta
    aware = dt(2026, 8, 14, 20, 0, tzinfo=timezone(timedelta(hours=3)))
    out = to_naive_utc(aware)
    assert out.tzinfo is None and out.hour == 17


def test_an_authenticated_admin_request_succeeds(client):
    """The regression itself: verify_admin parses the session expiry."""
    assert client.get("/admin/api/ops/maintenance").status_code == 200


# ── Maintenance mode ────────────────────────────────────────────────────

def test_maintenance_defaults_to_off(client):
    assert client.get("/admin/api/ops/maintenance").json()["enabled"] is False


def test_maintenance_records_who_why_and_when(client):
    r = client.post("/admin/api/ops/maintenance",
                    json={"enabled": True, "reason": "بازیابی پایگاه داده"})
    assert r.status_code == 200
    state = client.get("/admin/api/ops/maintenance").json()
    assert state["enabled"] is True
    assert state["reason"] == "بازیابی پایگاه داده"
    assert state["enabled_by"] == "ops"
    assert state["enabled_at"]


def test_maintenance_state_is_shared_not_per_process(client):
    """Stored in the database, so every worker observes the same value.
    A module-level flag would leave sibling workers still accepting writes."""
    client.post("/admin/api/ops/maintenance", json={"enabled": True, "reason": "x"})
    from app.db.queries import get_setting
    assert "enabled" in (get_setting("maintenance_state", "") or "")
    from app.services import maintenance
    assert maintenance.is_enabled() is True


def test_maintenance_blocks_visitor_writes_but_not_the_admin_panel(client):
    """An operator locked out by their own maintenance mode could never turn
    it off again."""
    client.post("/admin/api/ops/maintenance", json={"enabled": True, "reason": "x"})
    chat = client.post("/chat", json={"message": "سلام", "lang": "fa"},
                       headers={"Origin": "http://localhost",
                                "User-Agent": "Mozilla/5.0 (test)"})
    assert chat.status_code == 503
    assert client.get("/admin/api/ops/maintenance").status_code == 200
    assert client.post("/admin/api/ops/maintenance",
                       json={"enabled": False}).status_code == 200


def test_maintenance_survives_a_fresh_read(client):
    """State must come from storage each time, not a cached module global."""
    client.post("/admin/api/ops/maintenance", json={"enabled": True, "reason": "y"})
    import importlib
    from app.services import maintenance
    importlib.reload(maintenance)
    assert maintenance.is_enabled() is True


def test_maintenance_requires_admin(client):
    """Reuses the isolated `client` fixture and simply drops the cookie.

    Building a second TestClient here without the fixture's DB_PATH patch made
    the test boot against the REAL database and seed default content — 46.9s
    for one assertion, and it touched the operator's data.
    """
    client.cookies.clear()
    assert client.get("/admin/api/ops/maintenance").status_code == 401
    assert client.post("/admin/api/ops/maintenance",
                       json={"enabled": True}).status_code == 401


# ── Upsert semantics (settings UPDATE was silently broken) ──────────────

def test_updating_an_existing_setting_works(client):
    """INSERT OR REPLACE on an EXISTING key: this raised UniqueViolation under
    PostgreSQL until the adapter learned ON CONFLICT ... DO UPDATE."""
    from app.db.queries import get_setting, set_setting
    set_setting("upsert_probe", "first")
    set_setting("upsert_probe", "second")
    assert get_setting("upsert_probe", "") == "second"


# ── Restore preconditions ───────────────────────────────────────────────

def test_restore_refuses_a_malformed_backup_id():
    from app.services import pg_backup
    for bad in ("../../etc/passwd", "/etc/passwd", "", "pg_bad"):
        with pytest.raises(pg_backup.BackupError):
            pg_backup.restore(bad, actor="t", confirmation="x")


def test_restore_requires_the_exact_confirmation_string():
    from app.services import pg_backup
    with pytest.raises(pg_backup.BackupError):
        pg_backup.restore("pg_20260101_120000_abc123", actor="t",
                          confirmation="RESTORE BACKUP wrong")


def test_restore_reports_the_multi_process_limitation():
    """The result must state that sibling processes need restarting rather
    than implying coordination this deployment does not have."""
    import inspect
    from app.services import pg_backup
    source = inspect.getsource(pg_backup.restore)
    assert "restart_required_for_other_processes" in source


def test_validation_checks_more_than_an_exit_code():
    import inspect
    from app.services import pg_backup
    source = inspect.getsource(pg_backup.validate_restored_database)
    for check in ("schemas_present", "admin_accounts", "password_hashes_intact",
                  "persian_readable", "settings_readable", "migration_revision"):
        assert check in source, f"{check} missing from post-restore validation"


# ── PostgreSQL admin surface ────────────────────────────────────────────

def test_sqlite_endpoints_are_gated_when_running_on_postgres(monkeypatch, client):
    """Post-cutover the SQLite views describe a store the app no longer uses.

    Patch the router's own predicate rather than config.DB_BACKEND: flipping
    the backend would also reroute get_db_connection(), so the session lookup
    would miss and the request would 401 before ever reaching the guard.
    """
    import app.routers.dbadmin as dbadmin
    monkeypatch.setattr(dbadmin, "_postgres", lambda: True)
    assert client.get("/admin/api/infra/database").status_code == 400
    assert client.get("/admin/api/infra/database/app/tables").status_code == 400


def test_storage_stays_available_because_it_is_engine_agnostic(monkeypatch, client):
    import app.routers.dbadmin as dbadmin
    monkeypatch.setattr(dbadmin, "_postgres", lambda: True)
    assert client.get("/admin/api/infra/storage").status_code == 200


def test_the_database_page_shows_postgres_and_not_sqlite_controls():
    """Rendered template, not the API: an operator must not be offered PRAGMA
    or WAL checkpoint for a database that has neither."""
    from pathlib import Path
    html = Path("templates/admin/infra_database.html").read_text()
    assert "PostgreSQL" in html
    for gone in ("PRAGMA", "WAL checkpoint", "journal_mode", "wal_checkpoint"):
        assert gone not in html, f"SQLite control {gone!r} still rendered"
    for shown in ("استخر اتصال", "نسخهٔ اسکیما", "autovacuum", "انتظار قفل"):
        assert shown in html


def test_the_database_page_offers_no_locking_operation():
    from pathlib import Path
    html = Path("templates/admin/infra_database.html").read_text()
    # These appear only inside the explanatory note about why they are absent.
    assert html.count("VACUUM FULL") <= 1
    assert "اجرای دستور دلخواه" in html


def test_database_page_js_never_uses_innerhtml():
    from pathlib import Path
    js = Path("static/admin/js/infra_database.js").read_text()
    for line in js.splitlines():
        if "innerHTML" in line and not line.strip().startswith("*"):
            pytest.fail(f"innerHTML used with dynamic data: {line.strip()}")
