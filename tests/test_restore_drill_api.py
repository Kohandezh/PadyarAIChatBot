"""Infrastructure -> Backups: the restore drill door (manual run + list + page).

What this file pins (SPEC-B2 acceptance criteria 10 and 11):

  * POST /admin/api/infra/backups/{id}/drill sits behind the same admin door
    and CSRF check as its siblings. It answers 202 at once and the drill runs
    in the background thread restore_drill.start() makes.
  * A bad id is 404, a busy drill is 409, SQLite is 409. Each deny-case has an
    allow-control next to it, so a test cannot pass by always refusing.
  * GET /admin/api/infra/backups carries each row's `drill` block and the
    newest one as `latest_drill`. Old manifests without `drill` still render.
  * The page has the status line and the new column, and the page JS still
    never assigns innerHTML.

No real database, pg_dump or pg_restore is used. The pg engine is a fake and
the background part of the drill is replaced, so nothing real runs.
"""
import datetime
import json
import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
BACKUP_ID = "pg_20260830_120000_ab12cd"
OLDER_ID = "pg_20260829_120000_ab12cd"


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
    return token


def _csrf_headers(token):
    from app.auth.csrf import token_for_session
    return {"X-CSRF-Token": token_for_session(token)}


def _drill_block(status="passed", checked_at="2026-08-30T13:00:00+00:00"):
    return {
        "status": status,
        "checked_at": checked_at,
        "duration_ms": 263000,
        "restore_duration_ms": 200000,
        "tables_checked": 2,
        "mismatches": [],
        "reason": "تمرین بازیابی موفق بود.",
        "checks": {"row_counts": "passed", "schema_migrations": "passed",
                   "validation": "passed"},
        "tables": {"app.dataset": {"expected": 5, "actual": 5},
                   "app.questions": {"expected": 9, "actual": 9}},
        "problems": [],
        "cleanup_ok": True,
        "drill_database": "padyar_drill",
        "actor": "system",
    }


def _manifest(backup_id=BACKUP_ID, drill=None):
    m = {
        "backup_id": backup_id,
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
    if drill is not None:
        m["drill"] = drill
    return m


class FakePgEngine:
    def __init__(self, manifests):
        self.manifests = manifests

    def list_backups(self):
        return self.manifests


@pytest.fixture
def fake_pg(monkeypatch):
    """The router believes it runs on PostgreSQL. Manifests are set per test."""
    engine = FakePgEngine([_manifest()])
    from app.routers import backups as backups_router
    monkeypatch.setattr(backups_router, "_engine", lambda: (engine, True))
    return engine


@pytest.fixture
def fake_start(monkeypatch):
    """Replace restore_drill.start and record how it was called."""
    calls = []
    state = {"raise": None}

    def start(backup_id, actor):
        calls.append((backup_id, actor))
        if state["raise"] is not None:
            raise state["raise"]
        return None

    from app.services import restore_drill
    monkeypatch.setattr(restore_drill, "start", start)
    start.calls = calls
    start.state = state
    return start


def _url(backup_id=BACKUP_ID):
    return f"/admin/api/infra/backups/{backup_id}/drill"


# ── Who may start a drill ───────────────────────────────────────────────

def test_anonymous_cannot_start_a_drill(client, fake_pg, fake_start):
    res = client.post(_url())
    assert res.status_code in (401, 403)
    assert fake_start.calls == []


def test_admin_with_csrf_starts_a_drill(client, fake_pg, fake_start):
    token = _login(client)
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 202
    body = res.json()
    assert body["started"] is True
    assert body["backup_id"] == BACKUP_ID
    assert body["message"]
    # The actor is the logged-in admin, taken from the session, not the body.
    assert fake_start.calls == [(BACKUP_ID, "tester")]


def test_admin_without_csrf_header_is_rejected(client, fake_pg, fake_start):
    token = _login(client)
    res = client.post(_url())
    assert res.status_code == 403
    assert fake_start.calls == []
    # Allow-control: the same session with the header goes through.
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 202
    assert len(fake_start.calls) == 1


def test_a_csrf_token_for_another_session_is_rejected(client, fake_pg, fake_start):
    _login(client)
    res = client.post(_url(), headers=_csrf_headers("some-other-session"))
    assert res.status_code == 403
    assert fake_start.calls == []


# ── Which backup ────────────────────────────────────────────────────────

@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """Point pg_backup at a temp folder so the REAL id check can run."""
    from app.services import pg_backup
    folder = tmp_path / "pgbackups"
    folder.mkdir()
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(folder))
    return folder


@pytest.mark.parametrize("bad_id", ["pg_bad", "pg_20260830_120000_ZZZZZZ", "PG_20260830_120000_ab12cd"])
def test_a_malformed_id_is_404(client, fake_pg, backup_dir, bad_id):
    token = _login(client)
    res = client.post(_url(bad_id), headers=_csrf_headers(token))
    assert res.status_code == 404
    from app.routers.backups import FA_NOT_FOUND
    assert res.json()["detail"] == FA_NOT_FOUND


def test_a_path_in_the_id_never_reaches_the_drill(client, fake_pg, fake_start):
    # The slash splits the URL, so the router refuses it before any handler.
    token = _login(client)
    res = client.post("/admin/api/infra/backups/../etc/drill",
                      headers=_csrf_headers(token))
    assert res.status_code == 404
    res = client.post("/admin/api/infra/backups/..%2Fetc/drill",
                      headers=_csrf_headers(token))
    assert res.status_code == 404
    assert fake_start.calls == []


def test_an_unknown_but_well_formed_id_is_404(client, fake_pg, backup_dir):
    token = _login(client)
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 404


def test_a_real_backup_id_is_accepted(client, fake_pg, backup_dir, monkeypatch):
    """Allow-control for the two tests above: a real manifest + dump gets 202.

    Only the background part is replaced (the lock and the thread body), so the
    real id check and the real manifest load both run.
    """
    folder = backup_dir / BACKUP_ID
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    (folder / "padyar.dump").write_bytes(b"fake")
    from app.services import restore_drill
    ran = []
    monkeypatch.setattr(restore_drill, "_take_lock", lambda: None)
    monkeypatch.setattr(restore_drill, "_in_thread",
                        lambda *args: ran.append(args))
    token = _login(client)
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 202
    assert res.json()["backup_id"] == BACKUP_ID


# ── One drill at a time, and engine ─────────────────────────────────────

def test_a_busy_drill_answers_409_and_then_clears(client, fake_pg, fake_start):
    from app.services import restore_drill
    from app.routers.backups import FA_DRILL_BUSY
    token = _login(client)
    fake_start.state["raise"] = restore_drill.DrillAlreadyRunning("busy")
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 409
    assert res.json()["detail"] == FA_DRILL_BUSY
    assert "در حال اجراست" in FA_DRILL_BUSY
    # Allow-control: once the first drill is over, a new one starts.
    fake_start.state["raise"] = None
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 202


def test_an_unexpected_error_is_a_safe_500(client, fake_pg, fake_start):
    from app.routers.backups import FA_GENERIC
    token = _login(client)
    fake_start.state["raise"] = RuntimeError("secret path /var/lib/x")
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 500
    assert res.json()["detail"] == FA_GENERIC
    assert "secret" not in res.text


def test_sqlite_engine_answers_409(client, fake_start):
    """No fake_pg here: the test backend is SQLite."""
    from app.routers.backups import FA_DRILL_POSTGRES_ONLY
    token = _login(client)
    res = client.post(_url(), headers=_csrf_headers(token))
    assert res.status_code == 409
    assert res.json()["detail"] == FA_DRILL_POSTGRES_ONLY
    assert fake_start.calls == []


def test_starting_a_drill_is_audited(client, fake_pg, fake_start, monkeypatch):
    seen = []
    from app.routers import backups as backups_router
    real = backups_router.applog.audit
    monkeypatch.setattr(backups_router.applog, "audit",
                        lambda *a, **kw: (seen.append((a, kw)), real(*a, **kw))[1])
    token = _login(client)
    assert client.post(_url(), headers=_csrf_headers(token)).status_code == 202
    events = [a[0] for a, _ in seen]
    assert "admin.backup.drill.requested" in events
    kw = [k for a, k in seen if a[0] == "admin.backup.drill.requested"][0]
    assert kw["actor"] == "tester"
    assert kw["target"] == BACKUP_ID


# ── The list ────────────────────────────────────────────────────────────

def test_list_rows_carry_the_drill_block(client, fake_pg):
    fake_pg.manifests = [_manifest(drill=_drill_block())]
    _login(client)
    body = client.get("/admin/api/infra/backups").json()
    row = body["backups"][0]
    assert row["drill"]["status"] == "passed"
    assert row["drill"]["tables"]["app.dataset"] == {"expected": 5, "actual": 5}
    # Existing keys are untouched.
    for key in ("backup_id", "created_at", "created_by", "kind", "files",
                "total_bytes", "verification"):
        assert key in row
    assert body["latest_drill"]["backup_id"] == BACKUP_ID
    assert body["latest_drill"]["status"] == "passed"


def test_old_manifests_without_drill_still_render(client, fake_pg):
    fake_pg.manifests = [_manifest()]
    _login(client)
    body = client.get("/admin/api/infra/backups").json()
    assert body["backups"][0]["drill"] is None
    assert body["latest_drill"] is None


def test_an_error_manifest_has_no_drill(client, fake_pg):
    fake_pg.manifests = [{"backup_id": "pg_broken", "error": "unreadable"}]
    _login(client)
    body = client.get("/admin/api/infra/backups").json()
    assert body["backups"][0]["drill"] is None
    assert body["latest_drill"] is None


def test_latest_drill_is_the_newest_one(client, fake_pg):
    fake_pg.manifests = [
        _manifest(OLDER_ID, drill=_drill_block(
            "passed", "2026-08-29T13:00:00+00:00")),
        _manifest(BACKUP_ID, drill=_drill_block(
            "failed", "2026-08-30T13:00:00+00:00")),
    ]
    _login(client)
    body = client.get("/admin/api/infra/backups").json()
    assert body["latest_drill"]["backup_id"] == BACKUP_ID
    assert body["latest_drill"]["status"] == "failed"
    assert [r["drill"]["status"] for r in body["backups"]] == ["passed", "failed"]


def test_sqlite_list_has_no_latest_drill(client):
    _login(client)
    body = client.get("/admin/api/infra/backups").json()
    assert body["engine"] == "sqlite"
    assert body["latest_drill"] is None


# ── The page ────────────────────────────────────────────────────────────

def test_page_requires_admin(client):
    res = client.get("/secure-panel-admin/infrastructure/backups",
                     follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/secure-panel-admin/login"


def test_page_has_the_drill_status_line_and_column(client):
    _login(client)
    html = client.get("/secure-panel-admin/infrastructure/backups").text
    assert 'id="drill-status"' in html
    assert 'id="drillModal"' in html
    assert "<th>تمرین بازیابی</th>" in html
    # The «no drill yet» sentence and the one-line explanation are plain text
    # in the template or the script, so the operator never sees an empty line.
    assert "نتیجهٔ تمرین بازیابی" in html


# ── The page script ─────────────────────────────────────────────────────

def test_page_script_never_assigns_html_from_data():
    source = (REPO / "static/admin/js/infra_backups.js").read_text(encoding="utf-8")
    assert not re.search(r"innerHTML\s*\+?=", source)
    assert not re.search(r"outerHTML\s*=", source)
    assert "insertAdjacentHTML" not in source
    assert "document.write" not in source


def test_page_script_starts_a_drill_through_fetchauth():
    source = (REPO / "static/admin/js/infra_backups.js").read_text(encoding="utf-8")
    # fetchAuth is what adds the CSRF header, so a bare fetch() would be
    # rejected with 403.
    assert "/drill`" in source
    assert not re.search(r"\bfetch\(", source.replace("fetchAuth(", ""))
