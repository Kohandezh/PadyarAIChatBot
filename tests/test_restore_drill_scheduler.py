"""The nightly backup also runs a restore drill, and the drill can not hurt it.

What is asserted here, without pg_dump, pg_restore or a server:

  * a scheduled backup that verified is drilled once, BEFORE prune, so the
    backup being drilled is not pruned under it
  * a manual create from the panel never drills
  * a backup that did not verify is never drilled
  * a drill that raises (any reason) does not change the return value, the
    outcome counter or the prune
"""
import pytest

BACKUP_ID = "pg_20260914_030000_ab12cd"


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()


@pytest.fixture
def run(db, monkeypatch):
    """A PostgreSQL scheduler run with every step recorded in `calls`."""
    from app.services import backup, pg_backup, restore_drill
    monkeypatch.setattr(backup, "_backup_engine", lambda: "postgres")
    calls = []

    monkeypatch.setattr(pg_backup, "create", lambda actor="", reason="": (
        calls.append(("create", reason)) or {"backup_id": BACKUP_ID}))
    monkeypatch.setattr(pg_backup, "verify", lambda backup_id, actor="", offsite=True: (
        calls.append(("verify", backup_id)) or
        {"backup_id": backup_id, "verification": {"status": "verified"}}))
    monkeypatch.setattr(pg_backup, "prune", lambda **kw: calls.append(("prune",)) or [])
    monkeypatch.setattr(pg_backup, "backup_dir", lambda backup_id: "/somewhere/" + backup_id)
    monkeypatch.setattr(restore_drill, "run", lambda backup_id, actor="system": (
        calls.append(("drill", backup_id, actor)) or {"status": "passed"}))
    return calls


def _count(result):
    from app.services import metrics
    return int(metrics.backup_outcome_total.labels(result=result)._value.get())


def test_a_verified_scheduled_backup_is_drilled_once_before_prune(run):
    from app.services import backup

    path = backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert [c[0] for c in run] == ["create", "verify", "drill", "prune"]
    assert ("drill", BACKUP_ID, "scheduler") in run
    assert path == "/somewhere/" + BACKUP_ID


def test_a_manual_create_never_drills(run):
    from app.services import backup

    backup._run_backup_now(actor="admin", kind="manual")

    assert [c[0] for c in run] == ["create", "verify", "prune"]


def test_a_backup_that_failed_verification_is_not_drilled(run, monkeypatch):
    from app.services import backup, pg_backup
    monkeypatch.setattr(pg_backup, "verify", lambda backup_id, actor="", offsite=True: (
        run.append(("verify", backup_id)) or
        {"backup_id": backup_id, "verification": {"status": "failed"}}))

    backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert "drill" not in [c[0] for c in run]
    assert run[-1] == ("prune",)


def test_a_verify_that_raises_is_not_drilled(run, monkeypatch):
    from app.services import backup, pg_backup

    def boom(backup_id, actor="", offsite=True):
        raise RuntimeError("pg_restore missing")

    monkeypatch.setattr(pg_backup, "verify", boom)

    backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert "drill" not in [c[0] for c in run]


@pytest.mark.parametrize("error", [
    "already",   # a manual drill holds the lock
    RuntimeError("boom"),
    ValueError("bad"),
])
def test_a_drill_that_raises_changes_nothing_else(run, monkeypatch, error):
    from app.services import backup, restore_drill
    if error == "already":
        error = restore_drill.DrillAlreadyRunning("busy")

    def boom(backup_id, actor="system"):
        run.append(("drill", backup_id, actor))
        raise error

    monkeypatch.setattr(restore_drill, "run", boom)
    success, failed = _count("success"), _count("failed")

    path = backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert path == "/somewhere/" + BACKUP_ID
    assert [c[0] for c in run] == ["create", "verify", "drill", "prune"]
    assert (_count("success"), _count("failed")) == (success + 1, failed)


@pytest.mark.parametrize("status", ["passed", "failed", "skipped"])
def test_a_drill_result_never_changes_the_outcome_counter(run, monkeypatch, status):
    from app.services import backup, restore_drill
    monkeypatch.setattr(restore_drill, "run",
                        lambda backup_id, actor="system": {"status": status})
    success, failed = _count("success"), _count("failed")

    backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert (_count("success"), _count("failed")) == (success + 1, failed)
