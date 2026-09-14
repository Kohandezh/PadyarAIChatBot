"""Off-site copy of a VERIFIED PostgreSQL backup dump.

WHY A SECOND COPY
-----------------
pg_backup writes every dump under BASE_DIR/backups/postgres — on the same
host, and usually the same disk, as the database it protects. One host-level
event (disk death, ransomware, a bad rm) then takes the database AND every
copy of it together. This module copies the dump that just passed verify()
to a second failure domain: another machine over rsync/ssh, or an
already-mounted network path.

FAILURE IS NON-FATAL — BY DESIGN
--------------------------------
The local backup is valid the moment verify() passes. The off-site copy is
hardening, not a gate: a full remote disk, a dead network or a wrong target
must never turn a good nightly backup into a failed job, and must never
raise into the backup pipeline. Every failure is recorded in the backup's
manifest.json (`offsite` block) and as a service event, then swallowed.

COMMAND SAFETY
--------------
The target comes from OFFSITE_BACKUP_TARGET (operator env, never the
browser) and is dispatched by strict prefix. rsync runs with a FIXED argv
list — no shell, no interpolation of anything except the dump path and the
operator's own target string, the same discipline pg_backup applies to
pg_dump/pg_restore.
"""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

from app.config import logger
from app.services import applog

_RSYNC_PREFIX = "rsync:"
_DIR_PREFIX = "dir:"


def _target() -> str:
    # Read at CALL time, not import time, so tests (and a .env edited after
    # boot on a future install) monkeypatch one source of truth.
    from app.config import OFFSITE_BACKUP_TARGET
    return (OFFSITE_BACKUP_TARGET or "").strip()


def _timeout() -> int:
    from app.config import OFFSITE_BACKUP_TIMEOUT
    return OFFSITE_BACKUP_TIMEOUT


def copy_verified_dump(backup_id: str, manifest: dict, actor: str = ""):
    """Copy one verified dump off-site. Returns the result block, or None
    when no target is configured. NEVER raises — see the module docstring.
    """
    from app.services import pg_backup

    target = _target()
    if not target:
        return None

    started = time.perf_counter()
    result = {
        "target": target,
        "source_sha256": (manifest or {}).get("sha256", ""),
        "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "failed",
        "exit_code": None,
        "error": "",
    }
    try:
        dump = pg_backup.member_path(backup_id, "padyar.dump")
        if not dump:
            raise FileNotFoundError("padyar.dump")
        if target.startswith(_RSYNC_PREFIX):
            rc = _rsync(dump, target[len(_RSYNC_PREFIX):], backup_id)
            result["exit_code"] = rc
            if rc != 0:
                raise RuntimeError(f"rsync exited {rc}")
        elif target.startswith(_DIR_PREFIX):
            _dir_copy(dump, target[len(_DIR_PREFIX):], backup_id)
            result["exit_code"] = 0
        else:
            raise ValueError(
                f"unrecognized OFFSITE_BACKUP_TARGET (expected "
                f"{_RSYNC_PREFIX}… or {_DIR_PREFIX}…)")
        result["status"] = "copied"
    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except Exception as e:  # noqa: BLE001 — non-fatal by contract
        result["error"] = f"{type(e).__name__}: {e}"[:300]
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)

    _record(backup_id, manifest, result, actor=actor)
    return result


def _rsync(dump: str, remote: str, backup_id: str) -> int:
    """rsync to `remote` (no trailing slash). Returns the exit code."""
    dest = f"{remote.rstrip('/')}/{backup_id}.dump"
    proc = subprocess.run(
        ["rsync", "-a", "--chmod=F600", dump, dest],
        capture_output=True, text=True, timeout=_timeout())
    if proc.returncode != 0:
        logger.error("[backup_offsite] rsync failed rc=%s: %s",
                     proc.returncode, (proc.stderr or "")[:400])
    return proc.returncode


def _dir_copy(dump: str, directory: str, backup_id: str) -> None:
    """Copy onto an ALREADY-MOUNTED path, then fsync so a crash right after
    the copy does not report a file the storage never received."""
    directory = directory.rstrip("/") or "/"
    # The directory must already exist: creating it here would silently
    # "succeed" onto the local disk when the mount is down — the exact
    # single-host failure this feature exists to remove.
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"{directory} is not a mounted directory")
    dest = os.path.join(directory, f"{backup_id}.dump")
    shutil.copyfile(dump, dest)
    os.chmod(dest, 0o600)
    fd = os.open(dest, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    # Best-effort: some platforms/filesystems refuse fsync on a directory fd.
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _record(backup_id: str, manifest: dict, result: dict, actor: str = "") -> None:
    """Persist the offsite block into the backup's manifest + one service
    event. Recording must never raise either — it runs after the copy, in
    the success path of verify().
    """
    from app.services import pg_backup

    if manifest is not None:
        manifest["offsite"] = result
    try:
        path = os.path.join(pg_backup.backup_dir(backup_id), "manifest.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            stored["offsite"] = result
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(stored, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.error("[backup_offsite] could not record result: %s",
                     type(e).__name__)

    if result["status"] == "copied":
        applog.service(
            "backup.offsite.copied", "پشتیبان روی مقصد خارج از سرور کپی شد",
            actor=actor, target=backup_id, outcome="ok",
            duration_ms=result["duration_ms"],
            metadata={"offsite_target": result["target"],
                      "sha256": result["source_sha256"]})
    else:
        applog.service(
            "backup.offsite.failed",
            "کپی پشتیبان روی مقصد خارج از سرور ناموفق بود (پشتیبان محلی سالم است)",
            level="error", actor=actor, target=backup_id, outcome="failed",
            duration_ms=result["duration_ms"],
            metadata={"offsite_target": result["target"],
                      "exit_code": result["exit_code"],
                      "error": result["error"]})
