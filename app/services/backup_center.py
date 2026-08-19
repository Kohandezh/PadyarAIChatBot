"""Backup Center — verified, restorable backup SETS.

WHY A "SET" AND NOT A FILE
--------------------------
This install runs TWO SQLite databases: the content/chat database
(`app.config.DB_PATH`) and the logging database (`app.config.LOGS_DB_PATH`).
A backup of one without the other is not a backup of the system — restoring it
leaves the install in a state that never existed. So the unit here is a *set*:
one directory holding a copy of every database plus a JSON manifest that says
what is inside it and what each file's SHA-256 was at the moment it was
written.

    backups/sets/set_20260818_140930_9f3a71/
        manifest.json          what this set contains (written once, evidence)
        verification.json      the last verdict (rewritten on every verify)
        chat_history.db        role "main"
        application_logs.db    role "logs"

WHY EVERY COPY GOES THROUGH backup_db.copy_database
---------------------------------------------------
Both databases run in WAL mode. A committed transaction can live in the `-wal`
sidecar for a long time before a checkpoint folds it into the `.db` file, so a
plain file copy silently loses recent writes and can catch a torn page.
`backup_db.copy_database()` uses SQLite's online backup API, which takes a
consistent snapshot of the whole database including the WAL. There is exactly
one copy routine in this subsystem; do not add a second.

"CREATED" IS NOT "USABLE"
------------------------
`verify()` is the point of this module. It re-reads every file in a set, checks
the manifest checksum against the bytes on disk, opens each file as SQLite and
runs `PRAGMA integrity_check`. A backup nobody has verified is a hope, not a
recovery plan — and `restore()` re-verifies at restore time rather than
trusting a stored flag, because the flag is old and the disk is not.

RESTORE IS NOT FULLY ATOMIC — READ THIS BEFORE "IMPROVING" IT
-------------------------------------------------------------
Restoring means overwriting live database files while the app is running and
holding its own connections. Two limits are real and are NOT papered over:

  1. Two files cannot be replaced in one atomic step. The restore is therefore
     done as a set with rollback: a safety backup of the CURRENT databases is
     taken first, and if the second database fails to restore, BOTH are put
     back from that safety set. A caller is told, honestly, whether the
     rollback succeeded.
  2. Connections already open (this process, or another gunicorn worker) are
     not closed by us and cannot be. SQLite keeps them consistent — they see
     the new content once the write lock is released — but a request that
     is *in flight* across the swap can read the old main database and the new
     log database. Restore therefore reports `restart_recommended: True`, and
     the operator-facing UI says to restart the app. A hot restore that is
     atomic across workers is not achievable in this architecture without a
     maintenance mode, and pretending otherwise would be the dangerous
     option.

Restoring the logging database necessarily discards log rows written after the
backup point. That is why the `restore.requested` audit row is written BEFORE
the safety backup is taken: the safety set captures it, and the
`restore.completed` / `restore.failed` row is written afterwards, into the
now-live log database. The destructive action is recorded on both sides of it.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from datetime import datetime, timezone

import backup_db
from app.config import logger
from app.services import applog

# ── Layout ──────────────────────────────────────────────────────────────
SETS_DIRNAME = "sets"
MANIFEST_NAME = "manifest.json"
VERDICT_NAME = "verification.json"

MAIN_ROLE = "main"
LOGS_ROLE = "logs"

# role -> file name inside the set. The names are fixed, so a member name from
# a manifest is checked against this map before it is ever used as a path.
ROLE_FILES = {
    MAIN_ROLE: "chat_history.db",
    LOGS_ROLE: "application_logs.db",
}
MEMBER_NAMES = set(ROLE_FILES.values()) | {MANIFEST_NAME, VERDICT_NAME}

# Persian labels for the operator UI — the panel must never show a file path.
ROLE_LABELS = {
    MAIN_ROLE: "اطلاعات اصلی (سوال‌ها، ویدیوها، تنظیمات)",
    LOGS_ROLE: "گزارش‌ها و رخدادها",
}

# The ONLY shape a backup id may have. Admits no separator, no dot, no
# dot-dot, no drive letter, no percent-escape — so "../../etc/passwd",
# "/etc/passwd" and "..%2f.." are rejected before any path is built.
_ID_RE = re.compile(r"^set_\d{8}_\d{6}_[0-9a-f]{6}$")

KEEP_SETS = 14  # how many sets survive prune()

# Kinds of set. `safety` sets are the undo for a restore.
KIND_MANUAL = "manual"
KIND_SCHEDULED = "scheduled"
KIND_SAFETY = "safety"


# ── Errors — each maps to one operator-facing Persian message ───────────

class BackupError(Exception):
    """Base class. `.fa` is the message an operator may see."""
    fa = "عملیات پشتیبان‌گیری انجام نشد."


class UnknownBackup(BackupError):
    fa = "نسخهٔ پشتیبان پیدا نشد."


class BackupNotVerified(BackupError):
    fa = ("این نسخهٔ پشتیبان سالم نیست و بازگردانی آن انجام نشد. "
          "نسخهٔ سالم دیگری را انتخاب کنید.")


class AuditUnavailable(BackupError):
    """Raised when the restore's audit row could not be persisted.

    FAIL-CLOSED. Operational logging is allowed to degrade; overwriting a live
    database with no record of who did it is not. If the audit trail cannot
    take the row, the restore does not happen."""
    fa = ("ثبت این عملیات در سوابق ممکن نشد، بنابراین بازگردانی انجام نشد. "
          "ابتدا مشکل سامانهٔ گزارش‌ها را برطرف کنید.")


class RestoreFailed(BackupError):
    fa = "بازگردانی ناموفق بود."


# ── Paths ───────────────────────────────────────────────────────────────

def sets_root() -> str:
    """backups/sets — resolved through backup_db so one override moves both."""
    return os.path.join(backup_db.backup_dir(), SETS_DIRNAME)


def _live_targets():
    """[(role, file name in the set, live path on disk)] for every database.

    Read from app.config at CALL time, never captured at import time: tests
    redirect DB_PATH/LOGS_DB_PATH per test, and an install may set them in
    .env after this module was first imported."""
    from app import config
    return [
        (MAIN_ROLE, ROLE_FILES[MAIN_ROLE], config.DB_PATH),
        (LOGS_ROLE, ROLE_FILES[LOGS_ROLE], config.LOGS_DB_PATH),
    ]


def _new_id() -> str:
    return "set_{}_{}".format(datetime.now().strftime("%Y%m%d_%H%M%S"),
                              secrets.token_hex(3))


def set_dir(backup_id: str):
    """Absolute directory for `backup_id`, or None if the id is not ours.

    Two independent guards on purpose:
      1. the allowlist regex — the id cannot express a traversal at all;
      2. realpath containment — the resolved directory must still be inside
         backups/sets, which also catches a symlink planted in that directory.
    Neither is load-bearing alone."""
    if not backup_id or not isinstance(backup_id, str) or not _ID_RE.match(backup_id):
        return None
    root = os.path.realpath(sets_root())
    path = os.path.realpath(os.path.join(root, backup_id))
    if path != root and not path.startswith(root + os.sep):
        return None
    return path


def member_path(backup_id: str, name: str):
    """Absolute path of one file inside a set, or None.

    `name` must be one of the fixed member names — a manifest is our own file,
    but it is still data on disk and is never trusted as a path fragment."""
    directory = set_dir(backup_id)
    if directory is None or name not in MEMBER_NAMES:
        return None
    path = os.path.realpath(os.path.join(directory, name))
    if not path.startswith(os.path.realpath(directory) + os.sep):
        return None
    return path


# ── Small helpers ───────────────────────────────────────────────────────

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def app_version() -> str:
    """This install's version or commit, when it can be known cheaply.

    A VERSION file wins; otherwise the git HEAD commit is read straight out of
    .git with no subprocess (no shell is available to this subsystem). Returns
    "" rather than guessing — an unknown version in a manifest is honest, a
    wrong one is worse than none."""
    base = backup_db.BASE_DIR
    try:
        version_file = os.path.join(base, "VERSION")
        if os.path.isfile(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                return f.read().strip()[:80]
    except OSError:
        pass
    try:
        head_file = os.path.join(base, ".git", "HEAD")
        with open(head_file, "r", encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref:"):
            return head[:40]
        ref = head.split(" ", 1)[1].strip()
        ref_file = os.path.join(base, ".git", *ref.split("/"))
        if os.path.isfile(ref_file):
            with open(ref_file, "r", encoding="utf-8") as f:
                return f.read().strip()[:40]
        packed = os.path.join(base, ".git", "packed-refs")
        with open(packed, "r", encoding="utf-8") as f:
            for line in f:
                if line.rstrip().endswith(" " + ref):
                    return line.split(" ", 1)[0][:40]
    except (OSError, IndexError):
        pass
    return ""


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: dict) -> bool:
    """Atomic-ish write: temp file then replace, so a crash mid-write cannot
    leave a half-parsed manifest that makes a good set look damaged."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


# ── Create ──────────────────────────────────────────────────────────────

def create(actor: str = "", kind: str = KIND_MANUAL) -> dict:
    """Take one backup SET covering every database. Returns its summary.

    Writes the manifest LAST: a directory without a manifest is an
    interrupted backup, and `list()` reports it as damaged rather than
    offering it for restore."""
    kind = kind if kind in (KIND_MANUAL, KIND_SCHEDULED, KIND_SAFETY) else KIND_MANUAL
    backup_id = _new_id()
    started = time.monotonic()

    applog.info("backup", "backup.started", "شروع پشتیبان‌گیری",
                actor=actor, target=backup_id, outcome="started",
                metadata={"kind": kind})

    directory = os.path.join(sets_root(), backup_id)
    try:
        os.makedirs(directory, exist_ok=False)
    except OSError as exc:
        applog.exception("backup", "backup.failed", exc, "ساخت پوشهٔ پشتیبان ممکن نشد")
        raise BackupError(str(exc)) from exc

    files = []
    try:
        for role, name, live in _live_targets():
            if role == LOGS_ROLE:
                # The log database may not exist yet on a fresh install. It is
                # part of the set either way, so make sure it is a real file
                # with real tables before copying it.
                applog.ensure_tables()
            dest = os.path.join(directory, name)
            # standalone: the stored file must be one artefact whose bytes do
            # not change when a verification later opens it — see copy_database.
            backup_db.copy_database(live, dest, standalone=True)
            files.append({
                "role": role,
                "name": name,
                "sha256": _sha256(dest),
                "bytes": os.path.getsize(dest),
            })

        duration_ms = int((time.monotonic() - started) * 1000)
        manifest = {
            "backup_id": backup_id,
            "created_at": _now_iso(),
            "created_by": actor or "system",
            "kind": kind,
            "files": files,
            "app_version": app_version(),
            "total_bytes": sum(f["bytes"] for f in files),
            "duration_ms": duration_ms,
            "status": "ok",
        }
        if not _write_json(os.path.join(directory, MANIFEST_NAME), manifest):
            raise BackupError("could not write manifest")
    except Exception as exc:
        # A half-written set must not survive to be offered as a restore source.
        shutil.rmtree(directory, ignore_errors=True)
        applog.exception("backup", "backup.failed", exc,
                         "پشتیبان‌گیری ناموفق بود",
                         actor=actor, target=backup_id, outcome="error",
                         duration_ms=int((time.monotonic() - started) * 1000))
        if isinstance(exc, BackupError):
            raise
        raise BackupError(str(exc)) from exc

    applog.info("backup", "backup.completed", "پشتیبان‌گیری کامل شد",
                actor=actor, target=backup_id, outcome="ok",
                duration_ms=duration_ms,
                metadata={"kind": kind, "files": len(files),
                          "total_bytes": manifest["total_bytes"]})
    applog.audit("admin.backup.create", "ایجاد نسخهٔ پشتیبان",
                 actor=actor or "system", target=backup_id, outcome="ok",
                 duration_ms=duration_ms, metadata={"kind": kind})
    return _summarise(backup_id, manifest, None)


# ── List ────────────────────────────────────────────────────────────────

def _summarise(backup_id: str, manifest, verdict) -> dict:
    """One row for the admin table. Never contains a filesystem path."""
    files = []
    if manifest:
        for f in manifest.get("files", []):
            files.append({
                "name": f.get("name", ""),
                "role": f.get("role", ""),
                "label": ROLE_LABELS.get(f.get("role", ""), f.get("name", "")),
                "bytes": f.get("bytes", 0),
                "sha256": f.get("sha256", ""),
            })
    state = "unknown"
    if verdict:
        state = "verified" if verdict.get("ok") else "failed"

    created_at = (manifest or {}).get("created_at", "")
    age_seconds = None
    try:
        if created_at:
            age_seconds = max(0, int(
                (datetime.now(timezone.utc)
                 - __import__('app.db.timeutil', fromlist=['as_datetime']).as_datetime(created_at)).total_seconds()))
    except ValueError:
        age_seconds = None

    return {
        "backup_id": backup_id,
        "created_at": created_at,
        "created_by": (manifest or {}).get("created_by", ""),
        "kind": (manifest or {}).get("kind", ""),
        "app_version": (manifest or {}).get("app_version", ""),
        "duration_ms": (manifest or {}).get("duration_ms"),
        "status": (manifest or {}).get("status", "damaged" if not manifest else "ok"),
        "files": files,
        "file_count": len(files),
        "total_bytes": (manifest or {}).get("total_bytes",
                                            sum(f["bytes"] for f in files)),
        "age_seconds": age_seconds,
        "verification": {
            "state": state,
            "checked_at": (verdict or {}).get("checked_at", ""),
            "problems": (verdict or {}).get("problems", []),
        },
    }


def list_sets() -> list:
    """Every backup set, newest first. Ids sort chronologically by construction.

    Named `list_sets`, not `list`: a module-level `list = ...` shadows the
    builtin for every function in this file, and the first future edit that
    writes `list(...)` here would fail in a way nobody would guess from the
    traceback."""
    root = sets_root()
    if not os.path.isdir(root):
        return []
    rows = []
    for name in os.listdir(root):
        if not _ID_RE.match(name):
            continue
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        manifest = _read_json(os.path.join(directory, MANIFEST_NAME))
        verdict = _read_json(os.path.join(directory, VERDICT_NAME))
        rows.append(_summarise(name, manifest, verdict))
    rows.sort(key=lambda r: r["backup_id"], reverse=True)
    return rows


# ── Verify ──────────────────────────────────────────────────────────────

def _check_sqlite_file(path: str) -> list:
    """Problems found opening `path` as SQLite. Empty list means healthy."""
    problems = []
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                problems.append("not_a_sqlite_file")
                return problems
    except OSError:
        problems.append("unreadable")
        return problems
    try:
        # Read-only URI: verification must never modify the artefact it is
        # judging, nor create a stray journal beside it.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        problems.append("integrity_check_failed")
        return problems
    if not rows or str(rows[0][0]).lower() != "ok":
        problems.append("integrity_check_failed")
    return problems


def verify(backup_id: str, actor: str = "") -> dict:
    """Re-read a set from disk and decide whether it could actually restore.

    Checks EVERY expected file: present, right size, checksum equal to the one
    the manifest recorded at creation, opens as SQLite, passes
    `PRAGMA integrity_check`. All checks run — the verdict lists every problem
    rather than stopping at the first, because an operator deciding which
    backup to trust needs the whole picture."""
    directory = set_dir(backup_id)
    if directory is None or not os.path.isdir(directory):
        applog.warning("backup", "backup.verify.failed",
                       "شناسهٔ نسخهٔ پشتیبان نامعتبر است",
                       actor=actor, target=str(backup_id)[:120], outcome="not_found")
        raise UnknownBackup(f"unknown backup id: {str(backup_id)[:60]}")

    started = time.monotonic()
    applog.info("backup", "backup.verify.started", "شروع بررسی سلامت نسخهٔ پشتیبان",
                actor=actor, target=backup_id, outcome="started")

    problems = []
    manifest = _read_json(os.path.join(directory, MANIFEST_NAME))
    if manifest is None:
        problems.append("manifest_missing_or_unreadable")
        entries = []
    else:
        entries = [e for e in manifest.get("files", []) if isinstance(e, dict)]

    seen_roles = set()
    for entry in entries:
        name = entry.get("name", "")
        role = entry.get("role", "")
        seen_roles.add(role)
        path = member_path(backup_id, name)
        if path is None:
            problems.append(f"{name or role or 'file'}:unexpected_name")
            continue
        if not os.path.isfile(path):
            problems.append(f"{name}:missing")
            continue
        try:
            actual_bytes = os.path.getsize(path)
        except OSError:
            problems.append(f"{name}:unreadable")
            continue
        if entry.get("bytes") is not None and actual_bytes != entry["bytes"]:
            problems.append(f"{name}:size_mismatch")
        try:
            if entry.get("sha256") and _sha256(path) != entry["sha256"]:
                problems.append(f"{name}:checksum_mismatch")
        except OSError:
            problems.append(f"{name}:unreadable")
            continue
        for issue in _check_sqlite_file(path):
            problems.append(f"{name}:{issue}")

    # A set that is missing a whole database is not restorable even if every
    # file it does list is perfect.
    for role in ROLE_FILES:
        if role not in seen_roles:
            problems.append(f"{ROLE_FILES[role]}:not_in_manifest")

    verdict = {
        "backup_id": backup_id,
        "ok": not problems,
        "problems": problems,
        "checked_at": _now_iso(),
        "checked_by": actor or "system",
    }
    _write_json(os.path.join(directory, VERDICT_NAME), verdict)

    duration_ms = int((time.monotonic() - started) * 1000)
    if verdict["ok"]:
        applog.info("backup", "backup.verify.completed", "نسخهٔ پشتیبان سالم است",
                    actor=actor, target=backup_id, outcome="ok",
                    duration_ms=duration_ms)
    else:
        applog.error("backup", "backup.verify.failed", "نسخهٔ پشتیبان سالم نیست",
                     actor=actor, target=backup_id, outcome="failed",
                     duration_ms=duration_ms, error_code="verify_failed",
                     metadata={"problems": problems})
    return verdict


# ── Delete ──────────────────────────────────────────────────────────────

def delete(backup_id: str, actor: str = "") -> dict:
    """Remove one set. Only the named directory is touched."""
    directory = set_dir(backup_id)
    if directory is None or not os.path.isdir(directory):
        raise UnknownBackup(f"unknown backup id: {str(backup_id)[:60]}")
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        applog.exception("backup", "backup.delete.failed", exc,
                         "حذف نسخهٔ پشتیبان ممکن نشد",
                         actor=actor, target=backup_id, outcome="error")
        applog.audit("admin.backup.delete", "حذف نسخهٔ پشتیبان",
                     actor=actor or "system", target=backup_id, outcome="error")
        raise BackupError(str(exc)) from exc

    applog.audit("admin.backup.delete", "حذف نسخهٔ پشتیبان",
                 actor=actor or "system", target=backup_id, outcome="ok")
    return {"backup_id": backup_id, "deleted": True}


def prune(keep: int = KEEP_SETS) -> list:
    """Delete all but the newest `keep` sets. Returns the removed ids."""
    rows = list_sets()
    doomed = rows if keep <= 0 else rows[keep:]
    removed = []
    for row in doomed:
        directory = set_dir(row["backup_id"])
        if not directory:
            continue
        try:
            shutil.rmtree(directory)
            removed.append(row["backup_id"])
        except OSError as exc:
            # Housekeeping must never fail the backup that triggered it.
            logger.warning("Could not prune backup set %s: %s",
                           row["backup_id"], type(exc).__name__)
    if removed:
        applog.info("backup", "backup.pruned", "نسخه‌های قدیمی حذف شدند",
                    outcome="ok", metadata={"removed": removed})
    return removed


# ── Restore ─────────────────────────────────────────────────────────────

def restore(backup_id: str, actor: str = "") -> dict:
    """Replace every live database from `backup_id`. See the module docstring
    for the two limits this cannot remove.

    Order is the safety property, and it is deliberate:

      1. re-verify the set NOW (a stored verdict is a memory, not a fact)
      2. write the audit row and REFUSE if it did not persist (fail-closed)
      3. take a safety backup of the CURRENT databases — it also captures the
         audit row from step 2, so the request survives the log restore
      4. restore every database; on any failure put ALL of them back from the
         safety set and report whether that rollback worked
    """
    directory = set_dir(backup_id)
    if directory is None or not os.path.isdir(directory):
        raise UnknownBackup(f"unknown backup id: {str(backup_id)[:60]}")

    started = time.monotonic()

    # 1. Never trust the stored flag — verify against the bytes on disk now.
    verdict = verify(backup_id, actor=actor)
    if not verdict["ok"]:
        applog.audit("admin.backup.restore.failed",
                     "بازگردانی رد شد: نسخهٔ پشتیبان سالم نیست",
                     actor=actor or "system", target=backup_id, outcome="refused",
                     level="critical", error_code="not_verified",
                     metadata={"problems": verdict["problems"]})
        raise BackupNotVerified(BackupNotVerified.fa)

    # 2. FAIL-CLOSED. applog never raises; it returns None when a row could not
    #    be persisted. Operational logging may degrade — destroying a live
    #    database with no audit trail may not. `critical` rather than the
    #    default `notice` for two reasons: overwriting a live database IS
    #    critical, and it keeps the row above any severity floor an operator
    #    has raised, so this gate blocks real logging failures, not settings.
    audit_id = applog.audit(
        "admin.backup.restore.requested", "درخواست بازگردانی نسخهٔ پشتیبان",
        actor=actor or "system", target=backup_id, outcome="pending",
        level="critical", metadata={"created_at": verdict["checked_at"]})
    if not audit_id:
        logger.error("Restore of %s refused: audit row could not be persisted",
                     backup_id)
        raise AuditUnavailable(AuditUnavailable.fa)

    # 3. The undo. Taken AFTER the audit row so the safety set contains it.
    try:
        safety = create(actor=actor or "system", kind=KIND_SAFETY)
    except Exception as exc:
        applog.audit("admin.backup.restore.failed",
                     "بازگردانی انجام نشد: تهیهٔ نسخهٔ ایمنی ممکن نبود",
                     actor=actor or "system", target=backup_id, outcome="failed",
                     level="critical", error_type=type(exc).__name__)
        raise RestoreFailed("safety backup failed") from exc
    safety_id = safety["backup_id"]

    # 4. Restore every database as one set.
    restored = []
    try:
        for role, name, live in _live_targets():
            source = member_path(backup_id, name)
            if source is None or not os.path.isfile(source):
                raise RestoreFailed(f"missing member {name}")
            backup_db.copy_database(source, live)
            restored.append(role)
    except Exception as exc:
        rolled_back = _rollback(safety_id)
        applog.ensure_tables()
        applog.audit(
            "admin.backup.restore.failed", "بازگردانی ناموفق بود",
            actor=actor or "system", target=backup_id, outcome="failed",
            level="critical", error_type=type(exc).__name__,
            duration_ms=int((time.monotonic() - started) * 1000),
            metadata={"restored_before_failure": restored,
                      "safety_backup_id": safety_id,
                      "rolled_back": rolled_back})
        message = RestoreFailed.fa + (
            " اطلاعات قبلی برگردانده شد." if rolled_back
            else " هشدار: بازگرداندن وضعیت قبلی هم ناموفق بود؛"
                 f" نسخهٔ ایمنی با شناسهٔ {safety_id} روی سرور موجود است.")
        raise RestoreFailed(message) from exc

    # The log database was just replaced by an older copy; make sure its tables
    # exist before the completion row is written into it.
    applog.ensure_tables()
    duration_ms = int((time.monotonic() - started) * 1000)
    applog.audit("admin.backup.restore.completed", "بازگردانی نسخهٔ پشتیبان انجام شد",
                 actor=actor or "system", target=backup_id, outcome="ok",
                 level="critical", duration_ms=duration_ms,
                 metadata={"safety_backup_id": safety_id, "restored": restored})
    applog.info("backup", "backup.restore.completed", "بازگردانی کامل شد",
                actor=actor, target=backup_id, outcome="ok",
                duration_ms=duration_ms)

    return {
        "backup_id": backup_id,
        "safety_backup_id": safety_id,
        "restored": restored,
        "duration_ms": duration_ms,
        # Honest, not decorative: connections opened before the swap (this
        # process and every other worker) are not closed by us.
        "restart_recommended": True,
    }


def _rollback(safety_id: str) -> bool:
    """Put every live database back from the safety set. True if all succeeded."""
    ok = True
    for role, name, live in _live_targets():
        source = member_path(safety_id, name)
        try:
            if source is None or not os.path.isfile(source):
                ok = False
                continue
            backup_db.copy_database(source, live)
        except Exception as exc:  # noqa: BLE001 — try every file, report the truth
            logger.error("Rollback of %s from %s failed: %s",
                         role, safety_id, type(exc).__name__)
            ok = False
    return ok
