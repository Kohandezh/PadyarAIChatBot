"""A backup is worth more than its row counts.

`pg_backup.create()` counts every table inside the snapshot it hands to
`pg_dump --snapshot=<id>` (see tests/postgres/test_pg_backup_counts.py). That
count step needs its own PostgreSQL connection. If that connection cannot
open, the nightly backup must still be taken the old way: a plain pg_dump
with no `--snapshot`, a manifest that says the counts are missing, and a
warning in the log. Losing a whole night's backup because a count failed would
trade the thing that matters for the thing that only checks it.

The count connection here fails for real: DATABASE_URL points at a closed
port. Only pg_dump is stubbed (it writes a placeholder file), because no
server is running.
"""
import json
import logging
import os

import pytest

PASSWORD = "s3cret-pw"


@pytest.fixture
def pg_backup(tmp_path, monkeypatch):
    from app.services import pg_backup
    monkeypatch.setenv("DATABASE_URL",
                       f"postgresql://padyar_app:{PASSWORD}@127.0.0.1:1/padyar")
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: f"/usr/bin/{name}")
    return pg_backup


@pytest.fixture
def dumps(pg_backup, monkeypatch):
    calls = []

    def fake_run(argv, env, what):
        calls.append(list(argv))
        with open(argv[argv.index("--file") + 1], "wb") as f:
            f.write(b"PGDMP placeholder")

    monkeypatch.setattr(pg_backup, "_run", fake_run)
    return calls


def test_an_unreachable_count_connection_still_takes_a_plain_backup(
        pg_backup, dumps, caplog):
    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        manifest = pg_backup.create(actor="pytest", reason="fallback")

    assert len(dumps) == 1
    assert not any(a.startswith("--snapshot") for a in dumps[0]), dumps[0]
    with open(pg_backup._manifest_path(manifest["backup_id"]), encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["row_counts"] is None
    assert on_disk["row_counts_source"] == "unavailable"
    assert os.path.getsize(pg_backup._dump_path(manifest["backup_id"])) > 0
    assert "row counts" in caplog.text.lower()


def test_the_fallback_keeps_every_existing_manifest_field(pg_backup, dumps):
    manifest = pg_backup.create(actor="pytest", reason="fields")

    for field in ("backup_id", "created_at", "created_by", "engine", "database",
                  "format", "file", "bytes", "sha256", "duration_ms", "reason",
                  "verification"):
        assert field in manifest, field
    assert manifest["verification"] == {"status": "unknown", "checked_at": None}


def test_no_password_on_the_fallback_command_line(pg_backup, dumps):
    pg_backup.create(actor="pytest", reason="argv")
    assert not any(PASSWORD in a for a in dumps[0])


def test_a_pg_dump_failure_still_fails_the_backup(pg_backup, monkeypatch):
    def broken(argv, env, what):
        raise pg_backup.BackupError("pg_dump ناموفق بود.")

    monkeypatch.setattr(pg_backup, "_run", broken)
    with pytest.raises(pg_backup.BackupError):
        pg_backup.create(actor="pytest", reason="broken")
    assert not os.listdir(pg_backup.BACKUP_DIR)


def test_an_old_manifest_without_row_counts_still_lists(pg_backup):
    backup_id = "pg_20260101_030000_abc123"
    os.makedirs(os.path.join(pg_backup.BACKUP_DIR, backup_id))
    old = {"backup_id": backup_id, "bytes": 10, "sha256": "x",
           "verification": {"status": "verified", "checked_at": None}}
    with open(pg_backup._manifest_path(backup_id), "w", encoding="utf-8") as f:
        json.dump(old, f)

    assert pg_backup.list_backups() == [old]
