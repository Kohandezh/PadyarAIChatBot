"""`cursor.lastrowid` must describe THIS statement's row, and no other.

THE DEFECT
----------
`app/db/pg.py` emulated sqlite3's `lastrowid` with `SELECT lastval()`.
`lastval()` is SESSION-scoped: it reports the last sequence touched anywhere on
the connection, not the last row this statement inserted. Connections are
pooled and reused, so an insert into a sequence-less table (`settings`,
`synonyms`, `dataset` — all keyed on values the caller supplies) reported the
id of an earlier, unrelated insert instead of None.

Nothing read `lastrowid` on those paths, so it never produced a visible bug.
That is exactly why it needed a test: a latent wrong answer that becomes a
silent data-integrity bug the moment someone trusts it — `app/db/connection.py`
already branches on `cursor.rowcount == 1` after an insert, and the same class
of caller for `lastrowid` is one feature away.

The fix appends `RETURNING <id>` to the insert itself, which cannot describe
any other statement.
"""
import pytest


def _instance(conn):
    """A provider instance to hang model rows off (they carry an FK)."""
    conn.execute(
        "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
        " enabled, trust_class, config, secret_enc)"
        " VALUES (?,?,?,?,?,?,?)",
        ("lr-inst", "openai_compatible", "LR", True, "public", "{}", ""))
    conn.commit()
    return "lr-inst"


# ── The generated id is returned correctly ──────────────────────────────

def test_a_sequence_backed_insert_returns_its_own_new_id(conn):
    iid = _instance(conn)
    cur = conn.execute(
        "INSERT INTO ai_provider_models (provider_instance_id, model_id,"
        " display_name, source, status) VALUES (?,?,?,?,?)",
        (iid, "m-one", "M1", "manual", "manual"))
    conn.commit()
    assert isinstance(cur.lastrowid, int)
    assert cur.lastrowid > 0

    stored = conn.execute(
        "SELECT id FROM ai_provider_models WHERE model_id = ?", ("m-one",)
    ).fetchone()["id"]
    assert cur.lastrowid == stored, "lastrowid did not match the row it created"


def test_consecutive_inserts_report_their_own_distinct_ids(conn):
    iid = _instance(conn)
    ids = []
    for n in range(3):
        cur = conn.execute(
            "INSERT INTO ai_provider_models (provider_instance_id, model_id,"
            " display_name, source, status) VALUES (?,?,?,?,?)",
            (iid, f"m-{n}", "M", "manual", "manual"))
        ids.append(cur.lastrowid)
    conn.commit()
    assert len(set(ids)) == 3, f"ids repeated: {ids}"
    assert ids == sorted(ids)


# ── The defect itself ───────────────────────────────────────────────────

@pytest.mark.parametrize("table, cols, values", [
    ("settings", "(key, value)", ("lr_probe_key", "v")),
    ("synonyms", "(source, target)", ("lr-probe-src", "lr-tgt")),
    ("dataset", "(id, title, text)", ("lr-probe-ds", "T", "X")),
])
def test_a_sequence_less_insert_never_inherits_an_earlier_id(conn, table, cols, values):
    """THE regression. Same connection, sequence-backed insert first.

    With `lastval()` the second insert returned the FIRST insert's id. It must
    be None: nothing generated an id here.
    """
    iid = _instance(conn)
    first = conn.execute(
        "INSERT INTO ai_provider_models (provider_instance_id, model_id,"
        " display_name, source, status) VALUES (?,?,?,?,?)",
        (iid, "m-before", "M", "manual", "manual"))
    generated = first.lastrowid
    assert generated, "precondition: the first insert must generate an id"

    marks = ",".join("?" for _ in values)
    second = conn.execute(f"INSERT INTO {table} {cols} VALUES ({marks})", values)
    conn.commit()

    assert second.lastrowid is None, (
        f"{table}.lastrowid leaked {second.lastrowid!r}; "
        f"the earlier insert generated {generated!r}")
    assert second.lastrowid != generated


def test_the_leak_cannot_cross_a_pooled_connection_reuse(conn, pg_pool):
    """The pool hands the same physical connection back out. A sequence used by
    a previous checkout must not be visible to the next one's `lastrowid`."""
    iid = _instance(conn)
    conn.execute(
        "INSERT INTO ai_provider_models (provider_instance_id, model_id,"
        " display_name, source, status) VALUES (?,?,?,?,?)",
        (iid, "m-pooled", "M", "manual", "manual"))
    conn.commit()

    from app.db import pg
    for round_ in range(3):
        other = pg.connect()
        try:
            cur = other.execute(
                "INSERT INTO settings (key, value) VALUES (?,?)",
                (f"lr_pooled_{round_}", "v"))
            other.commit()
            assert cur.lastrowid is None, (
                f"round {round_}: a reused connection reported "
                f"lastrowid={cur.lastrowid!r} for a sequence-less insert")
        finally:
            other.close()


# ── Behaviour that must not have changed ────────────────────────────────

def test_an_insert_that_conflicts_and_is_skipped_reports_no_id(conn):
    """`INSERT OR IGNORE` that hits an existing row inserted nothing, so there
    is no id to report — RETURNING yields no row."""
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", ("lr_k", "a"))
    conn.commit()
    cur = conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
                       ("lr_k", "b"))
    conn.commit()
    assert cur.lastrowid is None
    assert conn.execute("SELECT value FROM settings WHERE key = ?",
                        ("lr_k",)).fetchone()["value"] == "a"


def test_a_select_reports_no_lastrowid(conn):
    assert conn.execute("SELECT 1 AS one").lastrowid is None


def test_an_update_reports_no_lastrowid(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", ("lr_u", "a"))
    conn.commit()
    cur = conn.execute("UPDATE settings SET value = ? WHERE key = ?", ("b", "lr_u"))
    conn.commit()
    assert cur.lastrowid is None


def test_an_explicit_returning_clause_is_left_alone(conn):
    """A caller that wrote its own RETURNING must keep its result set — the
    emulation must not append a second one or consume the caller's rows."""
    iid = _instance(conn)
    cur = conn.execute(
        "INSERT INTO ai_provider_models (provider_instance_id, model_id,"
        " display_name, source, status) VALUES (?,?,?,?,?) RETURNING model_id",
        (iid, "m-explicit", "M", "manual", "manual"))
    conn.commit()
    assert cur.fetchone()["model_id"] == "m-explicit"


def test_real_callers_that_read_lastrowid_still_work(conn):
    """`store.add_target` and `applog.record` both read `lastrowid` after an
    insert into a sequence-backed table. They must keep getting real ids."""
    from app.services.ai import store
    iid = _instance(conn)
    conn.execute(
        "INSERT INTO ai_provider_models (provider_instance_id, model_id,"
        " display_name, source, status) VALUES (?,?,?,?,?)",
        (iid, "gpt-x", "M", "manual", "manual"))
    conn.commit()
    target_id = store.add_target("chat", iid, "gpt-x", actor="test")
    assert isinstance(target_id, int) and target_id > 0
