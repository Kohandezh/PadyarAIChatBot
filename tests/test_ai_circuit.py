"""Circuit-breaker state machine, and its behaviour under real concurrency.

Two halves:

* The SQLite half proves the STATE MACHINE — thresholds, the sliding window,
  the instant auth trip, cooldown → half-open → closed/open, and the admin
  reset. Single-threaded and deterministic.

* The PostgreSQL half proves the SHARED-STATE claims that SQLite and mocks
  cannot: two real worker connections racing for the single half-open probe
  lease, an abandoned lease expiring, and concurrent failure recording not
  losing counts. Production runs several gunicorn workers against PostgreSQL,
  so "all workers agree" is only meaningful when it is tested THERE. These
  tests skip cleanly when no server is reachable.

  They create their own provider instance with a random id and delete every
  row they created (circuit state, provider, and the observability rows the
  breaker logged) in fixture teardown.

Regression anchor: before this file existed, five concurrent failover-eligible
failures on PostgreSQL left `failure_count = 2` and the circuit CLOSED —
`record_failure` read the counter into Python and wrote it back, so
simultaneous workers overwrote each other. The breaker did not trip under
exactly the load it exists to protect against.
"""
import asyncio
import datetime
import secrets
import threading

import pytest

from app.services.ai import circuit, errors as ai_errors, store


# ── Helpers ─────────────────────────────────────────────────────────────

def _fail(code="server_error"):
    return ai_errors.AIError(code=code, provider_detail="boom")


def _auth_fail():
    return ai_errors.AIError(code="authentication_failed", provider_detail="401")


def _iso(offset_s=0):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=offset_s)
    return t.isoformat(timespec="seconds")


def _row(iid):
    return circuit.snapshot(iid)[0]


def _set_columns(iid, **cols):
    """Force circuit columns directly — the only way to make time pass.

    The rowcount assertion is deliberate: an UPDATE against a row that does
    not exist yet is a silent no-op, and a test built on one proves nothing.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        sets = ", ".join(f"{k}=?" for k in cols)
        changed = conn.execute(
            f"UPDATE ai_circuit_state SET {sets} WHERE provider_instance_id=?",
            (*cols.values(), iid)).rowcount
        conn.commit()
        assert changed == 1, "no circuit row to set up"
    finally:
        conn.close()


# ── SQLite fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def ai_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "circuit.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    store.ensure_ai_tables()
    store._invalidate_runtime()
    yield
    store._invalidate_runtime()


@pytest.fixture
def iid(ai_db):
    return store.create_instance("openai", "Circuit Under Test", {}, "sk-x", enabled=True)


# ══════════════════════════════════════════════════════════════════════
# 1–3. Threshold and the sliding window
# ══════════════════════════════════════════════════════════════════════

def test_failures_below_the_threshold_do_not_open_the_circuit(iid):
    threshold = circuit._setting_int("ai_circuit_threshold", circuit.DEFAULT_THRESHOLD)
    for _ in range(threshold - 1):
        assert circuit.record_failure(iid, _fail()) == "closed"
    assert _row(iid)["state"] == "closed"
    assert _row(iid)["failure_count"] == threshold - 1
    assert circuit.allows(iid) == (True, "")


def test_the_threshold_reached_inside_the_window_opens_the_circuit(iid):
    threshold = circuit._setting_int("ai_circuit_threshold", circuit.DEFAULT_THRESHOLD)
    states = [circuit.record_failure(iid, _fail()) for _ in range(threshold)]
    assert states[-1] == "open"
    assert states[:-1] == ["closed"] * (threshold - 1)
    allowed, why = circuit.allows(iid)
    assert allowed is False and "open until" in why


def test_failures_spread_wider_than_the_window_never_accumulate(iid):
    """The window must SLIDE. Four failures per window, forever, must never
    trip a five-in-window breaker — otherwise a provider with a steady low
    error rate is taken out of rotation for no reason."""
    threshold = circuit._setting_int("ai_circuit_threshold", circuit.DEFAULT_THRESHOLD)
    window_s = circuit._setting_int("ai_circuit_window_s", circuit.DEFAULT_WINDOW_S)
    for _ in range(6):                       # six consecutive windows
        for _ in range(threshold - 1):
            circuit.record_failure(iid, _fail())
        assert _row(iid)["state"] == "closed"
        assert _row(iid)["failure_count"] == threshold - 1
        # age the window out, exactly as wall-clock time would
        _set_columns(iid, window_started_at=_iso(-(window_s + 5)))
    assert _row(iid)["state"] == "closed"
    # and the very next failure starts a FRESH window at 1, not at 4+1
    circuit.record_failure(iid, _fail())
    assert _row(iid)["failure_count"] == 1


def test_the_counter_resets_to_one_when_the_window_has_aged_out(iid):
    window_s = circuit._setting_int("ai_circuit_window_s", circuit.DEFAULT_WINDOW_S)
    circuit.record_failure(iid, _fail())
    circuit.record_failure(iid, _fail())
    assert _row(iid)["failure_count"] == 2
    _set_columns(iid, window_started_at=_iso(-(window_s + 1)))
    circuit.record_failure(iid, _fail())
    assert _row(iid)["failure_count"] == 1


def test_non_failover_errors_never_count_against_the_provider(iid):
    threshold = circuit._setting_int("ai_circuit_threshold", circuit.DEFAULT_THRESHOLD)
    for code in ("invalid_request", "content_rejected", "context_limit_exceeded"):
        for _ in range(threshold * 2):
            assert circuit.record_failure(iid, _fail(code)) == "closed"
    assert _row(iid)["state"] == "closed"
    assert _row(iid)["failure_count"] == 0
    assert circuit.allows(iid) == (True, "")


# ══════════════════════════════════════════════════════════════════════
# 4. Auth failures trip instantly
# ══════════════════════════════════════════════════════════════════════

def test_authentication_failure_opens_on_the_very_first_failure(iid):
    """A known-broken credential must never be hammered: one 401 is proof."""
    assert circuit.record_failure(iid, _auth_fail()) == "open"
    row = _row(iid)
    assert row["state"] == "open"
    assert row["last_failure_code"] == "authentication_failed"
    assert circuit.allows(iid)[0] is False


def test_authentication_failure_gets_the_long_cooldown(iid):
    from app.db.timeutil import as_datetime
    circuit.record_failure(iid, _auth_fail())
    cooldown = as_datetime(_row(iid)["cooldown_until"])
    ahead = (cooldown - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    # the auth cooldown, not the ordinary one
    assert ahead > circuit.DEFAULT_COOLDOWN_S
    assert ahead <= circuit.AUTH_COOLDOWN_S + 5


# ══════════════════════════════════════════════════════════════════════
# 5. An open circuit costs the provider nothing
# ══════════════════════════════════════════════════════════════════════

class _FakeAdapter:
    """Records every invoke. A call here means a socket in production."""

    def __init__(self):
        self.calls = []

    async def invoke(self, rt, model_id, req):
        self.calls.append((rt.instance_id, model_id))
        raise ai_errors.AIError(code="server_error", provider_detail="never")


def test_an_open_circuit_skips_the_target_without_any_network_call(ai_db, monkeypatch):
    from app.services.ai import engine
    from app.services.ai.request import AIRequest, AIMessage

    only = store.create_instance("openai", "Only", {}, "sk-x", enabled=True)
    store.add_manual_model(only, "m1")
    store.add_target("chat", only, "m1")
    fake = _FakeAdapter()
    monkeypatch.setattr(engine, "adapter_for", lambda ptype: fake)

    circuit.record_failure(only, _auth_fail())        # instant open
    assert circuit.allows(only)[0] is False

    request = AIRequest(task="chat", messages=[AIMessage(role="user", content="q")],
                        system_prompt="s")
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(request))
    assert e.value.code == ai_errors.ALL_ROUTES_FAILED
    assert fake.calls == [], "the adapter was invoked while the circuit was OPEN"


# ══════════════════════════════════════════════════════════════════════
# 6, 9, 10. Cooldown → half-open → closed / open
# ══════════════════════════════════════════════════════════════════════

def _open_with_elapsed_cooldown(iid):
    circuit.record_failure(iid, _auth_fail())
    _set_columns(iid, cooldown_until=_iso(-3600))


def test_cooldown_expiry_moves_the_circuit_to_half_open(iid):
    _open_with_elapsed_cooldown(iid)
    allowed, why = circuit.allows(iid)
    assert allowed is True and "half-open probe" in why
    assert _row(iid)["state"] == "half_open"
    assert _row(iid)["probe_owner"] != ""
    assert _row(iid)["probe_expires_at"] is not None


def test_a_probe_success_closes_the_circuit_and_resets_the_counters(iid):
    _open_with_elapsed_cooldown(iid)
    assert circuit.allows(iid)[0] is True             # win the probe
    circuit.record_success(iid)
    row = _row(iid)
    assert row["state"] == "closed"
    assert row["failure_count"] == 0
    assert row["window_started_at"] is None
    assert row["opened_at"] is None
    assert row["cooldown_until"] is None
    assert row["probe_owner"] == ""
    assert row["probe_expires_at"] is None
    assert row["last_success_at"] is not None
    assert circuit.allows(iid) == (True, "")


def test_a_probe_failure_reopens_with_a_fresh_cooldown(iid):
    from app.db.timeutil import as_datetime
    _open_with_elapsed_cooldown(iid)
    assert circuit.allows(iid)[0] is True             # win the probe
    assert _row(iid)["state"] == "half_open"
    assert circuit.record_failure(iid, _fail()) == "open"
    row = _row(iid)
    assert row["state"] == "open"
    assert row["probe_owner"] == ""
    assert row["probe_expires_at"] is None
    cooldown = as_datetime(row["cooldown_until"])
    assert cooldown > datetime.datetime.now(datetime.timezone.utc)
    assert circuit.allows(iid)[0] is False            # closed again to traffic


def test_a_second_worker_cannot_probe_while_a_lease_is_live(iid):
    _open_with_elapsed_cooldown(iid)
    first = circuit.allows(iid)
    second = circuit.allows(iid)
    third = circuit.allows(iid)
    assert first[0] is True
    assert (second[0], third[0]) == (False, False)
    assert "probe" in second[1]


# ══════════════════════════════════════════════════════════════════════
# 8. A lease nobody releases must expire
# ══════════════════════════════════════════════════════════════════════

def test_an_abandoned_probe_lease_expires_so_the_circuit_cannot_wedge(iid):
    """If the probing worker is SIGKILLed mid-request nothing releases the
    lease. Without expiry the provider would stay unreachable forever."""
    circuit.reset(iid)                                   # materialize the row
    _set_columns(iid, state="half_open", probe_owner="dead-worker",
                 probe_expires_at=_iso(-1), cooldown_until=_iso(-3600))
    allowed, why = circuit.allows(iid)
    assert allowed is True and "reclaimed" in why
    assert _row(iid)["probe_owner"] != "dead-worker"


def test_a_live_probe_lease_is_not_reclaimed(iid):
    circuit.reset(iid)                                   # materialize the row
    _set_columns(iid, state="half_open", probe_owner="busy-worker",
                 probe_expires_at=_iso(circuit.PROBE_LEASE_S))
    assert circuit.allows(iid)[0] is False
    assert _row(iid)["probe_owner"] == "busy-worker"


# ══════════════════════════════════════════════════════════════════════
# Timestamp handling — the TIMESTAMPTZ / ISO-string class of bug
# ══════════════════════════════════════════════════════════════════════

def test_a_native_datetime_column_value_is_compared_not_string_matched(iid):
    """PostgreSQL hands back a real aware datetime for TIMESTAMPTZ; SQLite
    hands back the ISO string we wrote. Every read must survive both, and a
    naive datetime must not raise on comparison with an aware one."""
    from datetime import timezone
    aware = datetime.datetime.now(timezone.utc)
    naive = datetime.datetime.now()
    assert circuit._as_dt(aware) == aware
    assert circuit._as_dt(naive).tzinfo is not None          # assumed UTC
    assert circuit._as_dt(aware.isoformat(timespec="seconds")).tzinfo is not None
    assert circuit._as_dt(None) is None
    assert circuit._as_dt("") is None
    assert circuit._as_dt("not-a-timestamp") is None
    # and the comparison the breaker actually performs is legal both ways
    assert circuit._as_dt(aware) > circuit._as_dt(circuit._now_iso(-60))


@pytest.mark.filterwarnings("ignore:The default datetime adapter is deprecated")
def test_allows_handles_a_datetime_cooldown_without_raising(iid):
    """The regression this whole class of bug is named for: a datetime object
    where the code expected an ISO string."""
    circuit.record_failure(iid, _auth_fail())
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
    _set_columns(iid, cooldown_until=future)             # a real datetime, not a str
    allowed, why = circuit.allows(iid)
    assert allowed is False and "open until" in why


# ══════════════════════════════════════════════════════════════════════
# 11. Admin manual reset — works, authorized, audited
# ══════════════════════════════════════════════════════════════════════

def test_manual_reset_forces_the_circuit_closed(iid):
    circuit.record_failure(iid, _auth_fail())
    assert _row(iid)["state"] == "open"
    circuit.reset(iid, actor="admin")
    row = _row(iid)
    assert row["state"] == "closed"
    assert row["failure_count"] == 0
    assert row["cooldown_until"] is None
    assert row["probe_owner"] == ""
    assert circuit.allows(iid) == (True, "")


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "circuit_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr("app.services.openai.OPENAI_API_BASE", "")
    monkeypatch.setattr("app.services.openai.OPENAI_API_KEY", "")
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        store.ensure_ai_tables()
        store._invalidate_runtime()
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('circuitadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "circuitadmin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.session_token = token
        yield c
    store._invalidate_runtime()


def test_reset_circuit_endpoint_rejects_an_unauthenticated_caller(admin_client):
    inst = store.create_instance("openai", "Guarded", {}, "sk-x", enabled=True)
    circuit.record_failure(inst, _auth_fail())
    r = admin_client.post(f"/admin/api/ai/providers/{inst}/reset-circuit")
    assert r.status_code in (401, 403)
    assert _row(inst)["state"] == "open", "an anonymous caller reset the breaker"


def test_reset_circuit_endpoint_closes_the_circuit_and_writes_an_audit_row(admin_client):
    inst = store.create_instance("openai", "Guarded", {}, "sk-x", enabled=True)
    circuit.record_failure(inst, _auth_fail())
    admin_client.cookies.set("admin_session", admin_client.session_token)
    csrf = admin_client.get("/admin/csrf").json()["csrf_token"]

    # CSRF is required even with a valid session
    denied = admin_client.post(f"/admin/api/ai/providers/{inst}/reset-circuit", json={})
    assert denied.status_code in (400, 403)
    assert _row(inst)["state"] == "open"

    ok = admin_client.post(f"/admin/api/ai/providers/{inst}/reset-circuit",
                           json={}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert _row(inst)["state"] == "closed"

    from app.services import applog
    rows, _total = applog.query(tables=["audit_logs"], limit=200)
    hits = [dict(r) for r in rows
            if dict(r).get("event_name") == "admin.ai_circuit.reset"
            and dict(r).get("target") == inst]
    assert hits, "the manual reset was not audited"
    assert hits[0].get("actor"), "the audit row does not name an actor"


# ══════════════════════════════════════════════════════════════════════
# PostgreSQL — the shared-state claims. Mocks and SQLite cannot prove these.
# ══════════════════════════════════════════════════════════════════════

def _postgres_reachable():
    try:
        from app.db import pg
        return pg.healthy()[0]
    except Exception:                                    # noqa: BLE001
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_reachable(),
    reason="no PostgreSQL server reachable — shared-state claims cannot be proven")


@pytest.fixture(scope="module", autouse=True)
def _release_the_pool():
    """Hand the connection pool back when this module is done.

    A pool left open is torn down by the garbage collector at interpreter
    shutdown, where joining its worker threads raises PythonFinalizationError
    and prints a traceback over the pytest summary. `pool()` recreates it
    lazily, so a later module that needs PostgreSQL is unaffected.
    """
    yield
    try:
        from app.db import pg
        pg.close_pool()
    except Exception:                                    # noqa: BLE001
        pass


@pytest.fixture
def pg_iid(monkeypatch):
    """A throwaway provider instance in the REAL PostgreSQL control plane.

    Every row this fixture or its test creates is deleted again: the circuit
    row, the provider row, and the observability rows the breaker wrote.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    from app.db.connection import get_db_connection

    instance_id = "circuit-test-" + secrets.token_hex(5)
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO ai_provider_instances (id, provider_type,"
                     " display_name, enabled) VALUES (?,?,?,?)",
                     (instance_id, "openai", "circuit test (temporary)", False))
        conn.commit()
    finally:
        conn.close()
    try:
        yield instance_id
    finally:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM ai_circuit_state WHERE provider_instance_id=?",
                         (instance_id,))
            conn.execute("DELETE FROM ai_provider_instances WHERE id=?", (instance_id,))
            for table in ("app_logs", "audit_logs", "security_events", "service_events"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE target=?", (instance_id,))
                except Exception:                        # noqa: BLE001
                    conn.rollback()
            conn.commit()
        finally:
            conn.close()


def _pg_row(iid):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return dict(conn.execute(
            "SELECT * FROM ai_circuit_state WHERE provider_instance_id=?",
            (iid,)).fetchone())
    finally:
        conn.close()


def _run_workers(n, fn):
    """n threads, each with its OWN pooled connection, released together."""
    gate = threading.Barrier(n)
    results, lock = [], threading.Lock()

    def worker(index):
        gate.wait()
        try:
            out = fn(index)
        except Exception as exc:                         # noqa: BLE001
            out = exc
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "a worker thread hung"
    return results


@requires_postgres
def test_pg_concurrent_failures_from_many_connections_lose_no_counts(pg_iid):
    """THE regression test. Eight workers each record one failover-eligible
    failure at the same instant against real PostgreSQL. Every one must land.

    Before the fix this produced failure_count=2 with the circuit still
    CLOSED: the counter was read into Python and written back, so workers
    silently overwrote each other and the breaker never reached its threshold
    under the concurrency it exists for.
    """
    workers = 8
    results = _run_workers(workers, lambda _: circuit.record_failure(pg_iid, _fail()))
    assert all(not isinstance(r, Exception) for r in results), results
    row = _pg_row(pg_iid)
    assert row["failure_count"] == workers, (
        f"lost counts: {row['failure_count']} of {workers} failures recorded")
    threshold = circuit._setting_int("ai_circuit_threshold", circuit.DEFAULT_THRESHOLD)
    assert workers >= threshold
    assert row["state"] == "open", "the breaker did not trip under parallel load"


@requires_postgres
def test_pg_only_one_of_six_workers_wins_the_half_open_probe(pg_iid):
    """The thundering herd. Six workers all see an OPEN circuit whose cooldown
    has elapsed; exactly ONE may probe the struggling provider."""
    circuit.record_failure(pg_iid, _auth_fail())
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_circuit_state SET cooldown_until=? WHERE provider_instance_id=?",
                 (_iso(-3600), pg_iid))
    conn.commit()
    conn.close()

    results = _run_workers(6, lambda _: circuit.allows(pg_iid))
    assert all(not isinstance(r, Exception) for r in results), results
    winners = [r for r in results if r[0] is True]
    assert len(winners) == 1, f"{len(winners)} workers probed at once: {results}"
    row = _pg_row(pg_iid)
    assert row["state"] == "half_open"
    assert row["probe_owner"] != ""


@requires_postgres
def test_pg_the_probe_lease_stays_exclusive_after_the_winner_is_chosen(pg_iid):
    """Late arrivals must not slip in behind the winner while the lease runs."""
    circuit.record_failure(pg_iid, _auth_fail())
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_circuit_state SET cooldown_until=? WHERE provider_instance_id=?",
                 (_iso(-3600), pg_iid))
    conn.commit()
    conn.close()
    assert circuit.allows(pg_iid)[0] is True             # the winner
    owner = _pg_row(pg_iid)["probe_owner"]
    later = _run_workers(4, lambda _: circuit.allows(pg_iid))
    assert [r[0] for r in later] == [False] * 4
    assert _pg_row(pg_iid)["probe_owner"] == owner


@requires_postgres
def test_pg_an_abandoned_lease_expires_and_exactly_one_worker_reclaims_it(pg_iid):
    """The probing worker died. The lease must expire, and the reclaim must
    itself be a single-winner race — not a second thundering herd."""
    circuit.record_failure(pg_iid, _auth_fail())
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_circuit_state SET state='half_open', probe_owner='dead',"
                 " probe_expires_at=?, cooldown_until=? WHERE provider_instance_id=?",
                 (_iso(-1), _iso(-3600), pg_iid))
    conn.commit()
    conn.close()

    results = _run_workers(5, lambda _: circuit.allows(pg_iid))
    assert all(not isinstance(r, Exception) for r in results), results
    winners = [r for r in results if r[0] is True]
    assert len(winners) == 1, f"{len(winners)} workers reclaimed the lease: {results}"
    assert _pg_row(pg_iid)["probe_owner"] not in ("", "dead")


@requires_postgres
def test_pg_timestamptz_columns_come_back_as_aware_datetimes_and_still_compare(pg_iid):
    """The known past bug: the column returns a datetime while the code
    compares ISO strings. Assert the SHAPE, then assert the breaker's own
    decision built on it."""
    circuit.record_failure(pg_iid, _auth_fail())
    row = _pg_row(pg_iid)
    for column in ("cooldown_until", "opened_at", "window_started_at",
                   "last_failure_at", "updated_at"):
        value = row[column]
        assert isinstance(value, datetime.datetime), f"{column} is {type(value)}"
        assert value.tzinfo is not None, f"{column} came back naive"
    # aware datetime vs the breaker's ISO "now" — must not raise, must be right
    assert circuit._as_dt(row["cooldown_until"]) > circuit._as_dt(circuit._now_iso())
    allowed, why = circuit.allows(pg_iid)
    assert allowed is False and "open until" in why


@requires_postgres
def test_pg_a_success_recorded_by_one_worker_is_visible_to_every_other(pg_iid):
    """Shared state, not process memory: the close must be readable from a
    different connection immediately."""
    circuit.record_failure(pg_iid, _auth_fail())
    assert _pg_row(pg_iid)["state"] == "open"
    circuit.record_success(pg_iid)
    seen = _run_workers(4, lambda _: circuit.allows(pg_iid))
    assert [r[0] for r in seen] == [True] * 4
    assert _pg_row(pg_iid)["failure_count"] == 0


@requires_postgres
def test_pg_window_expiry_is_evaluated_by_the_database_not_by_one_worker(pg_iid):
    """The window reset happens inside the UPDATE, so it is the same decision
    for every worker rather than each one's local read."""
    window_s = circuit._setting_int("ai_circuit_window_s", circuit.DEFAULT_WINDOW_S)
    circuit.record_failure(pg_iid, _fail())
    circuit.record_failure(pg_iid, _fail())
    assert _pg_row(pg_iid)["failure_count"] == 2
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_circuit_state SET window_started_at=?"
                 " WHERE provider_instance_id=?", (_iso(-(window_s + 30)), pg_iid))
    conn.commit()
    conn.close()
    results = _run_workers(3, lambda _: circuit.record_failure(pg_iid, _fail()))
    assert all(not isinstance(r, Exception) for r in results), results
    # all three fall in the same fresh window: 3, never 5 (which would trip)
    assert _pg_row(pg_iid)["failure_count"] == 3
    assert _pg_row(pg_iid)["state"] == "closed"
