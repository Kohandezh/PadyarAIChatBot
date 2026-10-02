"""Backup metrics must describe a RESTORABLE backup, and survive a restart.

THE DEFECT THIS FILE PINS. The backup metrics said "a dump file was written",
not "a backup we can restore exists":

  * `backup_outcome_total{result="success"}` went up inside pg_backup.create(),
    BEFORE verify() ran. A dump that then failed verification still counted
    as a success.
  * `result="failed"` went up only when pg_dump itself failed. An error before
    the dump, or a failed verify, counted nothing.
  * Nothing told Prometheus WHEN the last good backup happened, so a
    "backup is stale" alert had nothing to read.
  * The labelled counter had no series until the first backup after a start,
    so `increase(...{result="failed"}[..])` saw nothing after a restart.

The contract now:

  * `_run_backup_now` (the scheduler, and every caller of create_backup_now)
    counts one outcome per attempt: `success` only when the dump was created
    AND verified, `failed` for every other ending, exactly once.
  * pg_backup.create() alone (the Infrastructure page's manual create, the
    restore safety backup) counts nothing.
  * `backup_last_success_timestamp_seconds` is the created_at time of the
    newest VERIFIED backup. It never goes down. 0 means none is known.
  * At startup it is seeded from the manifests already on disk.

pg_dump and pg_restore are never run: pg_backup._run is faked, the same
boundary tests/test_metrics.py and tests/test_backup_offsite.py fake.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── helpers ────────────────────────────────────────────────────────────


def _count(result):
    from app.services import metrics
    return int(metrics.backup_outcome_total.labels(result=result)._value.get())


def _gauge():
    from app.services import metrics
    family = next(iter(metrics.backup_last_success_timestamp_seconds.collect()))
    return family.samples[0].value


def _ts(iso):
    return datetime.fromisoformat(iso).timestamp()


def _exposed(text, name):
    from prometheus_client.parser import text_string_to_metric_families
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name == name:
                return s.value
    return None


def _fake_run(listing_ok=True):
    """pg_dump writes a small file; pg_restore --list parses (or not)."""
    def run(argv, env, what):
        if "--file" in argv:
            dump = argv[argv.index("--file") + 1]
            with open(dump, "wb") as f:
                f.write(b"padyar-dump")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if not listing_ok:
            from app.services.pg_backup import BackupError
            raise BackupError("pg_restore --list ناموفق بود.")
        return subprocess.CompletedProcess(
            argv, 0, stdout="\n".join(f"entry {i}" for i in range(8)), stderr="")
    return run


def _write_manifest(root, backup_id, created_at, status="verified", raw=None):
    directory = os.path.join(root, backup_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        if raw is not None:
            f.write(raw)
        else:
            json.dump({"backup_id": backup_id, "created_at": created_at,
                       "verification": {"status": status,
                                        "checked_at": created_at}}, f)


@pytest.fixture(autouse=True)
def _gauge_starts_at_zero():
    """The gauge is process-wide, so every test starts from "none known"."""
    from app.services import metrics
    metrics.backup_last_success_timestamp_seconds.set(0)
    yield
    metrics.backup_last_success_timestamp_seconds.set(0)


@pytest.fixture
def pg(tmp_path, monkeypatch):
    import app.config as config
    from app.services import backup, backup_offsite, pg_backup
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pg_backup, "_run", _fake_run())
    monkeypatch.setattr(backup_offsite, "copy_verified_dump",
                        lambda *a, **k: {"status": "skipped"})
    monkeypatch.setattr(backup, "_backup_engine", lambda: "postgres")
    return pg_backup


# ── One outcome per attempt ───────────────────────────────────────────


def test_a_created_and_verified_backup_counts_one_success_and_sets_its_time(pg):
    from app.services import backup
    success, failed = _count("success"), _count("failed")

    backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert _count("success") == success + 1
    assert _count("failed") == failed
    [manifest] = pg.list_backups()
    assert manifest["verification"]["status"] == "verified"
    assert _gauge() == _ts(manifest["created_at"])


def test_a_backup_that_fails_verification_is_a_failure_not_a_success(
        pg, monkeypatch):
    from app.services import backup
    monkeypatch.setattr(pg, "_run", _fake_run(listing_ok=False))
    success, failed = _count("success"), _count("failed")

    backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert _count("failed") == failed + 1
    assert _count("success") == success
    assert _gauge() == 0, "an unverified backup must not move the timestamp"
    [manifest] = pg.list_backups()
    assert manifest["verification"]["status"] == "failed"


def test_a_verify_that_raises_is_one_failure_and_the_run_still_prunes(
        pg, monkeypatch):
    from app.services import backup

    def boom(backup_id, actor="", offsite=True):
        raise RuntimeError("pg_restore missing")

    monkeypatch.setattr(pg, "verify", boom)
    pruned = []
    monkeypatch.setattr(pg, "prune", lambda **kw: pruned.append(True) or [])
    success, failed = _count("success"), _count("failed")

    backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert _count("failed") == failed + 1
    assert _count("success") == success
    assert _gauge() == 0
    assert pruned == [True], "a verify failure must not stop the prune"


def test_a_failed_pg_dump_counts_exactly_one_failure(pg, monkeypatch):
    from app.services import backup

    def boom(argv, env, what):
        raise pg.BackupError("pg_dump ناموفق بود.")

    monkeypatch.setattr(pg, "_run", boom)
    success, failed = _count("success"), _count("failed")

    with pytest.raises(pg.BackupError):
        backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert _count("failed") == failed + 1, "one failed attempt, one increment"
    assert _count("success") == success
    assert pg.list_backups() == [], "the half-written backup is still removed"


def test_an_error_before_the_dump_is_counted_as_a_failure(pg, monkeypatch):
    from app.services import backup

    def no_database_url():
        raise RuntimeError("DATABASE_URL is not set")

    monkeypatch.setattr(pg, "_conn_parts", no_database_url)
    failed = _count("failed")

    with pytest.raises(RuntimeError):
        backup._run_backup_now(actor="scheduler", kind="scheduled")

    assert _count("failed") == failed + 1


def test_create_alone_counts_no_outcome(pg):
    """The Infrastructure page's manual create and the restore safety backup
    call create() and never verify. They are not a verified attempt, so they
    count nothing (docs/engineering/MONITORING.md says so)."""
    success, failed = _count("success"), _count("failed")

    pg.create(actor="admin")

    assert (_count("success"), _count("failed")) == (success, failed)
    assert _gauge() == 0


def test_verifying_a_manual_backup_later_moves_the_timestamp(pg):
    manifest = pg.create(actor="admin")
    assert _gauge() == 0

    pg.verify(manifest["backup_id"], actor="admin")

    assert _gauge() == _ts(manifest["created_at"])


def test_verifying_an_older_backup_never_lowers_the_timestamp(pg):
    _write_manifest(pg.BACKUP_DIR, "pg_20260901_030000_aaaaaa",
                    "2026-09-01T03:00:00+00:00", status="unknown")
    newer = "2026-09-20T03:00:00+00:00"
    pg.record_verified({"created_at": newer,
                        "verification": {"status": "verified"}})
    assert _gauge() == _ts(newer)

    with open(os.path.join(pg.BACKUP_DIR, "pg_20260901_030000_aaaaaa",
                           "padyar.dump"), "wb") as f:
        f.write(b"x")
    manifest_file = os.path.join(pg.BACKUP_DIR, "pg_20260901_030000_aaaaaa",
                                 "manifest.json")
    with open(manifest_file, encoding="utf-8") as f:
        data = json.load(f)
    data["sha256"] = hashlib.sha256(b"x").hexdigest()
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    result = pg.verify("pg_20260901_030000_aaaaaa", actor="admin")

    assert result["verification"]["status"] == "verified"
    assert _gauge() == _ts(newer), "timestamps only grow"


# ── Seeding from disk at startup ──────────────────────────────────────


def test_seeding_with_no_backup_directory_leaves_zero(pg):
    assert not os.path.exists(pg.BACKUP_DIR)
    pg.seed_last_success_metric()
    assert _gauge() == 0


def test_seeding_picks_the_newest_verified_backup_and_skips_the_rest(pg):
    root = pg.BACKUP_DIR
    _write_manifest(root, "pg_20260910_030000_aaaaaa", "2026-09-10T03:00:00+00:00")
    _write_manifest(root, "pg_20260912_030000_bbbbbb", "2026-09-12T03:00:00+00:00")
    _write_manifest(root, "pg_20260913_030000_cccccc",
                    "2026-09-13T03:00:00+00:00", status="failed")
    _write_manifest(root, "pg_20260914_030000_dddddd",
                    "2026-09-14T03:00:00+00:00", status="unknown")
    _write_manifest(root, "pg_20260915_030000_eeeeee", None, raw="{not json")
    _write_manifest(root, "pg_20260916_030000_ffffff", None, raw="[1, 2]")
    _write_manifest(root, "pg_20260917_030000_abcdef", "not-a-date")

    pg.seed_last_success_metric()

    assert _gauge() == _ts("2026-09-12T03:00:00+00:00")


def test_seeding_ignores_a_directory_of_only_unusable_manifests(pg):
    _write_manifest(pg.BACKUP_DIR, "pg_20260915_030000_eeeeee", None,
                    raw="{not json")
    _write_manifest(pg.BACKUP_DIR, "pg_20260914_030000_dddddd",
                    "2026-09-14T03:00:00+00:00", status="unknown")
    pg.seed_last_success_metric()
    assert _gauge() == 0


@pytest.mark.parametrize("bad_verification", ["verified", ["verified"], 7])
def test_one_misshapen_manifest_does_not_stop_the_rest_from_seeding(
        pg, bad_verification):
    """A NEWER manifest whose `verification` is not an object must be skipped,
    and the older properly verified backup must still seed the gauge."""
    _write_manifest(pg.BACKUP_DIR, "pg_20260910_030000_aaaaaa",
                    "2026-09-10T03:00:00+00:00")
    _write_manifest(pg.BACKUP_DIR, "pg_20260920_030000_bbbbbb", None, raw=json.dumps(
        {"backup_id": "pg_20260920_030000_bbbbbb",
         "created_at": "2026-09-20T03:00:00+00:00",
         "verification": bad_verification}))

    pg.seed_last_success_metric()

    assert _gauge() == _ts("2026-09-10T03:00:00+00:00")


def test_a_manifest_whose_verification_is_not_an_object_is_not_verified(pg):
    assert pg.record_verified({"created_at": "2026-09-10T03:00:00+00:00",
                               "verification": "verified"}) is False
    assert _gauge() == 0


def test_seeding_skips_a_manifest_that_breaks_and_seeds_the_others(
        pg, monkeypatch):
    """Errors are isolated per manifest, not per loop."""
    _write_manifest(pg.BACKUP_DIR, "pg_20260910_030000_aaaaaa",
                    "2026-09-10T03:00:00+00:00")
    _write_manifest(pg.BACKUP_DIR, "pg_20260920_030000_bbbbbb",
                    "2026-09-20T03:00:00+00:00")
    real = pg.record_verified

    def breaks_on_the_newer(manifest):
        if manifest.get("backup_id") == "pg_20260920_030000_bbbbbb":
            raise RuntimeError("unexpected manifest shape")
        return real(manifest)

    monkeypatch.setattr(pg, "record_verified", breaks_on_the_newer)

    pg.seed_last_success_metric()

    assert _gauge() == _ts("2026-09-10T03:00:00+00:00")


def test_a_created_at_far_in_the_future_is_ignored(pg):
    """The gauge only moves forward, so a future time would pin it forever
    and the stale alert could never fire again."""
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(
        timespec="seconds")
    pg.record_verified({"backup_id": "pg_future", "created_at": future,
                        "verification": {"status": "verified"}})
    assert _gauge() == 0


def test_a_created_at_in_the_past_still_moves_the_gauge(pg):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(
        timespec="seconds")
    pg.record_verified({"backup_id": "pg_past", "created_at": past,
                        "verification": {"status": "verified"}})
    assert _gauge() == _ts(past)


def test_a_broken_metric_never_makes_verify_fail(pg, monkeypatch):
    """AC8: backups behave as before. A metric write that fails must not make
    verify() raise, and must not skip the off-site copy after it."""
    from app.services import backup_offsite, metrics
    manifest = pg.create(actor="admin")

    def broken(timestamp):
        raise OSError("metrics directory gone")

    monkeypatch.setattr(metrics, "set_backup_last_success", broken)
    copied = []
    monkeypatch.setattr(backup_offsite, "copy_verified_dump",
                        lambda backup_id, manifest, actor="": copied.append(backup_id))

    result = pg.verify(manifest["backup_id"], actor="admin")

    assert result["verification"]["status"] == "verified"
    assert copied == [manifest["backup_id"]]


def test_seeding_never_raises(pg, monkeypatch):
    def broken():
        raise PermissionError("backups dir unreadable")

    monkeypatch.setattr(pg, "list_backups", broken)
    pg.seed_last_success_metric()
    assert _gauge() == 0


def test_app_startup_seeds_the_timestamp_from_disk(pg, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "METRICS_TOKEN", "t-backup-metrics")
    _write_manifest(pg.BACKUP_DIR, "pg_20260912_030000_bbbbbb",
                    "2026-09-12T03:00:00+00:00")
    from app.main import app

    with TestClient(app) as c:
        res = c.get("/metrics", headers={"Authorization": "Bearer t-backup-metrics"})

    assert res.status_code == 200
    assert _exposed(res.text, "backup_last_success_timestamp_seconds") == \
        _ts("2026-09-12T03:00:00+00:00")


# ── From the first scrape after a start ────────────────────────────────


def test_a_fresh_process_exposes_both_outcomes_at_zero_and_the_gauge_at_zero():
    """`increase(...{result="failed"}[1h])` needs the series to exist before
    the first failure after a restart. A fresh process shows it."""
    env = {k: v for k, v in os.environ.items()
           if k.lower() != "prometheus_multiproc_dir"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys\nfrom app.services import metrics\n"
         "sys.stdout.write(metrics.exposition().decode())"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert 'backup_outcome_total{result="success"} 0.0' in text
    assert 'backup_outcome_total{result="failed"} 0.0' in text
    assert "backup_last_success_timestamp_seconds 0.0" in text
