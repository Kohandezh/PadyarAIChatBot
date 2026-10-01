"""The backup schedule must be visible to Prometheus, or the stale alert lies.

THE DEFECT THIS FILE PINS. The planned "backup is stale" alert compares the
age of the last verified backup with the backup schedule. The schedule lives
in the `settings` table (`backup_auto_enabled`, `backup_interval_hours`),
which Prometheus cannot read. So the alert could not tell "automatic backups
are off on purpose" or "this install backs up every 48 hours" from "backups
stopped", and it would page falsely on such an install.

The contract now:

  * `backup_schedule_interval_seconds` = `backup_interval_hours * 3600` when
    the scheduler would run automatic backups, `0` when it would not.
    "Would run" is the scheduler's own test (get_schedule()["enabled"]), so
    the gauge can never disagree with the scheduler.
  * A missing setting row uses backup.DEFAULTS (24 hours, so 86400).
  * The scheduler loop sets it once when it starts (every worker, every
    start) and again on every check, so an admin's change reaches every
    worker within CHECK_EVERY_SECONDS.
  * A settings read that fails keeps the last value and logs. It never
    crashes the loop and never writes a guessed value.

The loop is driven by a fake asyncio.sleep, so no test waits 60 seconds and
no real backup runs.
"""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient


def _gauge():
    from app.services import metrics
    family = next(iter(metrics.backup_schedule_interval_seconds.collect()))
    return family.samples[0].value


def _exposed(text, name):
    from prometheus_client.parser import text_string_to_metric_families
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name == name:
                return s.value
    return None


def _run_loop(monkeypatch, on_sleep):
    """Run scheduler_loop until `on_sleep` raises CancelledError.

    `on_sleep(n)` is called on the n-th sleep (1-based) of the loop, which is
    the moment between two checks: the place an admin save would land.
    """
    from app.services import backup
    calls = {"n": 0}

    async def fake_sleep(_seconds):
        calls["n"] += 1
        on_sleep(calls["n"])

    monkeypatch.setattr(backup.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(backup.scheduler_loop())


def _stop():
    raise asyncio.CancelledError


@pytest.fixture(autouse=True)
def _gauge_starts_at_a_marker():
    """The gauge is process-wide. A marker value no rule produces shows when a
    test left it untouched."""
    from app.services import metrics
    gauge = metrics.backup_schedule_interval_seconds
    gauge.set(-1)
    yield
    gauge.set(0)


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "interval.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    from app.services import backup
    monkeypatch.setattr(backup, "_run_backup_now", lambda *a, **k: pytest.fail(
        "the metric tests must never run a backup"))
    yield


def _set(key, value):
    from app.db.queries import set_setting
    set_setting(key, value)


def _publish():
    from app.services import backup
    backup.publish_schedule_metric()


# ── The value rule ─────────────────────────────────────────────────────


def test_an_install_that_never_opened_the_form_shows_the_default_day(app_db):
    _publish()
    assert _gauge() == 86400


def test_automatic_backups_switched_off_read_as_zero(app_db):
    _set("backup_auto_enabled", "false")
    _set("backup_interval_hours", "24")
    _publish()
    assert _gauge() == 0


def test_automatic_backups_switched_on_read_as_the_interval(app_db):
    _set("backup_auto_enabled", "true")
    _set("backup_interval_hours", "24")
    _publish()
    assert _gauge() == 86400


@pytest.mark.parametrize("hours,seconds", [("48", 172800), ("24", 86400),
                                           ("6", 21600)])
def test_the_value_is_the_interval_in_seconds(app_db, hours, seconds):
    _set("backup_auto_enabled", "true")
    _set("backup_interval_hours", hours)
    _publish()
    assert _gauge() == seconds


@pytest.mark.parametrize("stored", ["True", "TRUE", "yes", "1", ""])
def test_enabled_means_exactly_what_the_scheduler_treats_as_enabled(
        app_db, stored):
    """The scheduler runs a backup only when the row is the string "true".
    Anything else means it never runs one, so the gauge must say 0 too, or
    the stale alert would wait for a backup that is never coming."""
    from app.services import backup
    _set("backup_auto_enabled", stored)
    assert backup.get_schedule()["enabled"] is False
    _publish()
    assert _gauge() == 0


# ── A failed read keeps the last good value ────────────────────────────


def test_a_successful_read_updates_the_value(app_db):
    _set("backup_interval_hours", "48")
    _publish()
    assert _gauge() == 172800
    _set("backup_interval_hours", "12")
    _publish()
    assert _gauge() == 43200


def test_a_database_error_keeps_the_previous_value_and_logs(
        app_db, monkeypatch, caplog):
    from app.services import backup
    _set("backup_interval_hours", "48")
    _publish()
    assert _gauge() == 172800

    def db_down():
        raise ConnectionError("database is down")

    monkeypatch.setattr(backup, "get_db_connection", db_down)
    with caplog.at_level(logging.WARNING):
        _publish()

    assert _gauge() == 172800, "a failed read must not write a guessed value"
    assert any("backup_schedule_interval_seconds" in r.getMessage()
               for r in caplog.records), caplog.text


def test_a_hand_edited_interval_that_is_not_a_number_keeps_the_value(app_db):
    _publish()
    assert _gauge() == 86400
    _set("backup_interval_hours", "every day")
    _publish()
    assert _gauge() == 86400


def test_a_failed_read_before_any_good_read_leaves_the_gauge_untouched(
        app_db, monkeypatch):
    from app.services import backup

    def db_down():
        raise ConnectionError("database is down")

    monkeypatch.setattr(backup, "get_db_connection", db_down)
    _publish()
    assert _gauge() == -1


# ── The scheduler loop publishes it (AC2 start, AC3 every check) ───────


def test_the_loop_publishes_the_schedule_before_its_first_wait(
        app_db, monkeypatch):
    """A restarted worker shows the stored schedule at once, not after the
    first 60 second wait."""
    _set("backup_interval_hours", "48")
    seen = {}

    def on_sleep(n):
        seen["at_first_sleep"] = _gauge()
        _stop()

    _run_loop(monkeypatch, on_sleep)
    assert seen["at_first_sleep"] == 172800


def test_an_admin_change_reaches_the_gauge_on_the_next_check(
        app_db, monkeypatch):
    from app.services import backup
    _set("backup_auto_enabled", "true")
    _set("backup_interval_hours", "24")

    def on_sleep(n):
        if n == 1:
            assert _gauge() == 86400
            backup.save_schedule(False, 24, "03:00")
            return
        _stop()

    _run_loop(monkeypatch, on_sleep)
    assert _gauge() == 0


def test_an_admin_change_of_the_interval_reaches_the_gauge(app_db, monkeypatch):
    from app.services import backup

    def on_sleep(n):
        if n == 1:
            backup.save_schedule(True, 48, "03:00")
            return
        _stop()

    _run_loop(monkeypatch, on_sleep)
    assert _gauge() == 172800


def test_a_failed_read_at_start_does_not_stop_the_loop(app_db, monkeypatch):
    from app.services import backup
    real_get_schedule = backup.get_schedule
    reads = {"n": 0}

    def flaky_get_schedule():
        reads["n"] += 1
        if reads["n"] == 1:
            raise ConnectionError("database is down")
        return real_get_schedule()

    monkeypatch.setattr(backup, "get_schedule", flaky_get_schedule)
    _set("backup_interval_hours", "48")

    def on_sleep(n):
        if n == 1:
            assert _gauge() == -1, "a failed read must leave the value alone"
            return
        _stop()

    _run_loop(monkeypatch, on_sleep)
    assert _gauge() == 172800, "the next good read must update it"


def test_a_failed_read_inside_the_loop_keeps_the_value_and_the_loop_runs_on(
        app_db, monkeypatch):
    from app.services import backup
    real_get_schedule = backup.get_schedule
    _set("backup_interval_hours", "48")

    def down():
        raise ConnectionError("database is down")

    def on_sleep(n):
        if n == 1:
            monkeypatch.setattr(backup, "get_schedule", down)
            return
        if n == 2:
            assert _gauge() == 172800
            monkeypatch.setattr(backup, "get_schedule", real_get_schedule)
            _set("backup_interval_hours", "12")
            return
        _stop()

    _run_loop(monkeypatch, on_sleep)
    assert _gauge() == 43200


def test_a_broken_metric_write_never_stops_a_due_backup(app_db, monkeypatch):
    """Only metrics change here. If writing the gauge fails, the scheduler
    must still decide and run the backup exactly as before."""
    from app.services import backup, metrics
    ran = []
    monkeypatch.setattr(backup, "_run_backup_now", lambda *a, **k: ran.append(1))

    class Broken:
        def set(self, _value):
            raise OSError("metrics directory is full")

    monkeypatch.setattr(metrics, "backup_schedule_interval_seconds", Broken())
    _set("backup_next_run", "2000-01-01T00:00:00")

    def on_sleep(n):
        if n == 1:
            return
        _stop()

    _run_loop(monkeypatch, on_sleep)
    assert ran == [1]


# ── The app start and /metrics ─────────────────────────────────────────


def test_the_app_start_publishes_the_schedule_on_metrics(app_db, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "METRICS_TOKEN", "t-interval")
    _set("backup_interval_hours", "48")
    from app.main import app

    with TestClient(app) as c:
        res = c.get("/metrics", headers={"Authorization": "Bearer t-interval"})

    assert res.status_code == 200
    assert _exposed(res.text, "backup_schedule_interval_seconds") == 172800
