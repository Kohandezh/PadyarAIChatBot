"""The watchdog during a database outage: it still texts, and it stays fast.

WHAT BROKE
----------
The watchdog's settings reader went through get_setting(), which never raises:
on a database error it returns the default. So during a PostgreSQL outage the
reader returned an EMPTY phone instead of failing. run_cycle's fallback to the
cached phone runs only on an exception, so it never ran, and the cycle then
saved cached_phone = "". Measured with PostgreSQL pointed at a closed port:
0 SMS, not the down-SMS and not HostPostgresDown, and the cache wiped.

test_settings_db_down_falls_back_to_cached_phone in test_watchdog_io.py passed
all along, because it injects a reader that raises. These tests run the REAL
reader against a database that cannot be reached.

The second half is time. Each settings read inside app/services/sms.py waited
DB_CONNECT_TIMEOUT for the pool, about 110 s per SMS at the production value.
Once the watchdog's one strict read fails it turns on pg.set_unavailable(),
and the rest of the cycle never waits for the database again. The credentials
still come from the .env fallback inside sms.py, exactly as before.

The cached threshold follows the cached phone: a cycle that cannot read the
database uses the last threshold read with success, and the 300000 default
only when none was ever read.
"""
import importlib.util
import json
import pathlib
import time

import pytest

WATCHDOG = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "watchdog" / "watchdog.py"
spec = importlib.util.spec_from_file_location("watchdog", WATCHDOG)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

INSTALL = "myevent"
PHONE = "+989121234567"
OTHER_PHONE = "+989351112233"
NOW = 1_800_000_000.0
POSTGRES_DOWN = [
    {"labels": {"alertname": "MonitoringHeartbeat"}, "status": {"state": "active"},
     "fingerprint": "heartbeat"},
    {"labels": {"alertname": "HostPostgresDown", "page": "sms"},
     "status": {"state": "active"}, "fingerprint": "pg-down"},
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from app.db import pg

    monkeypatch.setenv("APP_PORT", "8010")
    monkeypatch.setattr(pg, "_pool", None)
    monkeypatch.setattr(pg, "_unavailable", False)
    yield
    pg.close_pool()


@pytest.fixture
def db_up(tmp_path, monkeypatch):
    import app.config as config
    from app.db.connection import init_db

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    init_db()
    yield


@pytest.fixture
def db_down(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "no-such-dir" / "app.db"))
    yield


@pytest.fixture
def postgres_down(monkeypatch):
    """PostgreSQL at a port nothing listens on, with a 1 s pool timeout."""
    import app.config as config

    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://padyar:x@127.0.0.1:1/padyar")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "1")
    yield


def _state_file(tmp_path, **fields):
    state = {"fail_count": 2, "down_since": NOW - 120, "last_alert": 0.0,
             "credit_day": "", "credit_alerted": False}
    state.update(fields)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def _cycle(state_path, sent, **overrides):
    """One cycle with the REAL settings reader; the app is down, this
    install owns the host alerts, and HostPostgresDown is firing."""
    kwargs = dict(
        now=NOW,
        probe=lambda port: False,
        sender=lambda phone, text: sent.append((phone, text)),
        credit_reader=lambda: None,
        state_path=state_path,
        alerts_reader=lambda: POSTGRES_DOWN,
        owner_reader=lambda: INSTALL,
        silences_reader=lambda: [],
    )
    kwargs.update(overrides)
    return watchdog.run_cycle(INSTALL, **kwargs)


def _disk(state_path):
    return json.loads(state_path.read_text(encoding="utf-8"))


# ── The real reader ─────────────────────────────────────────────────────

def test_the_real_reader_fails_when_the_database_cannot_be_reached(db_down):
    from app.db import pg

    with pytest.raises(Exception):
        watchdog._read_settings()
    assert pg._unavailable is True, "a failed read must stop later waits this cycle"


def test_the_real_reader_returns_an_empty_phone_only_from_a_database_that_answered(db_up):
    from app.db import pg

    assert watchdog._read_settings() == ("", "300000")
    assert pg._unavailable is False


def test_the_real_reader_returns_the_stored_settings(db_up):
    from app.db.queries import set_setting

    set_setting("alert_critical_phone", PHONE)
    set_setting("alert_credit_threshold_toman", "250000")

    assert watchdog._read_settings() == (PHONE, "250000")


# ── A cycle while the database is down ──────────────────────────────────

def test_a_db_down_cycle_still_sends_the_down_sms_and_the_postgres_alert(db_down, tmp_path):
    state_path = _state_file(tmp_path, cached_phone=PHONE, cached_threshold="250000")
    sent = []

    _cycle(state_path, sent)

    assert [phone for phone, _ in sent] == [PHONE, PHONE]
    assert "پاسخ نمی‌دهد" in sent[0][1]
    assert watchdog.ALERT_LABELS["HostPostgresDown"] in sent[1][1]
    disk = _disk(state_path)
    assert disk["cached_phone"] == PHONE, "a failed read wiped the cached phone"
    assert disk["cached_threshold"] == "250000", "a failed read wiped the cached threshold"


def test_a_db_down_cycle_uses_the_cached_threshold_not_the_default(db_down, tmp_path):
    # 400 000 toman is above the 300 000 default and below the cached 500 000,
    # so only the cached value can trigger the low-credit SMS.
    state_path = _state_file(tmp_path, fail_count=0, down_since=0.0,
                             cached_phone=PHONE, cached_threshold="500000")
    sent = []

    _cycle(state_path, sent, probe=lambda port: True,
           credit_reader=lambda: 4_000_000, alerts_reader=lambda: [])

    assert len(sent) == 1
    assert "500٬000" in sent[0][1] and "400٬000" in sent[0][1]


def test_a_db_down_cycle_with_no_cached_phone_says_so_and_does_not_crash(db_down, tmp_path, capsys):
    state_path = _state_file(tmp_path)
    sent = []

    result = _cycle(state_path, sent)

    assert isinstance(result, dict) and sent == []
    out = capsys.readouterr().out
    assert "DOWN but no alert_critical_phone configured" in out
    assert _disk(state_path)["fail_count"] == 3


@pytest.mark.parametrize("cached", ["500000", ""])
def test_a_cycle_without_the_app_leaves_the_cached_threshold_alone(tmp_path, monkeypatch, cached):
    """No app means no settings read at all, so neither cache may move.
    "" is the never-read value; turning it into "300000" would look like a
    read that happened."""
    import sys

    monkeypatch.setitem(sys.modules, "app", None)
    state_path = _state_file(tmp_path, cached_phone=PHONE, cached_threshold=cached)

    _cycle(state_path, [])

    disk = _disk(state_path)
    assert disk["cached_threshold"] == cached
    assert disk["cached_phone"] == PHONE


# ── Allow-controls: the database is up ──────────────────────────────────

def test_a_db_up_cycle_texts_the_phone_in_the_database_and_caches_it(db_up, tmp_path):
    from app.db import pg
    from app.db.queries import set_setting

    set_setting("alert_critical_phone", OTHER_PHONE)
    set_setting("alert_credit_threshold_toman", "700000")
    state_path = _state_file(tmp_path, cached_phone=PHONE, cached_threshold="250000")
    sent = []

    _cycle(state_path, sent)

    assert [phone for phone, _ in sent] == [OTHER_PHONE, OTHER_PHONE]
    disk = _disk(state_path)
    assert disk["cached_phone"] == OTHER_PHONE
    assert disk["cached_threshold"] == "700000"
    assert pg._unavailable is False


def test_an_operator_who_clears_the_phone_stops_the_sms(db_up, tmp_path, capsys):
    from app.db.queries import set_setting

    set_setting("alert_critical_phone", "")
    state_path = _state_file(tmp_path, cached_phone=PHONE)
    sent = []

    _cycle(state_path, sent)

    assert sent == [], "an empty phone read from a live database means alerts are off"
    assert _disk(state_path)["cached_phone"] == ""
    assert "no alert_critical_phone configured" in capsys.readouterr().out


# ── End to end against PostgreSQL that does not answer ──────────────────

def test_postgres_down_cycle_waits_once_and_sends_both_sms_with_env_credentials(
        postgres_down, tmp_path, monkeypatch):
    from app.db import pg
    from app.services import sms

    monkeypatch.setenv("ASANAK_USERNAME", "env-user")
    monkeypatch.setenv("ASANAK_PASSWORD", "env-pass")
    monkeypatch.setenv("ASANAK_SOURCE", "3000")
    monkeypatch.delenv("SMS_DAILY_BUDGET", raising=False)
    monkeypatch.delenv("ASANAK_TEMPLATE_ID", raising=False)
    posts = []

    def gateway(url, payload):
        posts.append(dict(payload))
        return 200, '{"meta": {"status": 200}, "data": ["4711"]}'

    monkeypatch.setattr(sms, "_http_post", gateway)

    checkouts = []

    class CountingPool(pg.ConnectionPool):
        def connection(self, *args, **kwargs):
            checkouts.append(1)
            return super().connection(*args, **kwargs)

    monkeypatch.setattr(pg, "ConnectionPool", CountingPool)
    state_path = _state_file(tmp_path, cached_phone=PHONE)

    started = time.monotonic()
    _cycle(state_path, None, sender=None)
    elapsed = time.monotonic() - started

    assert len(checkouts) == 1, f"{len(checkouts)} pool waits in one DB-down cycle"
    assert [p["destination"] for p in posts] == ["09121234567", "09121234567"]
    assert {(p["username"], p["password"], p["source"]) for p in posts} == {
        ("env-user", "env-pass", "3000")}
    assert elapsed < 1 + 5, f"a DB-down cycle took {elapsed:.1f}s"
    assert _disk(state_path)["cached_phone"] == PHONE
