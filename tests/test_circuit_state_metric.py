"""ai_circuit_state is published from the database when the app starts.

THE DEFECT THIS FILE PINS. The gauge was set only on a circuit TRANSITION.
After a restart no worker had a series, so a circuit stored as `open` in the
`ai_circuit_state` table was invisible to Prometheus until its next
transition, and an "AI circuit open" alert stayed silent for exactly the
provider that was down (docs/features/monitoring-stack/SPEC.md DEP-3, SC-016).

The published value mirrors the stored row, never a guess: an `open` row
whose cooldown has passed is still `open` until a request moves it. An
unknown state is skipped and logged with its instance id. A database error
at start is logged and never stops the app from booting.

Each test uses its own random instance ids, because the single-process gauge
lives for the whole pytest run and a fixed id could see a value another test
set. Series are read through collect(), since `.labels()` would create the
series the absence tests check for.
"""
import datetime
import logging
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.services import metrics
from app.services.ai import circuit, store


@pytest.fixture
def ai_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "circuit-start.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    from app.db.connection import init_db
    init_db()
    store.ensure_ai_tables()
    store._invalidate_runtime()
    yield
    store._invalidate_runtime()


def _iid(tag):
    return f"{tag}-{secrets.token_hex(4)}"


def _iso(offset_s=0):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=offset_s)
    return t.isoformat(timespec="seconds")


def _insert(iid, state, cooldown_until=None):
    """Write a stored circuit row directly, as a previous run of the app left it.

    The CHECK constraint is lifted only for this one connection, so a test can
    store a state string the schema would refuse (an old or corrupted row).
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO ai_circuit_state (provider_instance_id, state, cooldown_until)"
            " VALUES (?, ?, ?)", (iid, state, cooldown_until))
        conn.commit()
    finally:
        conn.close()


def _ours(caplog):
    """Log records written by the circuit module. Its logger is the app-wide
    one, so other startup lines share the name; the file tells them apart."""
    return [r for r in caplog.records if r.filename == "circuit.py"]


def _series(iid):
    """The gauge value for one instance, or None when no series exists."""
    for family in metrics.ai_circuit_state.collect():
        for sample in family.samples:
            if sample.labels == {"instance": iid}:
                return sample.value
    return None


# ── AC1: every stored state is published with the module's constants ──


@pytest.mark.parametrize("state,value", [
    ("open", metrics.CIRCUIT_OPEN),
    ("half_open", metrics.CIRCUIT_HALF_OPEN),
    ("closed", metrics.CIRCUIT_CLOSED),
])
def test_a_stored_state_is_published_as_its_number(ai_db, state, value):
    iid = _iid(state)
    _insert(iid, state)
    assert _series(iid) is None

    circuit.publish_stored_states()

    assert _series(iid) == value


def test_every_row_is_published_not_only_the_first(ai_db):
    opened, closed = _iid("many-open"), _iid("many-closed")
    _insert(opened, "open")
    _insert(closed, "closed")

    circuit.publish_stored_states()

    assert _series(opened) == 2
    assert _series(closed) == 0


# ── AC2: the value mirrors the database exactly ────────────────────────


def test_an_open_row_whose_cooldown_passed_is_still_published_as_open(ai_db):
    """The row stays `open` until a request moves it to half_open. Publishing
    1 here would be a guess about a transition that has not happened."""
    iid = _iid("stale-open")
    _insert(iid, "open", cooldown_until=_iso(-3600))

    circuit.publish_stored_states()

    assert _series(iid) == metrics.CIRCUIT_OPEN


def test_publishing_never_changes_the_stored_row(ai_db):
    iid = _iid("read-only")
    _insert(iid, "open", cooldown_until=_iso(-3600))
    before = circuit.snapshot(iid)

    circuit.publish_stored_states()

    assert circuit.snapshot(iid) == before


@pytest.mark.parametrize("bad_state", ["", "tripped", "OPEN"])
def test_an_unknown_state_is_skipped_and_logged_with_its_instance(ai_db, caplog, bad_state):
    bad, good = _iid("unknown"), _iid("valid")
    _insert(bad, bad_state)
    _insert(good, "open")

    with caplog.at_level(logging.WARNING, logger=circuit.logger.name):
        circuit.publish_stored_states()

    assert _series(bad) is None, "an unknown state must never be guessed into a number"
    assert _series(good) == 2, "one bad row must not stop the valid rows"
    assert any(bad in r.getMessage() for r in _ours(caplog)), (
        "the skipped row's log line must name its provider instance id")


# ── AC3: no row, no series ─────────────────────────────────────────────


def test_no_row_means_no_series(ai_db):
    before = [s for f in metrics.ai_circuit_state.collect() for s in f.samples]

    circuit.publish_stored_states()

    after = [s for f in metrics.ai_circuit_state.collect() for s in f.samples]
    assert after == before


# ── AC4: a database error never fails startup ──────────────────────────


def test_a_missing_table_is_logged_and_does_not_raise(tmp_path, monkeypatch, caplog):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "no-ai-tables.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()

    with caplog.at_level(logging.WARNING, logger=circuit.logger.name):
        circuit.publish_stored_states()

    assert len(_ours(caplog)) == 1


def test_a_database_that_is_down_does_not_raise(ai_db, monkeypatch, caplog):
    def down(*_a, **_kw):
        raise sqlite3.OperationalError("database is down")

    monkeypatch.setattr(circuit, "snapshot", down)
    with caplog.at_level(logging.WARNING, logger=circuit.logger.name):
        circuit.publish_stored_states()

    assert len(_ours(caplog)) == 1


def test_the_app_publishes_the_stored_state_when_it_starts(ai_db):
    iid = _iid("at-start")
    _insert(iid, "open")
    from app.main import app

    with TestClient(app) as c:
        assert _series(iid) == 2, "the lifespan must publish before any request"
        assert c.get("/api/health").status_code == 200


def test_the_app_still_starts_when_the_circuit_read_fails(ai_db, monkeypatch):
    iid = _iid("db-error")
    _insert(iid, "open")

    def down(*_a, **_kw):
        raise sqlite3.OperationalError("database is down")

    monkeypatch.setattr(circuit, "snapshot", down)
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
    assert _series(iid) is None


# ── AC5: the breaker itself is unchanged ───────────────────────────────


def test_a_later_transition_still_overwrites_the_published_value(ai_db):
    iid = _iid("then-reset")
    _insert(iid, "open")
    circuit.publish_stored_states()
    assert _series(iid) == 2

    circuit.reset(iid)

    assert _series(iid) == 0
    assert circuit.snapshot(iid)[0]["state"] == "closed"
