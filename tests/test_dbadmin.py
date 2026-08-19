"""Infrastructure → Database: the properties that must never regress.

The four that matter most, and why each is a test rather than a comment:

  * A caller-supplied PATH must never reach sqlite3.connect. The panel picks a
    database by short name; anything else — traversal, an absolute path, a
    name with SQL punctuation in it — is refused before a file is opened.
  * An unknown ACTION must execute nothing. The router dispatches through an
    explicit dict of functions, so a request naming an operation that does not
    exist reaches no code at all.
  * Two maintenance operations must never run at once. VACUUM rewriting the
    file while a check-point truncates the write-ahead log is how a day's data
    is lost, so the second attempt is refused rather than queued.
  * VACUUM must refuse when the disk cannot hold a second copy. Running out of
    space halfway through a whole-file rewrite is the accident the check exists
    to prevent.

Everything here runs against throwaway databases in tmp_path. No test ever
touches the operator's real chat_history.db or application_logs.db.
"""
import datetime
import os
import pathlib
import re
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

DBADMIN_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "dbadmin.py"

# Names that must never resolve to a file. Two of them are path traversal, one
# is an absolute path, one carries SQL punctuation, and the rest are the empty
# and non-string cases a hand-written request can produce.
HOSTILE_NAMES = [
    "../../etc/passwd",
    "..",
    "/etc/passwd",
    "app;DROP",
    "app' OR '1'='1",
    "APP/../logs",
    "chat_history.db",
    "",
    "   ",
]


@pytest.fixture(scope="module")
def schema_template(tmp_path_factory):
    """Both databases, with the real schema, built exactly once.

    `init_db()` hashes a password with a deliberately slow KDF. Paying that in
    every one of this module's fixtures added over a minute to the suite, so
    the schema is created here once and copied per test — each test still gets
    its own untouched pair of files.

    The autouse fixtures in conftest.py are function-scoped and are therefore
    NOT in force while this module-scoped fixture runs, so both paths are
    redirected explicitly. No real database is ever opened.
    """
    folder = tmp_path_factory.mktemp("schema")
    with pytest.MonkeyPatch.context() as mp:
        import app.config as config
        mp.setattr(config, "DB_PATH", str(folder / "chat_history.db"))
        mp.setattr(config, "LOGS_DB_PATH", str(folder / "application_logs.db"))
        mp.setattr(config, "SEED_DEFAULT_CONTENT", False)

        from app.db.connection import init_db
        init_db()
        from app.services import applog
        applog.ensure_tables()

        # Fold any write-ahead log back into the file so a single copy carries
        # the whole database. The copies are switched back to WAL below, which
        # is how the application actually runs.
        for path in (config.DB_PATH, config.LOGS_DB_PATH):
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.close()
    return folder


def _install_schema(template, tmp_path, monkeypatch):
    import shutil
    import app.config as config
    for name in ("chat_history.db", "application_logs.db"):
        shutil.copyfile(template / name, tmp_path / name)
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "application_logs.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)


@pytest.fixture
def dba(tmp_path, monkeypatch, schema_template):
    """The service, pointed at two throwaway databases."""
    _install_schema(schema_template, tmp_path, monkeypatch)

    from app.services import applog
    applog._recent.clear()      # storm suppression must not swallow this test's rows

    from app.services import dbadmin, storage
    storage.reset_alert_state()
    return dbadmin


def _audit_rows(event_prefix=""):
    from app.services import applog
    rows, _total = applog.query(tables=["audit_logs", "security_events"], limit=500)
    if event_prefix:
        rows = [r for r in rows if str(r["event_name"]).startswith(event_prefix)]
    return rows


# ── Reporting ───────────────────────────────────────────────────────────

def test_overview_reports_sane_values_for_both_databases(dba):
    for name in ("app", "logs"):
        info = dba.overview(name)
        assert info["name"] == name
        assert info["label_fa"]
        assert info["exists"] is True
        assert info["readable"] is True
        assert info["size_bytes"] > 0
        assert info["page_size"] > 0
        assert info["page_count"] > 0
        assert info["table_count"] > 0
        assert info["sqlite_version"]
        assert info["journal_mode"]
        assert info["freelist_count"] >= 0
        assert info["index_count"] >= 0


def test_overview_never_leaks_the_filesystem_layout(dba):
    """A basename is enough to identify the file; the directory is not the
    panel's to publish over the network."""
    for name in ("app", "logs"):
        info = dba.overview(name)
        assert os.sep not in info["path_basename"]
        assert not any(isinstance(v, str) and v.startswith("/")
                       for v in info.values())


def test_tables_lists_the_real_schema_with_counts(dba):
    rows = dba.tables("app")
    names = {r["table"] for r in rows}
    # The tables app/db/connection.py creates on every boot.
    assert {"admins", "admin_sessions", "settings", "dataset",
            "questions", "synonyms", "chat_logs"} <= names
    for row in rows:
        assert row["rows"] >= 0
        assert isinstance(row["indexes"], list)

    log_tables = {r["table"] for r in dba.tables("logs")}
    assert {"app_logs", "audit_logs", "security_events", "service_events"} <= log_tables


def test_the_allowlist_is_exactly_the_two_known_databases(dba):
    assert set(dba._DATABASES) == {"app", "logs"}
    assert set(dba.NAMES) == {"app", "logs"}


# ── The allowlist ───────────────────────────────────────────────────────

@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_a_hostile_database_name_is_refused_and_touches_nothing(dba, hostile, tmp_path):
    before = sorted(os.listdir(tmp_path))

    with pytest.raises(dba.UnknownDatabase):
        dba.overview(hostile)
    with pytest.raises(dba.UnknownDatabase):
        dba.tables(hostile)
    with pytest.raises(dba.UnknownDatabase):
        dba.db_path(hostile)

    assert dba.is_known(hostile) is False
    # No file was created, opened into existence, or removed.
    assert sorted(os.listdir(tmp_path)) == before


@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_maintenance_on_a_hostile_name_runs_nothing(dba, hostile):
    result = dba.integrity_check(hostile, actor="tester")
    assert result["ok"] is False
    assert result["detail"] == "unknown_database"
    assert result["message_fa"] == dba.UNKNOWN_DB_FA
    denied = [r for r in _audit_rows("admin.database.") if r["outcome"] == "denied"]
    assert denied, "a refused name must still leave an audit trail"


def test_a_non_string_name_is_refused(dba):
    for value in (None, 12, ["app"], {"name": "app"}):
        assert dba.is_known(value) is False


# ── Integrity ───────────────────────────────────────────────────────────

def test_integrity_check_passes_on_a_healthy_database(dba):
    for name in ("app", "logs"):
        result = dba.integrity_check(name, actor="tester")
        assert result["ok"] is True, result
        assert result["duration_ms"] >= 0
        assert "مشکل" in result["message_fa"] or "سلامت" in result["message_fa"]


def test_the_last_integrity_result_is_stored_and_shown_without_re_running(dba):
    assert dba.overview("app")["integrity_status"] == "unknown"
    dba.integrity_check("app", actor="tester")
    info = dba.overview("app")
    assert info["integrity_status"] == "ok"
    assert info["integrity_checked_at"]


def _write_corrupt_database(tmp_path) -> pathlib.Path:
    """A real SQLite file with garbage written over its data pages.

    The header is left intact on purpose: a file that fails to open at all is
    the easy case. This one opens and then fails its integrity check, which is
    what a half-corrupted database on a dying disk actually looks like.
    """
    healthy = tmp_path / "healthy.db"
    conn = sqlite3.connect(healthy)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("x" * 200,) for _ in range(3000)])
    conn.commit()
    conn.close()

    raw = bytearray(healthy.read_bytes())
    for i in range(4096, 12288):
        raw[i] = 0xFF
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(bytes(raw))
    return corrupt


def test_integrity_check_reports_failure_on_a_corrupted_database(tmp_path, monkeypatch):
    import app.config as config
    corrupt = _write_corrupt_database(tmp_path)
    monkeypatch.setattr(config, "DB_PATH", str(corrupt))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "application_logs.db"))

    from app.services import applog, dbadmin
    applog.ensure_tables()
    applog._recent.clear()

    result = dbadmin.integrity_check("app", actor="tester")
    assert result["ok"] is False, result
    assert result["detail"]                       # SQLite's own report, truncated
    assert "پشتیبان" in result["message_fa"]      # the operator is told what to do

    failures = [r for r in _audit_rows("admin.database.integrity_check")
                if r["outcome"] == "error"]
    assert failures, "a failed integrity check must be auditable"


def test_quick_check_also_reports_failure_on_a_corrupted_database(tmp_path, monkeypatch):
    import app.config as config
    corrupt = _write_corrupt_database(tmp_path)
    monkeypatch.setattr(config, "DB_PATH", str(corrupt))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "application_logs.db"))

    from app.services import applog, dbadmin
    applog.ensure_tables()
    applog._recent.clear()

    assert dbadmin.quick_check("app", actor="tester")["ok"] is False


# ── Every operation, for real ───────────────────────────────────────────

@pytest.mark.parametrize("action", ["integrity_check", "quick_check", "analyze",
                                    "optimize", "wal_checkpoint", "vacuum"])
@pytest.mark.parametrize("name", ["app", "logs"])
def test_every_maintenance_action_runs_on_a_real_database(dba, action, name):
    assert action in dba.ACTIONS
    result = dba.ACTIONS[action](name, actor="tester")
    assert result["ok"] is True, (action, name, result)
    assert result["message_fa"]
    assert result["duration_ms"] >= 0
    assert "detail" in result


def test_the_action_list_is_exactly_the_six_supported_operations(dba):
    assert set(dba.ACTIONS) == {"integrity_check", "quick_check", "analyze",
                                "optimize", "wal_checkpoint", "vacuum"}
    catalog = dba.action_catalog()
    assert {c["key"] for c in catalog} == set(dba.ACTIONS)
    assert all(c["label_fa"] and c["help_fa"] for c in catalog)
    assert [c["key"] for c in catalog if c["danger"]] == ["vacuum"]


def test_a_successful_operation_writes_an_audit_row(dba):
    dba.analyze("app", actor="ops-person")
    rows = [r for r in _audit_rows("admin.database.analyze") if r["outcome"] == "ok"]
    assert rows
    assert rows[0]["actor"] == "ops-person"
    assert rows[0]["target"] == "app"
    assert rows[0]["duration_ms"] is not None


# ── One at a time ───────────────────────────────────────────────────────

def test_a_second_operation_is_refused_while_one_is_running(dba):
    """The lock is non-blocking on purpose: the second operator is told the
    system is busy, not silently queued behind a whole-file rewrite."""
    assert dba._MAINT_LOCK.acquire(blocking=False) is True
    try:
        result = dba.vacuum("app", actor="second-operator")
        assert result["ok"] is False
        assert result["detail"] == "busy"
        assert result["message_fa"] == dba.BUSY_FA
    finally:
        dba._MAINT_LOCK.release()

    denied = [r for r in _audit_rows("admin.database.vacuum")
              if r["outcome"] == "denied"]
    assert denied, "a refused concurrent run must be auditable"
    # And the lock is usable again afterwards.
    assert dba.analyze("app", actor="tester")["ok"] is True


# ── VACUUM needs room ───────────────────────────────────────────────────

class _Usage:
    def __init__(self, total, used, free):
        self.total, self.used, self.free = total, used, free


def test_vacuum_refuses_when_free_space_is_insufficient(dba, monkeypatch):
    from app.services import storage
    size = os.path.getsize(dba.db_path("app"))
    # Just under the 2x the operation needs.
    monkeypatch.setattr(storage, "_disk_usage",
                        lambda _path: _Usage(10 ** 12, 10 ** 12 - size, size))

    result = dba.vacuum("app", actor="tester")
    assert result["ok"] is False
    assert result["detail"] == "insufficient_space"
    assert "فضا" in result["message_fa"]


def test_vacuum_proceeds_when_there_is_room(dba, monkeypatch):
    from app.services import storage
    monkeypatch.setattr(storage, "_disk_usage",
                        lambda _path: _Usage(10 ** 12, 0, 10 ** 12))
    assert dba.vacuum("app", actor="tester")["ok"] is True


def test_vacuum_refuses_when_the_disk_cannot_be_measured(dba, monkeypatch):
    """"I could not check" is not a reason to start rewriting a database."""
    from app.services import storage

    def boom(_path):
        raise OSError("no such volume")

    monkeypatch.setattr(storage, "_disk_usage", boom)
    assert dba.vacuum("app", actor="tester")["ok"] is False


# ── The API ─────────────────────────────────────────────────────────────

def _registered_paths(app) -> set:
    """Every path the app serves, flattened.

    `app.routes` does not list them directly: an included router appears as a
    single wrapper object holding the real router in `original_router`. Reading
    only the top level makes every lookup miss, which would silently re-include
    a router that is already wired.
    """
    found, stack = set(), list(app.routes)
    while stack:
        route = stack.pop()
        path = getattr(route, "path", None)
        if isinstance(path, str):
            found.add(path)
        inner = getattr(route, "original_router", None) or route
        sub = getattr(inner, "routes", None)
        if sub and inner is not route:
            stack.extend(sub)
    return found


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "application_logs.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    from app.routers import dbadmin as dbadmin_router
    # This router is wired through app/modules/registry.py, which this agent
    # does not own. Mount it here only if it is genuinely absent, so the tests
    # exercise the real application when it is wired and still run when it is
    # not. Registering it twice would leave a duplicate route behind for every
    # later test module in the session, so the check has to be accurate.
    if "/admin/api/infra/database" not in _registered_paths(app):
        app.include_router(dbadmin_router.router)

    with TestClient(app) as c:
        yield c


def _login(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute('INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
                 (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    # Admin mutations require a CSRF token; these tests exercise the
    # endpoints themselves, not the CSRF guard (see tests/test_csrf.py).
    from app.auth.csrf import token_for_session
    client.headers.update({'X-CSRF-Token': token_for_session(token)})
    return token


def test_every_endpoint_requires_an_admin_session(client):
    assert client.get("/admin/api/infra/database").status_code == 401
    assert client.get("/admin/api/infra/database/app/tables").status_code == 401
    assert client.post("/admin/api/infra/database/app/maintenance",
                       json={"action": "analyze"}).status_code == 401
    assert client.get("/admin/api/infra/storage").status_code == 401


def test_the_overview_endpoint_returns_both_databases(client):
    _login(client)
    r = client.get("/admin/api/infra/database")
    assert r.status_code == 200
    data = r.json()
    assert {d["name"] for d in data["databases"]} == {"app", "logs"}
    assert {a["key"] for a in data["actions"]} == {
        "integrity_check", "quick_check", "analyze", "optimize",
        "wal_checkpoint", "vacuum"}


def test_the_tables_endpoint_returns_rows_and_indexes(client):
    _login(client)
    r = client.get("/admin/api/infra/database/logs/tables")
    assert r.status_code == 200
    assert {t["table"] for t in r.json()["tables"]} >= {"app_logs", "audit_logs"}


def test_an_unknown_action_returns_400_and_executes_nothing(client):
    _login(client)
    from app.services import applog
    before = applog.query(tables=["audit_logs"], limit=500)[1]

    for bogus in ["drop", "DROP TABLE admins", "run", "__class__", "os",
                  "get_db_connection", "", "vacuum; analyze"]:
        r = client.post("/admin/api/infra/database/app/maintenance",
                        json={"action": bogus})
        assert r.status_code == 400, bogus
        detail = r.json()["detail"]
        assert detail == "این عملیات پشتیبانی نمی‌شود."

    rows, after = applog.query(tables=["audit_logs"], limit=500)
    assert after == before, "an unknown action must not run a maintenance operation"


@pytest.mark.parametrize("hostile", ["app;DROP", "..", "chat_history.db", "etc",
                                     "../../etc/passwd", "%2e%2e"])
def test_a_hostile_database_name_is_refused_by_the_api(client, hostile):
    _login(client)
    assert client.get(f"/admin/api/infra/database/{hostile}/tables").status_code == 404
    r = client.post(f"/admin/api/infra/database/{hostile}/maintenance",
                    json={"action": "analyze"})
    assert r.status_code == 404


def test_a_safe_action_runs_through_the_api(client):
    _login(client)
    r = client.post("/admin/api/infra/database/app/maintenance",
                    json={"action": "analyze"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["message_fa"]


def test_vacuum_without_the_typed_confirmation_is_refused(client):
    """The typed phrase is checked on the server too. A confirmation enforced
    only in the browser is not a control."""
    _login(client)
    for attempt in [{}, {"confirm": ""}, {"confirm": "vacuum application database"},
                    {"confirm": "VACUUM LOGS DATABASE"}]:
        body = {"action": "vacuum"}
        body.update(attempt)
        r = client.post("/admin/api/infra/database/app/maintenance", json=body)
        assert r.status_code == 400, attempt
        assert "تأیید" in r.json()["detail"]

    r = client.post("/admin/api/infra/database/app/maintenance",
                    json={"action": "vacuum", "confirm": "VACUUM APPLICATION DATABASE"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_the_storage_endpoint_reports_real_disk_facts(client):
    _login(client)
    r = client.get("/admin/api/infra/storage")
    assert r.status_code == 200
    data = r.json()
    assert data["disk"]["total_bytes"] > 0
    assert data["disk"]["state"] in ("ok", "warning", "critical")
    assert any(c["key"] == "database_app" for c in data["categories"])


def test_no_endpoint_response_contains_a_filesystem_path(client):
    _login(client)
    for url in ("/admin/api/infra/database", "/admin/api/infra/database/app/tables",
                "/admin/api/infra/storage"):
        body = client.get(url).text
        assert "/Users/" not in body
        assert "/home/" not in body
        assert "/var/" not in body


# ── Source-inspection regression guard ──────────────────────────────────

def test_the_service_shells_out_to_nothing_and_evaluates_nothing():
    """This module's whole job is running operations against a database file.
    If a future change reaches for a shell or for dynamic execution to do it,
    that is the moment the allowlist stops being the security model."""
    source = DBADMIN_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("subprocess", "os.system", "shell=True", "eval(", "exec(",
                      "__import__", "popen", "getattr(sqlite3"):
        assert forbidden not in source, forbidden


def test_no_string_is_interpolated_into_sql_except_a_quoted_identifier():
    """Every `.execute(` call site is either a module-level constant or an
    identifier that went through `_quote_ident`, which validates against
    ^[A-Za-z_][A-Za-z0-9_]*$ and double-quotes the result."""
    source = DBADMIN_SOURCE.read_text(encoding="utf-8")
    for line in source.splitlines():
        if ".execute(" not in line:
            continue
        interpolating = ('f"' in line or "f'" in line or ".format(" in line
                         or "% (" in line or " + " in line)
        if interpolating:
            assert "_quote_ident(" in line, line.strip()


def test_quote_ident_rejects_anything_that_is_not_a_plain_identifier(dba):
    for bad in ['t"; DROP TABLE admins; --', "table name", "1table", "", "t-1",
                "admins;", "t'", None]:
        with pytest.raises(ValueError):
            dba._quote_ident(bad)
    assert dba._quote_ident("chat_logs") == '"chat_logs"'


def test_the_router_dispatches_through_an_explicit_dict_not_getattr():
    router_source = (pathlib.Path(__file__).resolve().parents[1]
                     / "app" / "routers" / "dbadmin.py").read_text(encoding="utf-8")
    assert "ACTIONS.get(" in router_source
    assert not re.search(r"getattr\(\s*dbadmin", router_source)
    for forbidden in ("subprocess", "os.system", "shell=True", "eval(", "exec("):
        assert forbidden not in router_source, forbidden


# ── The two pages ───────────────────────────────────────────────────────
# The page routes live in app/routers/public.py, which this agent does not
# own. These tests guard the templates: that they render at all, that they are
# behind the admin session, and that they load their own ES module.

PAGES = [("/secure-panel-inotex/infrastructure/database", "infra_database.js"),
         ("/secure-panel-inotex/infrastructure/storage", "infra_storage.js")]


@pytest.mark.parametrize("url,script", PAGES)
def test_the_page_is_behind_the_admin_session(client, url, script):
    if url not in _registered_paths(client.app):
        pytest.skip(f"{url} is not wired yet")
    r = client.get(url, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/secure-panel-inotex/login"


@pytest.mark.parametrize("url,script", PAGES)
def test_the_page_renders_and_loads_its_module(client, url, script):
    if url not in _registered_paths(client.app):
        pytest.skip(f"{url} is not wired yet")
    _login(client)
    r = client.get(url)
    assert r.status_code == 200
    assert script in r.text
    assert 'type="module"' in r.text
    # The page ships no dynamic values of its own — everything comes from the
    # API and is written with textContent by the module.
    assert "innerHTML" not in r.text
