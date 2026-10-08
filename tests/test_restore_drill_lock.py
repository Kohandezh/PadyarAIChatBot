"""The restore drill fails closed when its lock can not be taken.

The contract is one drill at a time per install. When the lock connection or
the lock query fails, nobody knows whether another drill is running. Running
anyway could start two restores into the same drill database. So the drill must
not run: nothing is restored and nothing is cleaned. It records "skipped".

No server is used. The lock connection is replaced, and every other way into
the database (a second connection, pg_restore) fails the test loudly.
"""
import json
import threading

import pytest

from app.services import pg_backup, restore_drill

BACKUP_ID = "pg_20260830_120000_ab12cd"
LOCK_NAME = "padyar-restore-drill-lock"


@pytest.fixture
def backup(tmp_path, monkeypatch):
    """A well-formed backup folder: a small manifest and a non-empty dump."""
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    folder = tmp_path / "pg" / BACKUP_ID
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"backup_id": BACKUP_ID, "row_counts": {"app.admins": 1}}),
        encoding="utf-8")
    (folder / "padyar.dump").write_bytes(b"not a real dump")
    return BACKUP_ID


@pytest.fixture
def no_restore(monkeypatch):
    """Fail the test if a restore (or any external tool) is started."""
    def boom(*args, **kwargs):
        raise AssertionError("pg_backup._run must not be called")
    monkeypatch.setattr(pg_backup, "_run", boom)


@pytest.fixture
def parts(monkeypatch):
    fake = {"host": "h", "port": "5432", "user": "u", "password": "",
            "dbname": "padyar_x"}
    monkeypatch.setattr(pg_backup, "_conn_parts", lambda: fake)
    return fake


def _break_lock_connection(monkeypatch, error):
    """Only the lock connection fails. Any other connection is a test failure."""
    opened = []

    def connect(parts, application_name, options):
        opened.append(application_name)
        if application_name == LOCK_NAME:
            raise error
        raise AssertionError(f"unexpected connection: {application_name}")

    monkeypatch.setattr(pg_backup, "_connect", connect)
    return opened


class _FailingQueryConnection:
    """The connection opens, but the lock query raises."""
    closed = False

    def execute(self, *args, **kwargs):
        raise OSError("server closed the connection")

    def close(self):
        self.closed = True


def _break_lock_query(monkeypatch):
    opened = []
    conn = _FailingQueryConnection()

    def connect(parts, application_name, options):
        opened.append(application_name)
        if application_name == LOCK_NAME:
            return conn
        raise AssertionError(f"unexpected connection: {application_name}")

    monkeypatch.setattr(pg_backup, "_connect", connect)
    return opened, conn


def _manifest(backup_id):
    with open(pg_backup._manifest_path(backup_id), encoding="utf-8") as f:
        return json.load(f)


def _threads():
    return [t for t in threading.enumerate() if t.name == "restore-drill"]


# ── run(): the nightly path ─────────────────────────────────────────────

@pytest.mark.parametrize("error", [OSError("refused"),
                                   __import__("psycopg").OperationalError("down")])
def test_run_skips_when_the_lock_connection_fails(
        backup, parts, no_restore, monkeypatch, error):
    opened = _break_lock_connection(monkeypatch, error)

    block = restore_drill.run(backup, actor="pytest")

    assert block["status"] == "skipped"
    assert block["reason"] == restore_drill.LOCK_FAILED_REASON_FA
    assert "تمرین انجام نشد" in block["reason"]
    assert opened == [LOCK_NAME], "no other connection may be opened"
    assert _manifest(backup)["drill"]["status"] == "skipped"
    assert _manifest(backup)["drill"]["reason"] == block["reason"]


def test_run_skips_when_the_lock_query_fails_and_closes_the_connection(
        backup, parts, no_restore, monkeypatch):
    opened, conn = _break_lock_query(monkeypatch)

    block = restore_drill.run(backup, actor="pytest")

    assert block["status"] == "skipped"
    assert opened == [LOCK_NAME]
    assert conn.closed is True
    assert _manifest(backup)["drill"]["status"] == "skipped"


def test_a_skipped_lock_drill_publishes_ok_zero(
        backup, parts, no_restore, monkeypatch):
    from app.services import metrics
    _break_lock_connection(monkeypatch, OSError("refused"))
    metrics.backup_drill_last_ok.set(1)

    restore_drill.run(backup, actor="pytest")

    assert metrics.backup_drill_last_ok._value.get() == 0


# ── start(): the manual button ──────────────────────────────────────────

def test_start_raises_records_the_skip_and_starts_no_thread(
        backup, parts, no_restore, monkeypatch):
    opened = _break_lock_connection(monkeypatch, OSError("refused"))
    before = len(_threads())

    with pytest.raises(restore_drill.DrillLockUnavailable):
        restore_drill.start(backup, "pytest")

    assert len(_threads()) == before
    assert opened == [LOCK_NAME]
    block = _manifest(backup)["drill"]
    assert block["status"] == "skipped"
    assert block["reason"] == restore_drill.LOCK_FAILED_REASON_FA
    assert block["actor"] == "pytest"


def test_start_also_fails_closed_when_the_lock_query_fails(
        backup, parts, no_restore, monkeypatch):
    _break_lock_query(monkeypatch)
    with pytest.raises(restore_drill.DrillLockUnavailable):
        restore_drill.start(backup, "pytest")
    assert _manifest(backup)["drill"]["status"] == "skipped"


# ── What stays the same ─────────────────────────────────────────────────

def test_lock_unavailable_is_not_the_already_running_error():
    assert not issubclass(restore_drill.DrillLockUnavailable,
                          restore_drill.DrillAlreadyRunning)
    assert not issubclass(restore_drill.DrillAlreadyRunning,
                          restore_drill.DrillLockUnavailable)


def test_a_held_lock_is_still_already_running_and_writes_nothing(
        backup, parts, no_restore, monkeypatch):
    class Held:
        closed = False

        def execute(self, *args, **kwargs):
            return self

        def fetchone(self):
            return (False,)

        def close(self):
            self.closed = True

    monkeypatch.setattr(pg_backup, "_connect", lambda *a: Held())
    with pytest.raises(restore_drill.DrillAlreadyRunning):
        restore_drill.run(backup, actor="pytest")
    with pytest.raises(restore_drill.DrillAlreadyRunning):
        restore_drill.start(backup, "pytest")
    assert "drill" not in _manifest(backup)


def test_a_bad_id_is_still_a_backup_error_and_takes_no_lock(
        parts, no_restore, monkeypatch):
    opened = _break_lock_connection(monkeypatch, OSError("refused"))
    with pytest.raises(pg_backup.BackupError):
        restore_drill.run("pg_bad", "pytest")
    with pytest.raises(pg_backup.BackupError):
        restore_drill.start("pg_bad", "pytest")
    assert opened == []
