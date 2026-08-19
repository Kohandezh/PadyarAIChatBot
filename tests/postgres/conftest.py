"""Fixtures for the REAL-PostgreSQL integration suite.

WHY THIS DIRECTORY EXISTS
-------------------------
`tests/conftest.py` pins `DB_BACKEND=sqlite` so the main suite is hermetic and
fast. Production is PostgreSQL 16. Five bugs have already escaped through that
gap — int-for-boolean writes, `enabled = 1` comparisons, `json.loads()` on an
already-parsed JSONB dict, TIMESTAMPTZ returned as a datetime but compared as a
string, and `sqlite3.IntegrityError` never matching psycopg's `UniqueViolation`.
None of them were visible to a SQLite test, because on SQLite they are not bugs.

These tests close that gap by talking to a real server. They are OPT-IN
(`RUN_POSTGRES_TESTS=1`) so the default suite stays fast, hermetic and green on
a machine with no PostgreSQL.

ISOLATION — READ THIS BEFORE ADDING A TEST
------------------------------------------
The developer's database holds real content, a real provider instance, real
admins and real usage history. Nothing here may touch it.

So the session fixture creates TWO THROWAWAY SCHEMAS inside the configured
database — `padyar_test_<pid>_<rand>` and `..._obs` — applies `migrations/*.sql`
into them (with the hard-coded `app.` / `observability.` prefixes rewritten),
points the connection pool's `search_path` at them, and DROPs both at the end.

`app` is deliberately NOT on the test `search_path`. A table this harness
forgot to create therefore fails loudly with "relation does not exist" instead
of silently resolving to the operator's live table.

A separate DATABASE would be stronger still, but `padyar_app` has no CREATEDB
privilege, and requiring a superuser DSN just to run tests would mean nobody
runs them. Schema isolation needs only what the app's own role already has.

Belt and braces: `_live_data_is_untouched` snapshots row counts of the live
`app.*` tables before and after the whole session and fails if anything moved.
`test_isolation.py` asserts the same thing where a reader can see it.
"""
import os
import re
import secrets

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

FLAG = "RUN_POSTGRES_TESTS"

# Counted before and after the session. Only tables that change on deliberate
# operator action — `chat_logs` and the observability tables would move on
# their own if the operator happens to have the app running, and a flaky
# guard is a guard people learn to ignore.
LIVE_TABLES = (
    "app.dataset", "app.questions", "app.synonyms", "app.settings",
    "app.admins", "app.ai_provider_instances", "app.ai_provider_models",
    "app.ai_routes", "app.ai_route_targets",
)


def dsn() -> str:
    """Same default as `app/db/pg.py` and `scripts/apply_migrations.py`."""
    return os.getenv(
        "DATABASE_URL",
        "postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar")


def _enabled() -> bool:
    return os.getenv(FLAG, "").strip().lower() not in ("", "0", "false", "no")


def _skip_reason() -> str:
    """Why this suite cannot run — empty string when it can.

    Both conditions SKIP rather than error: a developer without a local server,
    and every CI job that has not opted in, must still see a green run.
    """
    if not _enabled():
        return (f"{FLAG} is not set — real-PostgreSQL tests are opt-in "
                f"(see docs/engineering/POSTGRES_TESTING.md)")
    try:
        import psycopg  # noqa: F401
        from psycopg_pool import ConnectionPool  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"psycopg is not installed ({type(exc).__name__})"
    try:
        import psycopg
        with psycopg.connect(dsn(), connect_timeout=5) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        return f"PostgreSQL unreachable at DATABASE_URL ({type(exc).__name__})"
    return ""


def pytest_collection_modifyitems(config, items):
    """Skip everything under tests/postgres unless the flag is set AND the
    server answers. The check runs once, not per test."""
    reason = _skip_reason()
    if not reason:
        return
    mark = pytest.mark.skip(reason=reason)
    for item in items:
        if str(item.fspath).startswith(HERE + os.sep):
            item.add_marker(mark)


# ── Schema lifecycle ────────────────────────────────────────────────────

_BEGIN_COMMIT = re.compile(r"^\s*(BEGIN|COMMIT)\s*;\s*$", re.I | re.M)


def _retarget(body: str, app_schema: str, obs_schema: str) -> str:
    """Point a migration file at the throwaway schemas.

    `migrations/*.sql` hard-codes `app.` and `observability.`, because in
    production there is exactly one of each. Rewriting the prefix is the only
    way to apply the REAL migration text (not a hand-copied approximation)
    somewhere safe — and applying the real text is the whole point: a test
    against a re-typed schema proves nothing about production.

    BEGIN/COMMIT are stripped because each file is run inside a transaction
    here, exactly as `scripts/apply_migrations.py` does it.
    """
    body = re.sub(r"\bapp\.", f"{app_schema}.", body)
    body = re.sub(r"\bobservability\.", f"{obs_schema}.", body)
    return _BEGIN_COMMIT.sub("", body)


def _migration_files():
    return [os.path.join(MIGRATIONS_DIR, n)
            for n in sorted(os.listdir(MIGRATIONS_DIR)) if n.endswith(".sql")]


@pytest.fixture(scope="session")
def pg_schemas():
    """Create the throwaway schemas, migrate them, drop them afterwards.

    Yields `(app_schema, obs_schema)`.
    """
    import psycopg

    suffix = f"{os.getpid()}_{secrets.token_hex(3)}"
    app_schema = f"padyar_test_{suffix}"
    obs_schema = f"padyar_test_{suffix}_obs"
    search_path = f"-c search_path={app_schema},{obs_schema},public"

    with psycopg.connect(dsn(), autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{app_schema}"')
        conn.execute(f'CREATE SCHEMA "{obs_schema}"')

    try:
        with psycopg.connect(dsn(), options=search_path) as conn:
            for path in _migration_files():
                with open(path, encoding="utf-8") as fh:
                    body = _retarget(fh.read(), app_schema, obs_schema)
                with conn.transaction():
                    conn.execute(body)
        yield app_schema, obs_schema
    finally:
        with psycopg.connect(dsn(), autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{app_schema}" CASCADE')
            conn.execute(f'DROP SCHEMA IF EXISTS "{obs_schema}" CASCADE')
            # Teardown proof: the schemas are gone, not merely emptied.
            left = conn.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname = ANY(%s)",
                ([app_schema, obs_schema],)).fetchone()[0]
            assert left == 0, f"test schemas survived teardown: {left} left"


@pytest.fixture(scope="session", autouse=True)
def _live_data_is_untouched(pg_schemas):
    """Fail the session if anything in the operator's live `app` schema moved.

    A leak here is not a test failure to shrug at — it means a run of the test
    suite edited production content.
    """
    import psycopg

    def snapshot():
        out = {}
        with psycopg.connect(dsn()) as conn:
            for table in LIVE_TABLES:
                try:
                    out[table] = conn.execute(
                        f"SELECT count(*) FROM {table}").fetchone()[0]
                except Exception:  # noqa: BLE001 — table absent on this install
                    conn.rollback()
                    out[table] = None
        return out

    before = snapshot()
    yield before
    after = snapshot()
    assert before == after, (
        "LIVE DATA CHANGED during the PostgreSQL test session — "
        f"before={before} after={after}")


# ── Pool + backend wiring ───────────────────────────────────────────────

@pytest.fixture(scope="session")
def pg_pool(pg_schemas):
    """Replace `app.db.pg`'s process-wide pool with one bound to the test
    schemas.

    Patched rather than reconfigured because `pool()` hard-codes
    `search_path=app,observability,public` as a connection option (and it must:
    that is what production needs). Swapping the module global is the smallest
    seam that redirects the whole application — every call site goes through
    `get_db_connection()` → `pg.connect()` → `pool()`.
    """
    from psycopg_pool import ConnectionPool
    from app.db import pg

    app_schema, obs_schema = pg_schemas
    test_pool = ConnectionPool(
        conninfo=dsn(),
        min_size=1, max_size=5, timeout=10,
        kwargs={"options": f"-c search_path={app_schema},{obs_schema},public"},
        open=True, name="padyar-tests",
    )
    previous = pg._pool
    pg._pool = test_pool
    pg._PK_CACHE.clear()
    try:
        yield test_pool
    finally:
        pg._pool = previous
        pg._PK_CACHE.clear()
        test_pool.close()


def _all_tables(pg_schemas):
    import psycopg
    app_schema, obs_schema = pg_schemas
    with psycopg.connect(dsn()) as conn:
        rows = conn.execute(
            "SELECT schemaname, tablename FROM pg_tables"
            " WHERE schemaname = ANY(%s)",
            ([app_schema, obs_schema],)).fetchall()
    return [f'"{s}"."{t}"' for s, t in rows]


@pytest.fixture(scope="session")
def _table_list(pg_schemas):
    return _all_tables(pg_schemas)


@pytest.fixture(autouse=True)
def pg_clean(pg_pool, _table_list, pg_schemas, monkeypatch):
    """Point the app at PostgreSQL and give every test an empty schema.

    TRUNCATE, not DELETE: identity sequences restart, so a test that asserts on
    a generated id is not silently coupled to how many rows ran before it.
    """
    import psycopg
    import app.config as config

    app_schema, _obs = pg_schemas
    if _table_list:
        with psycopg.connect(dsn(), autocommit=True) as conn:
            conn.execute("TRUNCATE TABLE " + ", ".join(_table_list) +
                         " RESTART IDENTITY CASCADE")
            # migration 0003 seeds the two routable tasks; TRUNCATE removed
            # them, and route tests depend on the FK target existing.
            conn.execute(
                f'INSERT INTO "{app_schema}".ai_routes (task, description)'
                " VALUES ('chat',''), ('classify','')"
                " ON CONFLICT (task) DO NOTHING")

    # The root conftest pins DB_BACKEND=sqlite for the whole process. Every
    # reader does a late `from app.config import DB_BACKEND`, so patching the
    # module attribute is enough and is undone per test.
    monkeypatch.setattr(config, "DB_BACKEND", "postgres")

    from app.services.ai import store as ai_store
    ai_store._invalidate_runtime()
    yield
    ai_store._invalidate_runtime()


@pytest.fixture
def conn(pg_clean):
    """A connection through the REAL adapter (`app/db/pg.py`), not raw psycopg.

    Tests here are about what the application sees, and the adapter is where
    placeholder translation, `Row` and `lastrowid` emulation live.
    """
    from app.db.connection import get_db_connection
    c = get_db_connection()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def raw(pg_schemas):
    """A raw psycopg connection on the test schemas, for the few assertions
    that must bypass the adapter (e.g. "what type did the column actually
    get?")."""
    import psycopg
    app_schema, obs_schema = pg_schemas
    with psycopg.connect(
            dsn(), autocommit=True,
            options=f"-c search_path={app_schema},{obs_schema},public") as c:
        yield c


# ── Authenticated admin client ──────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch, pg_clean):
    """A TestClient whose application data lives in the test schemas.

    Mirrors the session + CSRF pattern of `tests/test_ai_admin_ui.py`. `DB_PATH`
    is still redirected: `init_db()` runs unconditionally at startup and is
    SQLite-only, so without this it would write into the operator's real
    `chat_history.db`.
    """
    import datetime

    from fastapi.testclient import TestClient

    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "startup_only.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    # Suppress the automatic legacy import: env fallbacks would otherwise
    # create a migrated provider instance during startup and pollute the
    # provider assertions below.
    monkeypatch.setattr("app.services.openai.OPENAI_API_BASE", "")
    monkeypatch.setattr("app.services.openai.OPENAI_API_KEY", "")

    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        db = get_db_connection()
        token = secrets.token_hex(16)
        db.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                   " security_question, security_answer_hash)"
                   " VALUES ('pgadmin','x','y','q','z')")
        db.execute("INSERT INTO admin_sessions (token, username, expiry)"
                   " VALUES (?,?,?)",
                   (token, "pgadmin",
                    datetime.datetime.now() + datetime.timedelta(hours=1)))
        db.commit()
        db.close()
        c.cookies.set("admin_session", token)
        c.session_token = token
        from app.auth.csrf import token_for_session
        c.headers.update({"X-CSRF-Token": token_for_session(token)})
        yield c
