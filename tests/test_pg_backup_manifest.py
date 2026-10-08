"""verify() and the restore drill each change only their own manifest keys.

WHY
---
After create(), two things write a backup's manifest.json: verify() (its
`verification` and `toc_entries`) and the restore drill (its `drill` block).
Each used to read the whole file, change it in memory and write the whole file
back. When they overlapped, the last writer erased the other one's key. Now both
go through pg_backup._update_manifest, which locks, re-reads, sets only its
keys and replaces the file in one step.

No server is used. pg_restore is replaced by a fake that lists a table of
contents, and the backups folder is a temp folder.
"""
import hashlib
import json
import os
import threading
import time

import pytest

from app.services import pg_backup, restore_drill

BACKUP_ID = "pg_20260830_120000_ab12cd"
OTHER_ID = "pg_20260829_120000_cd34ef"
DUMP = b"not a real dump, but it has bytes"


def _make_backup(backup_id=BACKUP_ID):
    folder = os.path.join(pg_backup.BACKUP_DIR, backup_id)
    os.makedirs(folder)
    with open(os.path.join(folder, "padyar.dump"), "wb") as f:
        f.write(DUMP)
    manifest = {"backup_id": backup_id, "sha256": hashlib.sha256(DUMP).hexdigest(),
                "file": "padyar.dump", "bytes": len(DUMP),
                "row_counts": {"app.admins": 1},
                "verification": {"status": "unknown", "checked_at": None}}
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return manifest


def _read(backup_id=BACKUP_ID):
    with open(pg_backup._manifest_path(backup_id), encoding="utf-8") as f:
        return json.load(f)


class _Listing:
    stdout = "\n".join(f"{i}; 0 0 TABLE app t{i}" for i in range(8))


@pytest.fixture
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    os.makedirs(pg_backup.BACKUP_DIR)
    _make_backup()
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: name)
    monkeypatch.setattr(pg_backup, "_run", lambda *a, **kw: _Listing())
    for name in ("info", "warning", "error", "audit"):
        monkeypatch.setattr(pg_backup.applog, name, lambda *a, **kw: None)


def _drill_block(status="passed"):
    block = restore_drill._new_block("pytest")
    block.update(status=status, reason="x", checked_at="2026-08-30T13:00:00+00:00")
    return block


# ── The two writers keep each other's keys ──────────────────────────────

def test_a_drill_block_that_lands_during_verify_survives_it(backups, monkeypatch):
    """verify() read the file, then the drill saved its block, then verify wrote."""
    real = pg_backup._sha256
    landed = []

    def sha_then_drill_lands(path):
        digest = real(path)  # verify has read the manifest before this call
        restore_drill._write_block(BACKUP_ID, _drill_block())
        landed.append(True)
        return digest

    monkeypatch.setattr(pg_backup, "_sha256", sha_then_drill_lands)

    returned = pg_backup.verify(BACKUP_ID, actor="pytest", offsite=False)

    stored = _read()
    assert landed
    assert stored["drill"]["status"] == "passed", "verify erased the drill block"
    assert stored["verification"]["status"] == "verified"
    assert stored["toc_entries"] == 8
    assert returned["verification"]["status"] == "verified"
    assert returned["row_counts"] == {"app.admins": 1}, "verify returns the full manifest"
    assert returned["backup_id"] == BACKUP_ID


def test_a_drill_block_written_while_verify_is_hashing_keeps_the_verification(
        backups, monkeypatch):
    """The reverse order, with two real threads: the drill writes mid-verify."""
    real = pg_backup._sha256
    hashing, go = threading.Event(), threading.Event()

    def slow_sha(path):
        hashing.set()
        assert go.wait(10)
        return real(path)

    monkeypatch.setattr(pg_backup, "_sha256", slow_sha)
    done = []
    worker = threading.Thread(
        target=lambda: done.append(
            pg_backup.verify(BACKUP_ID, actor="pytest", offsite=False)))
    worker.start()
    assert hashing.wait(10)

    restore_drill._write_block(BACKUP_ID, _drill_block("failed"))
    assert _read()["verification"]["status"] == "unknown", "verify is mid-way"
    go.set()
    worker.join(10)

    stored = _read()
    assert done and not worker.is_alive()
    assert stored["drill"]["status"] == "failed"
    assert stored["verification"]["status"] == "verified"


def test_a_verify_that_finishes_before_the_drill_block_is_kept_by_it(backups):
    pg_backup.verify(BACKUP_ID, actor="pytest", offsite=False)

    restore_drill._write_block(BACKUP_ID, _drill_block())

    stored = _read()
    assert stored["verification"]["status"] == "verified"
    assert stored["toc_entries"] == 8
    assert stored["drill"]["status"] == "passed"


def test_a_failed_toc_read_does_not_erase_an_older_toc_count(backups, monkeypatch):
    pg_backup.verify(BACKUP_ID, actor="pytest", offsite=False)

    def unreadable(*a, **kw):
        raise pg_backup.BackupError("x")

    monkeypatch.setattr(pg_backup, "_run", unreadable)
    returned = pg_backup.verify(BACKUP_ID, actor="pytest", offsite=False)

    assert returned["verification"]["status"] == "failed"
    assert _read()["toc_entries"] == 8, "only keys verify has a new value for change"


# ── The lock really excludes another writer ─────────────────────────────

def test_a_writer_waits_while_another_holds_the_manifest_lock(backups):
    import fcntl
    lock_path = pg_backup._manifest_path(BACKUP_ID) + ".lock"
    finished = threading.Event()
    with open(lock_path, "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        worker = threading.Thread(target=lambda: (
            pg_backup._update_manifest(BACKUP_ID, {"mine": 1}), finished.set()))
        worker.start()
        assert not finished.wait(0.4), "the writer must wait for the lock"
        assert "mine" not in _read()
        fcntl.flock(held, fcntl.LOCK_UN)
    assert finished.wait(10)
    worker.join(10)
    assert _read()["mine"] == 1


@pytest.fixture
def fast_thread_switching():
    """Make the interpreter switch threads often, so an unlocked write loses."""
    import sys
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def test_many_updates_from_two_threads_lose_no_key(backups, fast_thread_switching):
    rounds = 150
    errors = []

    def writer(key):
        try:
            for i in range(rounds):
                pg_backup._update_manifest(BACKUP_ID, {key: i})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    seen_broken = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                _read()
            except ValueError:
                seen_broken.append(True)

    threads = [threading.Thread(target=writer, args=(k,)) for k in ("a", "b")]
    watcher = threading.Thread(target=reader)
    watcher.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    stop.set()
    watcher.join(10)

    assert not errors, errors
    stored = _read()
    assert stored["a"] == rounds - 1 and stored["b"] == rounds - 1
    assert stored["backup_id"] == BACKUP_ID and stored["row_counts"] == {"app.admins": 1}
    assert not seen_broken, "a reader saw a half-written manifest"


def test_update_keeps_other_keys_and_returns_the_new_manifest(backups):
    result = pg_backup._update_manifest(BACKUP_ID, {"drill": {"status": "passed"}})

    assert result == _read()
    assert result["drill"] == {"status": "passed"}
    assert result["sha256"] and result["row_counts"] == {"app.admins": 1}


def test_update_of_a_missing_backup_raises_and_creates_nothing(backups):
    with pytest.raises(OSError):
        pg_backup._update_manifest(OTHER_ID, {"drill": {}})
    assert not os.path.exists(os.path.join(pg_backup.BACKUP_DIR, OTHER_ID))


def test_update_with_a_bad_id_is_refused_before_any_file_is_made(backups, tmp_path):
    with pytest.raises(pg_backup.BackupError):
        pg_backup._update_manifest("../../x", {"drill": {}})
    assert list(tmp_path.glob("**/*.lock")) == []


def test_the_drill_block_write_never_raises_for_a_pruned_backup(backups):
    restore_drill._write_block(OTHER_ID, _drill_block())  # no such folder


# ── The lock file is not a backup and not a download ────────────────────

def test_the_lock_file_is_never_listed_as_a_backup_or_served(backups):
    from app.routers import backups as router
    pg_backup.verify(BACKUP_ID, actor="pytest", offsite=False)
    restore_drill._write_block(BACKUP_ID, _drill_block())
    folder = os.path.join(pg_backup.BACKUP_DIR, BACKUP_ID)
    assert "manifest.json.lock" in os.listdir(folder), "the lock file is real"

    rows = pg_backup.list_backups()

    assert [r["backup_id"] for r in rows] == [BACKUP_ID]
    assert not any(r.get("error") for r in rows)
    assert pg_backup.member_path(BACKUP_ID, "manifest.json.lock") is None
    assert pg_backup.member_path(BACKUP_ID, "../manifest.json.lock") is None
    assert pg_backup.member_path(BACKUP_ID, "padyar.dump")
    assert [f["name"] for f in router._pg_row(rows[0])["files"]] == ["padyar.dump"]


def test_delete_and_prune_remove_the_lock_file_with_the_folder(backups):
    _make_backup(OTHER_ID)
    for backup_id in (BACKUP_ID, OTHER_ID):
        restore_drill._write_block(backup_id, _drill_block())

    assert pg_backup.delete(OTHER_ID, actor="pytest") is True
    assert not os.path.exists(os.path.join(pg_backup.BACKUP_DIR, OTHER_ID))

    assert pg_backup.prune(keep=0) == [BACKUP_ID]
    assert os.listdir(pg_backup.BACKUP_DIR) == []
