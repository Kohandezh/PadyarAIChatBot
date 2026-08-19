"""One test per class of bug that reached production because the suite ran on
SQLite.

Each of these fails against the pre-fix code and passes against the fix. They
are deliberately narrow — the point is not "PostgreSQL works", it is "this
specific SQLite-ism is still gone".
"""
import datetime
import json

import pytest


# ── 1. Booleans are BOOLEAN, not 0/1 ────────────────────────────────────

def test_a_python_bool_round_trips_as_a_real_boolean(conn):
    conn.execute(
        "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
        " enabled, has_secret) VALUES (?,?,?,?,?)",
        ("bool-rt", "openai", "Bool", True, False))
    conn.commit()
    row = conn.execute("SELECT enabled, has_secret FROM ai_provider_instances"
                       " WHERE id = ?", ("bool-rt",)).fetchone()
    # `is True`, not `== True`: 1 == True in Python, so equality would pass
    # against exactly the value this test exists to reject.
    assert row["enabled"] is True
    assert row["has_secret"] is False


@pytest.mark.parametrize("bad", [1, 0])
def test_an_integer_written_to_a_boolean_column_is_rejected(conn, bad):
    """SQLite stored 0/1 in a "BOOLEAN" column happily. PostgreSQL does not,
    and code that wrote `int(flag)` 500'd on the first admin toggle."""
    from psycopg import errors

    with pytest.raises(errors.DatatypeMismatch):
        conn.execute(
            "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
            " enabled) VALUES (?,?,?,?)",
            (f"bool-int-{bad}", "openai", "Bad", bad))
    conn.rollback()


# ── 2. Portable boolean SQL ─────────────────────────────────────────────

def _two_instances(conn):
    conn.execute(
        "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
        " enabled) VALUES (?,?,?,?), (?,?,?,?)",
        ("on-1", "openai", "On", True, "off-1", "openai", "Off", False))
    conn.commit()


def test_equals_true_and_the_bare_column_both_select_the_enabled_rows(conn):
    _two_instances(conn)
    for predicate in ("enabled = TRUE", "enabled", "enabled IS TRUE"):
        rows = conn.execute(
            f"SELECT id FROM ai_provider_instances WHERE {predicate}").fetchall()
        assert [r["id"] for r in rows] == ["on-1"], predicate


def test_the_sqlite_idiom_enabled_equals_1_is_a_hard_error(conn):
    """`WHERE enabled = 1` is valid SQLite and a 500 on PostgreSQL. Pinned so
    nobody reintroduces it believing it is portable."""
    from psycopg import errors

    _two_instances(conn)
    with pytest.raises(errors.UndefinedFunction):
        conn.execute("SELECT id FROM ai_provider_instances WHERE enabled = 1")
    conn.rollback()


# ── 3. JSONB comes back parsed ──────────────────────────────────────────

def test_jsonb_is_returned_as_a_native_dict(conn):
    payload = {"base_url": "https://example.invalid/v1", "n": 3,
               "fa": "پیکربندی", "nested": {"a": [1, 2]}}
    conn.execute(
        "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
        " config) VALUES (?,?,?,?)",
        ("jsonb-1", "openai", "J", json.dumps(payload, ensure_ascii=False)))
    conn.commit()
    got = conn.execute("SELECT config FROM ai_provider_instances WHERE id = ?",
                       ("jsonb-1",)).fetchone()["config"]
    assert isinstance(got, dict)
    assert got == payload


def test_load_json_survives_an_already_parsed_jsonb_value(conn):
    """`json.loads(dict)` raises TypeError, and the original handler swallowed
    it into `{}` — silently wiping every provider's configuration on read."""
    from app.services.ai.store import _load_json

    payload = {"base_url": "https://example.invalid/v1"}
    conn.execute(
        "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
        " config) VALUES (?,?,?,?)",
        ("jsonb-2", "openai", "J2", json.dumps(payload)))
    conn.commit()
    row = conn.execute("SELECT config FROM ai_provider_instances WHERE id = ?",
                       ("jsonb-2",)).fetchone()
    assert _load_json(row["config"]) == payload


def test_jsonb_metadata_list_shape_also_survives(conn):
    from app.services.ai.store import _load_json

    conn.execute("INSERT INTO ai_provider_instances (id, provider_type,"
                 " display_name) VALUES (?,?,?)", ("jm", "openai", "JM"))
    conn.execute("INSERT INTO ai_provider_models (provider_instance_id, model_id,"
                 " metadata) VALUES (?,?,?)", ("jm", "m-1", json.dumps(["a", "b"])))
    conn.commit()
    meta = conn.execute("SELECT metadata FROM ai_provider_models"
                        " WHERE provider_instance_id = ?", ("jm",)).fetchone()["metadata"]
    assert isinstance(meta, list)
    assert _load_json(meta) == ["a", "b"]


# ── 4. TIMESTAMPTZ is a datetime, not a string ──────────────────────────

def test_timestamptz_comes_back_as_an_aware_datetime(conn):
    expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash)"
                 " VALUES (?,?)", ("tzadmin", "x"))
    conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                 " VALUES (?,?,?)", ("tok-tz", "tzadmin", expiry))
    conn.commit()
    got = conn.execute("SELECT expiry FROM admin_sessions WHERE token = ?",
                       ("tok-tz",)).fetchone()["expiry"]
    assert isinstance(got, datetime.datetime)
    assert got.tzinfo is not None
    assert abs((got - expiry).total_seconds()) < 1


def test_fromisoformat_on_a_timestamptz_still_raises(conn):
    """The exact call that 500'd every admin request after cutover. Pinned so
    the reason `as_datetime()` exists cannot be forgotten and reverted."""
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash)"
                 " VALUES (?,?)", ("tzadmin2", "x"))
    conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                 " VALUES (?,?,?)",
                 ("tok-tz2", "tzadmin2",
                  datetime.datetime.now(datetime.timezone.utc)))
    conn.commit()
    raw_value = conn.execute("SELECT expiry FROM admin_sessions WHERE token = ?",
                             ("tok-tz2",)).fetchone()["expiry"]
    with pytest.raises(TypeError):
        datetime.datetime.fromisoformat(raw_value)


def test_the_timeutil_helpers_compare_a_real_timestamptz_correctly(conn):
    from app.db.timeutil import as_datetime, compare_now, to_naive_utc

    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash)"
                 " VALUES (?,?)", ("tzadmin3", "x"))
    conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                 " VALUES (?,?,?), (?,?,?)",
                 ("live", "tzadmin3", future, "dead", "tzadmin3", past))
    conn.commit()

    rows = {r["token"]: r["expiry"] for r in conn.execute(
        "SELECT token, expiry FROM admin_sessions").fetchall()}

    live = as_datetime(rows["live"])
    dead = as_datetime(rows["dead"])
    assert live > compare_now(live)          # would be TypeError if naive
    assert dead < compare_now(dead)
    assert to_naive_utc(rows["live"]).tzinfo is None


# ── 5. UNIQUE violations are recognised across backends ─────────────────

def test_a_duplicate_primary_key_raises_uniqueviolation(conn):
    from psycopg import errors

    from app.db import dberrors

    conn.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                 ("dup-1", "t", "x"))
    conn.commit()
    with pytest.raises(errors.UniqueViolation) as caught:
        conn.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                     ("dup-1", "t", "x"))
    assert dberrors.is_unique_violation(caught.value) is True
    conn.rollback()


def test_a_not_null_violation_is_not_mistaken_for_a_duplicate(conn):
    from psycopg import errors

    from app.db import dberrors

    with pytest.raises(errors.NotNullViolation) as caught:
        conn.execute("INSERT INTO ai_provider_instances (id, provider_type,"
                     " display_name) VALUES (?,?,?)", ("nn-1", None, "N"))
    assert dberrors.is_unique_violation(caught.value) is False
    assert dberrors.is_not_null_violation(caught.value) is True
    conn.rollback()


# ── 6. The transaction survives the error (the cascade) ─────────────────

def test_a_failed_statement_aborts_the_transaction_until_rollback(conn):
    """This is the one that turns a handled 4xx into a cascade of 500s.

    On SQLite an IntegrityError leaves the connection perfectly usable, so
    code that caught it and carried on was correct. On PostgreSQL the whole
    transaction is aborted: every later statement raises
    InFailedSqlTransaction until someone rolls back.
    """
    from psycopg import errors

    conn.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                 ("cascade", "t", "x"))
    conn.commit()

    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                     ("cascade", "t", "x"))
    with pytest.raises(errors.InFailedSqlTransaction):
        conn.execute("SELECT 1")

    conn.rollback()
    # ...and usable again afterwards, with the original row intact.
    assert conn.execute("SELECT count(*) AS n FROM dataset"
                        " WHERE id = ?", ("cascade",)).fetchone()["n"] == 1


def test_the_next_pooled_connection_is_clean_after_an_error(pg_clean):
    """The application's actual pattern: `with closing(get_db_connection())`.

    A connection handed back to the pool in a failed transaction would poison
    the next request. This proves the pool resets it.
    """
    from contextlib import closing

    from psycopg import errors

    from app.db.connection import get_db_connection

    with closing(get_db_connection()) as c:
        c.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                  ("pooled", "t", "x"))
        c.commit()
    with closing(get_db_connection()) as c:
        with pytest.raises(errors.UniqueViolation):
            c.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                      ("pooled", "t", "x"))
    # No rollback, no reset — just the next checkout, as a new request would.
    for _ in range(3):
        with closing(get_db_connection()) as c:
            assert c.execute("SELECT count(*) AS n FROM dataset").fetchone()["n"] == 1


def test_the_lastrowid_probe_cannot_abort_the_caller_transaction(conn):
    """`app/db/pg.py` emulates sqlite3's `lastrowid` with `SELECT lastval()`,
    which RAISES on a table that has no sequence — and a raised statement
    aborts the whole PostgreSQL transaction. The probe runs inside a SAVEPOINT
    precisely so an insert into `synonyms` (composite PK, no sequence) does not
    take the request down with it."""
    conn.execute("INSERT INTO synonyms (source, target) VALUES (?,?)",
                 ("اینوتکس", "inotex"))
    # NOTE: `lastrowid` is NOT asserted to be None here. `lastval()` is
    # SESSION-scoped, so on a pooled connection that already inserted into some
    # identity table it returns that unrelated sequence value instead of
    # raising. No caller in this codebase reads lastrowid after inserting into
    # a sequence-less table, so it is latent — but it is not None, and a test
    # claiming otherwise would be asserting a coincidence.
    # Still inside a live transaction: this would raise InFailedSqlTransaction
    # without the savepoint.
    conn.execute("INSERT INTO synonyms (source, target) VALUES (?,?)",
                 ("نمایشگاه", "expo"))
    conn.commit()
    assert conn.execute("SELECT count(*) AS n FROM synonyms").fetchone()["n"] == 2


def test_lastrowid_is_populated_for_an_identity_table(conn):
    conn.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                 ("q-owner", "t", "x"))
    cur = conn.execute("INSERT INTO questions (question, dataset_id)"
                       " VALUES (?,?)", ("اینوتکس کجاست؟", "q-owner"))
    conn.commit()
    assert isinstance(cur.lastrowid, int) and cur.lastrowid > 0
