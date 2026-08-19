"""The harness must be provably incapable of touching the operator's data.

If these fail, STOP — do not run the rest of the suite against this database.
"""
import os

from tests.postgres.conftest import LIVE_TABLES, dsn


def test_the_pool_is_pointed_at_the_throwaway_schemas(pg_pool, pg_schemas, conn):
    """An unqualified table name must resolve inside the test schema."""
    app_schema, obs_schema = pg_schemas
    row = conn.execute("SELECT current_schemas(false) AS s").fetchone()
    schemas = list(row["s"])
    assert schemas[0] == app_schema
    assert obs_schema in schemas
    # The live schema is absent on purpose: a table this harness forgot to
    # create must fail loudly, not silently read production rows.
    assert "app" not in schemas


def test_unqualified_names_resolve_to_the_test_schema(conn, pg_schemas, raw):
    app_schema, _obs = pg_schemas
    # `to_regclass(...)::text` prints unqualified whenever the schema is on the
    # search_path, so the schema is read from the catalog instead.
    resolved = conn.execute(
        "SELECT n.nspname AS s FROM pg_class c"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.oid = to_regclass('dataset')").fetchone()["s"]
    assert resolved == app_schema

    conn.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                 ("iso-check", "t", "x"))
    conn.commit()
    here = raw.execute(
        f'SELECT count(*) FROM "{app_schema}".dataset '
        "WHERE id = 'iso-check'").fetchone()[0]
    there = raw.execute(
        "SELECT count(*) FROM app.dataset WHERE id = 'iso-check'").fetchone()[0]
    assert here == 1
    assert there == 0, "a write landed in the LIVE app schema"


def test_live_tables_are_unchanged_so_far(_live_data_is_untouched):
    """Cross-checks the session guard mid-run, so a leak is attributed to the
    test that caused it rather than only surfacing at teardown."""
    import psycopg
    before = _live_data_is_untouched
    with psycopg.connect(dsn()) as c:
        for table in LIVE_TABLES:
            if before[table] is None:
                continue
            now = c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            assert now == before[table], f"{table} changed during the run"


def test_the_opt_in_flag_is_actually_set():
    """Proof the skip gate is real: reaching this line means the flag was on."""
    assert os.getenv("RUN_POSTGRES_TESTS", "").strip() not in ("", "0", "false")
