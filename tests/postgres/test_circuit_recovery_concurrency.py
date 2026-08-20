"""The recovery event must be emitted by the worker that ACTUALLY recovers.

WHY THIS FILE EXISTS SEPARATELY FROM tests/test_ai_circuit.py
-------------------------------------------------------------
The SQLite suite cannot prove this. SQLite permits one writer at a time, so the
interleaving that produces a duplicate — two workers both reading `half_open`
before either writes — simply cannot occur there, and a test written against
that backend passes whether the code is right or wrong.

PostgreSQL is where the application actually runs, with several Uvicorn workers
against real concurrent connections. That is where the race exists, so this is
where it is tested.

THE DEFECT THIS PINS
--------------------
`record_success()` reads the current state to NAME the transition. Gating the
event on that read is a stale pre-read: two workers whose probes both succeed
while the circuit is half_open each see `half_open`, and each logs a recovery
that happened once. An operator watching for "provider recovered" would see the
provider recover N times for one recovery — and, worse, the same pattern in an
alerting rule counts flapping that is not happening.

The event is gated on the conditional UPDATE's `rowcount` instead. Only one
worker can change a row from not-closed to closed, so only one can emit.
"""
import threading

import pytest

from app.services.ai import circuit, errors as ai_errors


@pytest.fixture
def iid(conn):
    conn.execute(
        "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
        " enabled, trust_class, config, secret_enc)"
        " VALUES (?,?,?,?,?,?,?)",
        ("circ-conc", "openai_compatible", "Circuit", True, "public", "{}", ""))
    conn.commit()
    return "circ-conc"


@pytest.fixture
def events(monkeypatch):
    """Capture at the applog sink — no log database involved."""
    seen, lock = [], threading.Lock()
    from app.services import applog

    def capture(category, event_name, level="info", message="", **fields):
        with lock:
            seen.append(event_name)
        return "captured"

    monkeypatch.setattr(applog, "record", capture)
    return seen


def _half_open(iid):
    """Drive the circuit to a genuine half_open state."""
    threshold = circuit._setting_int("ai_circuit_threshold",
                                     circuit.DEFAULT_THRESHOLD)
    for _ in range(threshold):
        circuit.record_failure(
            iid, ai_errors.AIError(code="server_error", provider_detail="boom"))
    assert circuit.snapshot(iid)[0]["state"] == "open"

    from app.db.connection import get_db_connection
    c = get_db_connection()
    try:
        changed = c.execute(
            "UPDATE ai_circuit_state SET cooldown_until = ?"
            " WHERE provider_instance_id = ?",
            (circuit._now_iso(-3600), iid)).rowcount
        c.commit()
        assert changed == 1, "no circuit row to age"
    finally:
        c.close()

    assert circuit.allows(iid)[0]
    assert circuit.snapshot(iid)[0]["state"] == "half_open"


@pytest.mark.parametrize("workers", [8])
def test_concurrent_recoveries_emit_exactly_one_closed_event(iid, events, workers):
    """Every worker's probe succeeds at once. One recovery, one event."""
    _half_open(iid)
    events.clear()

    barrier = threading.Barrier(workers, timeout=15)
    errors = []

    def worker():
        try:
            barrier.wait()
            circuit.record_success(iid)
        except Exception as exc:               # noqa: BLE001 — reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert circuit.snapshot(iid)[0]["state"] == "closed"
    closed = [e for e in events if e == "llm.circuit.closed"]
    assert closed == ["llm.circuit.closed"], events


def test_concurrent_probe_attempts_emit_exactly_one_half_open_event(iid, events):
    """The same property on the other transition. Here the probe LEASE is the
    proof: several workers find the cooldown elapsed simultaneously and race the
    conditional UPDATE, but only its winner may log."""
    threshold = circuit._setting_int("ai_circuit_threshold",
                                     circuit.DEFAULT_THRESHOLD)
    for _ in range(threshold):
        circuit.record_failure(
            iid, ai_errors.AIError(code="server_error", provider_detail="boom"))
    from app.db.connection import get_db_connection
    c = get_db_connection()
    try:
        c.execute("UPDATE ai_circuit_state SET cooldown_until = ?"
                  " WHERE provider_instance_id = ?",
                  (circuit._now_iso(-3600), iid))
        c.commit()
    finally:
        c.close()

    events.clear()
    barrier = threading.Barrier(8, timeout=15)
    allowed = []

    def worker():
        barrier.wait()
        allowed.append(circuit.allows(iid)[0])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sum(1 for a in allowed if a) == 1, "more than one probe was let through"
    half = [e for e in events if e == "llm.circuit.half_open"]
    assert half == ["llm.circuit.half_open"], events


def test_a_success_that_changes_nothing_still_clears_the_failure_window(iid,
                                                                       events):
    """The no-transition branch is not a no-op: it must still reset the counter
    and stamp last_success_at, or failures from an old window would survive a
    recovery and trip the circuit on a healthy provider."""
    circuit.record_success(iid)
    circuit.record_failure(
        iid, ai_errors.AIError(code="server_error", provider_detail="boom"))
    assert circuit.snapshot(iid)[0]["failure_count"] == 1

    events.clear()
    circuit.record_success(iid)
    row = circuit.snapshot(iid)[0]
    assert row["state"] == "closed"
    assert row["failure_count"] == 0
    assert row["last_success_at"]
    assert [e for e in events if e.startswith("llm.circuit.")] == []
