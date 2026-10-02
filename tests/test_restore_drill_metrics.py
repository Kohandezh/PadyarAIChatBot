"""Restore drill metrics: set by the drill, seeded at startup, merged per mode.

  * backup_drill_last_success_timestamp_seconds  (max, never backward)
  * backup_drill_last_duration_seconds           (mostrecent)
  * backup_drill_last_ok                         (mostrecent, 1 passed, 0 else)

No server and no pg_restore: _finish() is called with a hand-made block, and
the backups folder is a tmp dir. The multiprocess cases run real subprocesses,
like tests/test_metrics_multiprocess.py, because the mode is chosen at import.
"""
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.services import pg_backup, restore_drill

REPO_ROOT = Path(__file__).resolve().parent.parent
NAMES = ("backup_drill_last_success_timestamp_seconds",
         "backup_drill_last_duration_seconds", "backup_drill_last_ok")


# ── helpers ────────────────────────────────────────────────────────────

def _iso(moment):
    return moment.isoformat(timespec="seconds")


def _ts(iso):
    return datetime.fromisoformat(iso).timestamp()


def _gauge(name):
    from app.services import metrics
    return next(iter(getattr(metrics, name).collect())).samples[0].value


def _block(status="passed", checked_at=None, duration_ms=1500):
    return {"status": status, "checked_at": checked_at,
            "duration_ms": duration_ms, "reason": "r", "actor": "tester",
            "tables_checked": 0, "mismatches": [], "checks": {}}


def _write_manifest(root, backup_id, drill=None, extra=None):
    directory = os.path.join(root, backup_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "manifest.json")
    manifest = {"backup_id": backup_id, **(extra or {})}
    if drill is not None:
        manifest["drill"] = drill
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return path


@pytest.fixture(autouse=True)
def _gauges_start_clean():
    """The gauges are process-wide, so every test starts from "none known"."""
    from app.services import metrics

    def reset():
        metrics.backup_drill_last_success_timestamp_seconds.set(0)
        metrics.backup_drill_last_duration_seconds.set(0)
        metrics.backup_drill_last_ok.set(0)

    reset()
    yield
    reset()


@pytest.fixture
def folder(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    return str(tmp_path / "pg")


def _finish(folder, block, backup_id="pg_20260914_030000_ab12cd"):
    path = _write_manifest(folder, backup_id)
    restore_drill._finish(backup_id, block)
    return path


# ── _finish publishes ──────────────────────────────────────────────────

def test_a_passed_drill_moves_the_time_sets_ok_and_the_duration(folder):
    when = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))

    _finish(folder, _block("passed", when, duration_ms=2500))

    assert _gauge("backup_drill_last_success_timestamp_seconds") == _ts(when)
    assert _gauge("backup_drill_last_ok") == 1
    assert _gauge("backup_drill_last_duration_seconds") == 2.5


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_a_failed_or_skipped_drill_sets_ok_zero_and_keeps_the_time(folder, status):
    from app.services import metrics
    old = _ts("2026-09-01T03:00:00+00:00")
    metrics.backup_drill_last_success_timestamp_seconds.set(old)
    metrics.backup_drill_last_ok.set(1)
    when = _iso(datetime.now(timezone.utc))

    _finish(folder, _block(status, when, duration_ms=800))

    assert _gauge("backup_drill_last_ok") == 0
    assert _gauge("backup_drill_last_success_timestamp_seconds") == old
    assert _gauge("backup_drill_last_duration_seconds") == 0.8


def test_an_older_passed_drill_never_moves_the_time_backward(folder):
    new = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    old = _iso(datetime.now(timezone.utc) - timedelta(days=3))
    _finish(folder, _block("passed", new), "pg_20260914_030000_aaaaaa")

    _finish(folder, _block("passed", old), "pg_20260911_030000_bbbbbb")

    assert _gauge("backup_drill_last_success_timestamp_seconds") == _ts(new)
    assert _gauge("backup_drill_last_ok") == 1


def test_a_checked_at_in_the_future_is_refused(folder):
    future = _iso(datetime.now(timezone.utc) + timedelta(hours=5))

    path = _finish(folder, _block("passed", future))

    assert _gauge("backup_drill_last_success_timestamp_seconds") == 0
    # The drill itself passed, so ok is still 1 and the block is still saved.
    assert _gauge("backup_drill_last_ok") == 1
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["drill"]["status"] == "passed"


@pytest.mark.parametrize("bad", [None, "not a date", 7])
def test_an_unusable_checked_at_does_not_move_the_time(folder, bad):
    _finish(folder, _block("passed", bad))

    assert _gauge("backup_drill_last_success_timestamp_seconds") == 0


def test_a_metric_write_that_raises_does_not_break_finish(folder, monkeypatch):
    from app.services import metrics

    def boom(*a, **k):
        raise RuntimeError("metrics broken")

    when = _iso(datetime.now(timezone.utc))
    # A context, so the real gauges are back before the fixture resets them.
    with monkeypatch.context() as broken:
        broken.setattr(metrics, "set_backup_drill_last_success", boom)
        broken.setattr(metrics.backup_drill_last_ok, "set", boom)
        broken.setattr(metrics.backup_drill_last_duration_seconds, "set", boom)
        path = _finish(folder, _block("passed", when))

    with open(path, encoding="utf-8") as f:
        assert json.load(f)["drill"]["status"] == "passed"


def test_a_drill_already_running_publishes_nothing(folder, monkeypatch):
    def locked():
        raise restore_drill.DrillAlreadyRunning("busy")

    monkeypatch.setattr(restore_drill, "_take_lock", locked)
    path = _write_manifest(folder, "pg_20260914_030000_ab12cd")
    monkeypatch.setattr(pg_backup, "_dump_path", lambda i: path)

    with pytest.raises(restore_drill.DrillAlreadyRunning):
        restore_drill.run("pg_20260914_030000_ab12cd")

    assert _gauge("backup_drill_last_ok") == 0
    assert _gauge("backup_drill_last_duration_seconds") == 0


# ── seed_metrics at startup ────────────────────────────────────────────

def test_seeding_takes_the_newest_pass_and_the_newest_drill_overall(folder):
    _write_manifest(folder, "pg_20260910_030000_aaaaaa",
                    _block("passed", "2026-09-10T03:30:00+00:00", 1000))
    _write_manifest(folder, "pg_20260911_030000_bbbbbb",
                    _block("passed", "2026-09-11T03:30:00+00:00", 2000))
    _write_manifest(folder, "pg_20260912_030000_cccccc",
                    _block("failed", "2026-09-12T03:30:00+00:00", 4000))

    restore_drill.seed_metrics()

    assert _gauge("backup_drill_last_success_timestamp_seconds") == \
        _ts("2026-09-11T03:30:00+00:00")
    assert _gauge("backup_drill_last_ok") == 0
    assert _gauge("backup_drill_last_duration_seconds") == 4.0


def test_seeding_with_the_newest_drill_passed_sets_ok_one(folder):
    _write_manifest(folder, "pg_20260911_030000_bbbbbb",
                    _block("failed", "2026-09-11T03:30:00+00:00", 1000))
    _write_manifest(folder, "pg_20260912_030000_cccccc",
                    _block("passed", "2026-09-12T03:30:00+00:00", 3000))

    restore_drill.seed_metrics()

    assert _gauge("backup_drill_last_ok") == 1
    assert _gauge("backup_drill_last_duration_seconds") == 3.0


def test_seeding_skips_old_manifests_and_junk(folder):
    _write_manifest(folder, "pg_20260901_030000_aaaaaa")  # no drill block
    _write_manifest(folder, "pg_20260902_030000_bbbbbb", "not a dict")
    _write_manifest(folder, "pg_20260903_030000_cccccc",
                    {"status": "passed", "checked_at": "garbage"})
    _write_manifest(folder, "pg_20260904_030000_dddddd",
                    _block("passed", "2026-09-04T03:30:00+00:00", 500))
    junk = os.path.join(folder, "pg_20260905_030000_eeeeee")
    os.makedirs(junk)
    with open(os.path.join(junk, "manifest.json"), "w") as f:
        f.write("{not json")

    restore_drill.seed_metrics()

    assert _gauge("backup_drill_last_success_timestamp_seconds") == \
        _ts("2026-09-04T03:30:00+00:00")


def test_seeding_refuses_a_future_pass(folder):
    future = _iso(datetime.now(timezone.utc) + timedelta(days=2))
    _write_manifest(folder, "pg_20260904_030000_dddddd", _block("passed", future))

    restore_drill.seed_metrics()

    assert _gauge("backup_drill_last_success_timestamp_seconds") == 0


def test_seeding_with_no_backups_folder_is_fine(folder):
    assert not os.path.exists(folder)

    restore_drill.seed_metrics()

    assert _gauge("backup_drill_last_success_timestamp_seconds") == 0
    assert _gauge("backup_drill_last_ok") == 0


def test_seeding_never_raises(folder, monkeypatch):
    def broken():
        raise PermissionError("backups dir unreadable")

    monkeypatch.setattr(pg_backup, "list_backups", broken)

    restore_drill.seed_metrics()

    assert _gauge("backup_drill_last_ok") == 0


def test_app_startup_seeds_the_drill_metrics_from_disk(folder, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "METRICS_TOKEN", "t-drill-metrics")
    _write_manifest(folder, "pg_20260912_030000_bbbbbb",
                    _block("passed", "2026-09-12T03:30:00+00:00", 2000))
    from app.main import app

    with TestClient(app) as c:
        res = c.get("/metrics", headers={"Authorization": "Bearer t-drill-metrics"})

    assert res.status_code == 200
    values = {}
    for family in text_string_to_metric_families(res.text):
        for s in family.samples:
            values[s.name] = s.value
    assert values["backup_drill_last_success_timestamp_seconds"] == \
        _ts("2026-09-12T03:30:00+00:00")
    assert values["backup_drill_last_ok"] == 1


# ── Exposition ─────────────────────────────────────────────────────────

def test_the_three_gauges_are_exposed_as_gauges():
    from app.services import metrics
    text = metrics.exposition().decode()
    for name in NAMES:
        assert f"# TYPE {name} gauge" in text
        assert name in metrics.FAMILY_NAMES


def test_a_fresh_process_exposes_all_three_at_zero():
    env = {k: v for k, v in os.environ.items()
           if k.lower() != "prometheus_multiproc_dir"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys\nfrom app.services import metrics\n"
         "sys.stdout.write(metrics.exposition().decode())"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    for name in NAMES:
        assert f"{name} 0.0" in proc.stdout


# ── The merge mode across workers (real subprocesses) ──────────────────

def _worker(code, mp_dir):
    env = {k: v for k, v in os.environ.items()
           if k.lower() != "prometheus_multiproc_dir"}
    env.update(PROMETHEUS_MULTIPROC_DIR=str(mp_dir), PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          cwd=REPO_ROOT, env=env, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _scrape_value(mp_dir, name):
    text = _worker("""
        import sys
        from app.services import metrics
        sys.stdout.write(metrics.exposition().decode())
    """, mp_dir)
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name == name:
                return s.value
    return None


def test_the_last_success_time_merges_as_max_across_workers(tmp_path):
    """Worker A passed a drill at 1790000000. Later worker B (seeded from an
    older manifest) sets a smaller time and C holds 0. None may hide A."""
    _worker("""
        from app.services import metrics
        metrics.backup_drill_last_success_timestamp_seconds.set(1790000000)
        metrics.mark_process_dead()
    """, tmp_path)
    _worker("""
        from app.services import metrics
        metrics.backup_drill_last_success_timestamp_seconds.set(1780000000)
    """, tmp_path)
    _worker("""
        from app.services import metrics
        metrics.backup_drill_last_success_timestamp_seconds.set(0)
    """, tmp_path)

    assert _scrape_value(
        tmp_path, "backup_drill_last_success_timestamp_seconds") == 1790000000


@pytest.mark.parametrize("name,first,second", [
    ("backup_drill_last_ok", 1, 0),
    ("backup_drill_last_ok", 0, 1),
    ("backup_drill_last_duration_seconds", 90, 12),
    ("backup_drill_last_duration_seconds", 12, 90),
])
def test_ok_and_duration_merge_as_the_most_recent_write(tmp_path, name, first, second):
    """Under max, a failed drill (0) written after a pass (1) would stay hidden
    behind the older 1. The newest write is the truth."""
    _worker(f"""
        from app.services import metrics
        metrics.{name}.set({first})
    """, tmp_path)
    _worker(f"""
        from app.services import metrics
        metrics.{name}.set({second})
    """, tmp_path)

    assert _scrape_value(tmp_path, name) == second
