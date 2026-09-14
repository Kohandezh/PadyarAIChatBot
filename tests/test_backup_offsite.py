"""Off-site copy of a VERIFIED backup dump — the second failure domain.

The gap this file pins (2026-09-14 assessment): every pg_dump lands under
backups/postgres on the SAME host as the database it protects. One
host-level event (disk death, ransomware, a bad rm) erases the data and
every copy of it together.

What is asserted here, with subprocess and the filesystem under test control:

  * a configured target receives the dump, and the result is recorded in the
    backup's manifest.json (`offsite` block) plus a service event
  * no OFFSITE_BACKUP_TARGET -> a no-op: nothing copied, nothing written,
    nothing logged
  * every off-site failure (rsync exit, timeout, missing mount, unknown
    scheme) is NON-fatal: the local backup stays valid, the failure is
    recorded and logged, and the caller never sees an exception
  * pg_backup.verify() — the single point where a dump proves restorable —
    wires the copy in, and the restore pre-check opts out of it
"""
import hashlib
import json
import os
import subprocess

import pytest

BACKUP_ID = "pg_20260914_030000_ab12cd"
DUMP_BYTES = b"fake-dump-bytes"
DUMP_SHA = hashlib.sha256(DUMP_BYTES).hexdigest()


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """A pg_backup.BACKUP_DIR holding one already-verified-looking backup."""
    from app.services import pg_backup
    root = tmp_path / "postgres"
    d = root / BACKUP_ID
    d.mkdir(parents=True)
    (d / "padyar.dump").write_bytes(DUMP_BYTES)
    (d / "manifest.json").write_text(json.dumps({
        "backup_id": BACKUP_ID,
        "file": "padyar.dump",
        "sha256": DUMP_SHA,
        "verification": {"status": "verified"},
    }), encoding="utf-8")
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(root))
    return d


@pytest.fixture
def events(monkeypatch):
    """Capture applog.service calls so no assertion needs the log database."""
    from app.services import applog
    seen = []

    def _record(event, message="", level="info", **f):
        seen.append({"event": event, "level": level, **f})
        return len(seen)

    monkeypatch.setattr(applog, "service", _record)
    return seen


def _manifest(backup_dir):
    return json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))


# ── rsync target ────────────────────────────────────────────────────────

def test_rsync_success_copies_and_records(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "rsync:user@offsite:/srv/padyar-backups")
    argvs = []

    def fake_run(argv, **kwargs):
        argvs.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(backup_offsite.subprocess, "run", fake_run)

    result = backup_offsite.copy_verified_dump(
        BACKUP_ID, _manifest(backup_dir), actor="tester")

    assert result["status"] == "copied"
    argv, kwargs = argvs[0]
    # Fixed argv: no shell, no string interpolation of anything but the dump
    # path and the operator's own target — the pg_backup discipline.
    assert argv[0] == "rsync"
    assert "-a" in argv
    assert "--chmod=F600" in argv
    assert argv[-2] == str(backup_dir / "padyar.dump")
    assert argv[-1] == f"user@offsite:/srv/padyar-backups/{BACKUP_ID}.dump"
    # The copy runs under the timeout the config exposes.
    assert kwargs["timeout"] == config.OFFSITE_BACKUP_TIMEOUT

    block = _manifest(backup_dir)["offsite"]
    assert block["target"] == "rsync:user@offsite:/srv/padyar-backups"
    assert block["source_sha256"] == DUMP_SHA
    assert block["exit_code"] == 0
    assert isinstance(block["duration_ms"], int)
    assert [e["event"] for e in events] == ["backup.offsite.copied"]
    assert events[0]["level"] == "info"


def test_absent_target_is_a_noop(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    called = []
    monkeypatch.setattr(backup_offsite.subprocess, "run",
                        lambda *a, **k: called.append(a))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result is None
    assert called == []
    assert "offsite" not in _manifest(backup_dir)
    assert events == []


def test_rsync_failure_is_nonfatal_but_visible(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "rsync:user@offsite:/srv/padyar-backups")
    monkeypatch.setattr(backup_offsite.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 23, stdout="",
                                                     stderr="no space left"))

    # The whole contract: no exception reaches the caller.
    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert result["exit_code"] == 23
    block = _manifest(backup_dir)["offsite"]
    assert block["status"] == "failed"
    assert block["exit_code"] == 23
    assert events[0]["event"] == "backup.offsite.failed"
    assert events[0]["level"] == "error"


def test_timeout_is_nonfatal(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "rsync:user@offsite:/srv/padyar-backups")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="rsync", timeout=1)

    monkeypatch.setattr(backup_offsite.subprocess, "run", fake_run)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert result["exit_code"] is None
    assert result["error"] == "timeout"
    assert events[0]["event"] == "backup.offsite.failed"


# ── dir target ──────────────────────────────────────────────────────────

def test_dir_copy_writes_the_dump(backup_dir, events, tmp_path, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    mount = tmp_path / "offsite-mount"
    mount.mkdir()
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", f"dir:{mount}")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied"
    dest = mount / f"{BACKUP_ID}.dump"
    assert dest.read_bytes() == DUMP_BYTES
    assert (os.stat(dest).st_mode & 0o777) == 0o600
    block = _manifest(backup_dir)["offsite"]
    assert block["exit_code"] == 0
    assert events[0]["event"] == "backup.offsite.copied"


def test_dir_copy_refuses_an_unmounted_path(backup_dir, events, monkeypatch):
    """If the mount is down the copy must FAIL, not quietly write to the
    local disk under the mountpoint — that is the single-host failure this
    feature exists to remove."""
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "dir:/definitely-not-mounted-padyar")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert not os.path.exists("/definitely-not-mounted-padyar")
    assert events[0]["event"] == "backup.offsite.failed"


def test_unknown_target_scheme_fails_softly(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "ftp://who-knows/where")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert events[0]["event"] == "backup.offsite.failed"


# ── The wiring into pg_backup.verify() ──────────────────────────────────

def _listing_ok():
    """A pg_restore --list result with more than the 5 entries verify needs."""
    return subprocess.CompletedProcess(
        [], 0, stdout="\n".join(f"entry {i}" for i in range(8)), stderr="")


def test_verify_success_wires_the_offsite_copy(backup_dir, monkeypatch):
    """Remove the hook in pg_backup.verify() and this fails — the off-site
    copy would then have no production caller (anti-scaffold rule)."""
    from app.services import backup_offsite, pg_backup
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: "/usr/bin/true")
    monkeypatch.setattr(pg_backup, "_run", lambda *a, **k: _listing_ok())
    seen = []
    monkeypatch.setattr(
        backup_offsite, "copy_verified_dump",
        lambda backup_id, manifest, actor="":
            seen.append((backup_id, actor)) or {"status": "spied"})

    manifest = pg_backup.verify(BACKUP_ID, actor="tester")

    assert manifest["verification"]["status"] == "verified"
    assert seen == [(BACKUP_ID, "tester")]


def test_verify_can_opt_out_for_the_restore_precheck(backup_dir, monkeypatch):
    """restore() re-verifies before touching anything; a restore must never
    sit behind a network copy, so it passes offsite=False."""
    from app.services import backup_offsite, pg_backup
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: "/usr/bin/true")
    monkeypatch.setattr(pg_backup, "_run", lambda *a, **k: _listing_ok())
    seen = []
    monkeypatch.setattr(
        backup_offsite, "copy_verified_dump",
        lambda backup_id, manifest, actor="": seen.append(backup_id))

    manifest = pg_backup.verify(BACKUP_ID, actor="tester", offsite=False)

    assert manifest["verification"]["status"] == "verified"
    assert seen == []
