"""The `drill` block is the LAST thing a finished drill makes visible.

WHY
---
A client that sees the block in manifest.json takes it as "the drill is done".
It may then start the next drill (needs the lock free) or read /metrics (needs
the gauges set). So three things must already be true when the block appears:
the advisory lock is released, the metrics are published, and only then is the
block written. The outcome log line comes after the block.

No server is used. The three steps are replaced by fakes that write their name
into one list, and the manifest write is seen through os.replace.
"""
import json
import os

import pytest

from app.services import pg_backup, restore_drill

BACKUP_ID = "pg_20260830_120000_ab12cd"


@pytest.fixture
def backup(tmp_path, monkeypatch):
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    folder = tmp_path / "pg" / BACKUP_ID
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"backup_id": BACKUP_ID, "row_counts": {"app.admins": 1}}),
        encoding="utf-8")
    (folder / "padyar.dump").write_bytes(b"not a real dump")
    return BACKUP_ID


@pytest.fixture
def steps(backup, monkeypatch):
    """Record release / publish / write / log in the order they happen."""
    seen = []
    manifest_file = pg_backup._manifest_path(backup)
    real_replace = os.replace

    def spy_replace(src, dst, *a, **kw):
        real_replace(src, dst, *a, **kw)
        if os.path.abspath(dst) == os.path.abspath(manifest_file):
            seen.append("write")

    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr(restore_drill, "_release_lock",
                        lambda conn: seen.append("release"))
    monkeypatch.setattr(restore_drill, "_publish_metrics",
                        lambda backup_id, block: seen.append("publish"))
    for name in ("info", "warning", "error"):
        monkeypatch.setattr(restore_drill.applog, name,
                            lambda *a, **kw: seen.append("log"))
    return seen


def _fake_drill(status):
    def drill(backup_id, manifest, block):
        block["status"] = status
        block["reason"] = "x"
    return drill


@pytest.mark.parametrize("status", ["passed", "failed", "skipped"])
def test_a_drill_releases_the_lock_then_publishes_then_writes_the_block(
        backup, steps, monkeypatch, status):
    monkeypatch.setattr(restore_drill, "_drill", _fake_drill(status))
    manifest = restore_drill._load(backup)

    restore_drill._execute(backup, manifest, "pytest", object())

    # "log" also appears for the started line, before everything else.
    assert [s for s in steps if s != "log"] == ["release", "publish", "write"]
    assert steps[-1] == "log", "the outcome is logged after the block is saved"


def test_a_drill_that_crashes_still_releases_the_lock_before_the_block(
        backup, steps, monkeypatch):
    def boom(backup_id, manifest, block):
        raise RuntimeError("boom")

    monkeypatch.setattr(restore_drill, "_drill", boom)

    block = restore_drill._execute(backup, restore_drill._load(backup),
                                   "pytest", object())

    assert block["status"] == "failed"
    assert [s for s in steps if s != "log"] == ["release", "publish", "write"]


def test_the_lock_skip_path_publishes_before_it_writes_the_block(backup, steps):
    error = restore_drill.DrillLockUnavailable()

    restore_drill._record_lock_skip(backup, "pytest", error)

    assert [s for s in steps if s != "log"] == ["publish", "write"]
    assert steps[-1] == "log"
