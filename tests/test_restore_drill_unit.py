"""Restore drill rules that need no server.

The drill restores into `<live name>_drill`. The two ways that goes wrong are a
name that points back at the live database, and a name PostgreSQL cuts to 63
bytes so it lands on some other database. Both are refused here before any
connection is made. The server-side behavior is in
tests/postgres/test_restore_drill.py.
"""
import inspect
import json
import os
import subprocess

import pytest

from app.services import pg_backup, restore_drill


# ── The drill database name ─────────────────────────────────────────────

def test_a_normal_live_name_gets_the_drill_suffix():
    assert restore_drill.drill_db_name("padyar_inotex") == "padyar_inotex_drill"


def test_a_derived_name_of_exactly_63_bytes_is_allowed():
    live = "a" * (63 - len("_drill"))
    assert len(restore_drill.drill_db_name(live).encode()) == 63


@pytest.mark.parametrize("live", [
    "",
    "padyar_inotex_drill",
    "_drill",
    "a" * (63 - len("_drill") + 1),
    "پ" * 30,  # 60 bytes in UTF-8, 30 characters: the limit counts bytes
])
def test_a_name_that_could_hit_the_live_database_is_refused(live):
    with pytest.raises(restore_drill.DrillRefused) as caught:
        restore_drill.drill_db_name(live)
    assert caught.value.message_fa


def test_the_drill_name_comes_from_the_connection_not_from_input():
    source = inspect.getsource(restore_drill._drill)
    assert "_conn_parts" in source
    assert "drill_db_name" in source
    for entry in (restore_drill.run, restore_drill.start):
        assert "dbname" not in inspect.signature(entry).parameters


# ── Newest drill across backups ─────────────────────────────────────────

def _manifest(backup_id, checked_at=None, status="passed", with_drill=True):
    m = {"backup_id": backup_id}
    if with_drill:
        m["drill"] = {"status": status, "checked_at": checked_at}
    return m


def test_latest_drill_picks_the_newest_checked_at_and_names_its_backup():
    found = restore_drill.latest_drill([
        _manifest("pg_20260101_000000_aaaaaa", "2026-01-01T03:00:00+00:00"),
        _manifest("pg_20260103_000000_bbbbbb", "2026-01-03T03:00:00+00:00",
                  status="failed"),
        _manifest("pg_20260102_000000_cccccc", "2026-01-02T03:00:00+00:00"),
    ])
    assert found["backup_id"] == "pg_20260103_000000_bbbbbb"
    assert found["status"] == "failed"


def test_latest_drill_skips_manifests_that_never_had_a_drill():
    found = restore_drill.latest_drill([
        _manifest("pg_20260105_000000_aaaaaa", with_drill=False),
        _manifest("pg_20260101_000000_bbbbbb", "2026-01-01T03:00:00+00:00"),
    ])
    assert found["backup_id"] == "pg_20260101_000000_bbbbbb"


def test_latest_drill_is_none_when_no_backup_was_ever_drilled():
    assert restore_drill.latest_drill([]) is None
    assert restore_drill.latest_drill(
        [_manifest("pg_20260105_000000_aaaaaa", with_drill=False)]) is None


def test_latest_drill_does_not_raise_on_junk():
    junk = [None, 5, "x", {}, {"drill": "no"}, {"drill": None},
            {"backup_id": "p", "drill": {"status": "passed", "checked_at": 7}},
            {"backup_id": "q", "error": "manifest unreadable"},
            _manifest("pg_20260101_000000_bbbbbb", "2026-01-01T03:00:00+00:00")]
    found = restore_drill.latest_drill(junk)
    assert found["backup_id"] == "pg_20260101_000000_bbbbbb"
    assert restore_drill.latest_drill(None) is None
    assert restore_drill.latest_drill("not a list") is None


# ── Old manifests still load ────────────────────────────────────────────

def test_old_manifests_without_drill_or_schema_migrations_still_list(
        tmp_path, monkeypatch):
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    backup_id = "pg_20260101_030000_abc123"
    os.makedirs(os.path.join(pg_backup.BACKUP_DIR, backup_id))
    old = {"backup_id": backup_id, "bytes": 10, "sha256": "x",
           "verification": {"status": "verified", "checked_at": None}}
    with open(pg_backup._manifest_path(backup_id), "w", encoding="utf-8") as f:
        json.dump(old, f)

    assert pg_backup.list_backups() == [old]
    assert restore_drill.latest_drill(pg_backup.list_backups()) is None


# ── Timeouts ────────────────────────────────────────────────────────────

class _Done:
    returncode = 0
    stdout = ""
    stderr = ""


def _spy_subprocess(monkeypatch):
    seen = {}

    def fake(argv, **kwargs):
        seen.update(kwargs)
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake)
    return seen


def test_run_uses_the_module_timeout_read_at_call_time(monkeypatch):
    seen = _spy_subprocess(monkeypatch)
    monkeypatch.setattr(pg_backup, "_TIMEOUT", 17)
    pg_backup._run(["x"], {}, "x")
    assert seen["timeout"] == 17


def test_run_uses_the_timeout_it_is_given(monkeypatch):
    seen = _spy_subprocess(monkeypatch)
    pg_backup._run(["x"], {}, "x", timeout=999)
    assert seen["timeout"] == 999


def test_restore_has_a_longer_timeout_than_the_dump():
    assert pg_backup._RESTORE_TIMEOUT > pg_backup._TIMEOUT


def test_the_panel_restore_passes_the_restore_timeout():
    source = inspect.getsource(pg_backup.restore)
    assert "timeout=_RESTORE_TIMEOUT" in source


def test_create_and_verify_keep_the_dump_timeout():
    for fn in (pg_backup.create, pg_backup.verify):
        assert "_RESTORE_TIMEOUT" not in inspect.getsource(fn)
