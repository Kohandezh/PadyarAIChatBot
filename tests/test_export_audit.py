"""Every path that takes data out of this install writes one audit row.

The defect these tests close: a backup file is the whole database. After the
exhibition it holds the raw mobile number of every company captured, every
`otp_challenges.destination` and every visitor access code. Downloading it
used to leave nothing behind, so "who took the contact list of three hundred
companies, and when" had no answer.

Asserting a 200 would prove nothing here. Each test counts `data.export` rows
in `audit_logs` and checks the actor, the address and the client on the row
that appeared. The last two tests are the ones that keep this honest over
time: one proves the exported CONTENT never reaches the row, and one fails
when somebody adds a new export route without deciding about its audit line.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Real databases and a real backups directory, both inside tmp_path.

    conftest already redirects LOGS_DB_PATH here for every test, so the audit
    rows these tests read are written to a throwaway file too.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    import backup_db
    monkeypatch.setattr(backup_db, "BACKUP_DIR", str(tmp_path / "backups"))

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    return tmp_path


@pytest.fixture
def client(paths):
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client, username="tester"):
    """A real admin session row plus its cookie and CSRF header."""
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, username, expiry.isoformat()),
    )
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    from app.auth.csrf import token_for_session
    client.headers.update({"X-CSRF-Token": token_for_session(token)})
    return token


def _exports():
    """Every `data.export` row currently in audit_logs, oldest first."""
    from app.services import applog
    rows, _ = applog.query(category="audit", limit=500,
                           sort="id", direction="asc")
    return [r for r in rows if r["event_name"] == "data.export"]


# The headers a real browser sends. Both have to land on the row: the address
# says which machine, the client says which browser or which script.
HEADERS = {"User-Agent": "ExportProbe/1.0", "X-Forwarded-For": "203.0.113.9"}


def _assert_actor(row, actor="tester"):
    assert row["actor"] == actor
    assert row["user_agent"] == "ExportProbe/1.0"
    assert row["ip"]                       # never blank
    assert row["category"] == "audit"
    assert row["outcome"] == "ok"


def _make_backup(tmp_path):
    """One legacy single-file backup on disk. Returns its name."""
    import os
    import backup_db
    path = backup_db.create_backup()
    return os.path.basename(path)


# ── The named export paths ──────────────────────────────────────────────

def test_legacy_backup_download_writes_one_audit_row(client, paths):
    """The whole database in one file. This is the row that was missing."""
    _login(client)
    name = _make_backup(paths)
    before = len(_exports())

    r = client.get(f"/admin/api/backups/download/{name}", headers=HEADERS)
    assert r.status_code == 200

    rows = _exports()
    assert len(rows) == before + 1
    row = rows[-1]
    _assert_actor(row)
    assert row["target"] == name
    assert row["route"] == f"/admin/api/backups/download/{name}"
    assert row["http_method"] == "GET"


def test_a_download_that_serves_nothing_writes_no_export_row(client, paths):
    """A refused name is not an export. The row must mean data actually left."""
    _login(client)
    before = len(_exports())
    r = client.get("/admin/api/backups/download/chat_history_20200101_000000.db",
                   headers=HEADERS)
    assert r.status_code == 404
    assert len(_exports()) == before


def test_legacy_backup_create_writes_one_audit_row(client, paths):
    """Creating the file is the export. From there it is one download away."""
    _login(client)
    before = len(_exports())

    r = client.post("/admin/api/backups/create", headers=HEADERS)
    assert r.status_code == 200

    rows = _exports()
    assert len(rows) == before + 1
    _assert_actor(rows[-1])
    assert rows[-1]["target"] == r.json()["name"]


def test_infra_backup_download_writes_one_audit_row(client, paths):
    _login(client)
    created = client.post("/admin/api/infra/backups", headers=HEADERS)
    assert created.status_code == 200
    backup_id = created.json()["backup_id"]
    member = created.json()["files"][0]["name"]
    before = len(_exports())

    r = client.get(f"/admin/api/infra/backups/{backup_id}/download",
                   params={"file": member}, headers=HEADERS)
    assert r.status_code == 200

    rows = _exports()
    assert len(rows) == before + 1
    _assert_actor(rows[-1])
    assert rows[-1]["target"] == f"{backup_id}/{member}"


def test_infra_backup_create_writes_one_audit_row(client, paths):
    """The engines log their own `admin.backup.create`. That row carries no
    address and no client, so it cannot answer the question this one answers."""
    _login(client)
    before = len(_exports())

    r = client.post("/admin/api/infra/backups", headers=HEADERS)
    assert r.status_code == 200

    rows = _exports()
    assert len(rows) == before + 1
    _assert_actor(rows[-1])
    assert rows[-1]["target"] == r.json()["backup_id"]


def test_chat_history_export_writes_one_audit_row(client, paths):
    """Every question every visitor ever typed, in one CSV."""
    _login(client)
    before = len(_exports())

    r = client.get("/admin/api/export_csv", headers=HEADERS)
    assert r.status_code == 200

    rows = _exports()
    assert len(rows) == before + 1
    _assert_actor(rows[-1])
    assert rows[-1]["target"] == "chat_logs"


@pytest.mark.parametrize("kind,fmt", [
    ("dataset", "json"), ("dataset", "csv"),
    ("questions", "json"), ("questions", "csv"),
])
def test_content_exports_write_one_audit_row(client, paths, kind, fmt):
    _login(client)
    before = len(_exports())

    r = client.get(f"/admin/api/{kind}/export", params={"format": fmt},
                   headers=HEADERS)
    assert r.status_code == 200

    rows = _exports()
    assert len(rows) == before + 1
    _assert_actor(rows[-1])
    assert rows[-1]["target"] == kind


def test_the_actor_on_the_row_is_the_admin_who_asked(client, paths):
    """Two admins, two files, two rows. The names must not be interchangeable."""
    _login(client, "alice")
    client.get("/admin/api/dataset/export", headers=HEADERS)
    _login(client, "bob")
    client.get("/admin/api/questions/export", headers=HEADERS)

    rows = _exports()[-2:]
    assert [r["actor"] for r in rows] == ["alice", "bob"]
    assert [r["target"] for r in rows] == ["dataset", "questions"]


# ── The two rules the rows themselves have to obey ──────────────────────

def test_the_exported_content_never_reaches_the_audit_row(client, paths):
    """Record WHAT was taken, not what was in it.

    audit_logs has a deliberately long retention. A row that quoted the export
    would be a second, longer-lived copy of the personal data it exists to
    police.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("INSERT INTO chat_logs (query, response, response_type, source,"
                 " confidence) VALUES (?, ?, ?, ?, ?)",
                 ("شماره من 09121234567 است", "باشد", "ai", "gpt", 0.9))
    conn.commit()
    conn.close()

    _login(client)
    r = client.get("/admin/api/export_csv", headers=HEADERS)
    assert "09121234567" in r.text          # it really was exported

    blob = str(_exports()[-1])
    assert "09121234567" not in blob
    assert "شماره من" not in blob


def test_a_broken_audit_write_never_breaks_the_download(client, paths, monkeypatch):
    """An audit write that took down a backup download would be the worse bug."""
    from app.services import applog
    _login(client)
    name = _make_backup(paths)

    def boom(*a, **kw):
        raise RuntimeError("log store is full")

    monkeypatch.setattr(applog, "record", boom)
    r = client.get(f"/admin/api/backups/download/{name}", headers=HEADERS)
    assert r.status_code == 200
    assert r.content


# ── The guard against the next unlogged export ──────────────────────────

# Every route in the app whose path says it hands data out, and what was
# decided about it. Adding an export route breaks this test until its name is
# listed here, which is the moment to write its audit line.
KNOWN_EXPORT_ROUTES = {
    "/admin/api/export_csv":                        "data.export",
    "/admin/api/dataset/export":                    "data.export",
    "/admin/api/questions/export":                  "data.export",
    "/admin/api/backups/download/{name}":           "data.export",
    "/admin/api/infra/backups/{backup_id}/download": "data.export",
    # Already audited by its own router as `admin.logs.exported`, with actor
    # and IP. Left alone here, handed off for the user-agent field and the
    # rename. See app/routers/logs.py.
    "/admin/api/logs/export":                       "admin.logs.exported",
}


def _all_paths(routes):
    """Every path in the app.

    FastAPI wraps an included router in a node that has no `path` of its own
    and keeps the real router on `original_router`, so this walks into both
    that and any plain sub-router."""
    for r in routes:
        path = getattr(r, "path", None)
        if path:
            yield path
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _all_paths(inner.routes)
        elif hasattr(r, "routes"):
            yield from _all_paths(r.routes)


def test_no_export_route_exists_without_a_decision_about_its_audit():
    from app.main import app
    found = {p for p in _all_paths(app.routes)
             if "export" in p or "download" in p}
    assert found == set(KNOWN_EXPORT_ROUTES), (
        "an export route changed. Give it an audit row (app/routers/admin.py "
        "audit_export) and list it above, or say why it needs none."
    )
