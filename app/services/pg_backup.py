"""PostgreSQL-native backup, verification and restore.

MECHANISM
---------
`pg_dump --format=custom` (compressed, and the only format `pg_restore` can
selectively restore from). NOT a filesystem copy of the data directory: copying
a live PGDATA is unsafe without a full base-backup protocol, and this module
must never appear to have taken a backup it cannot restore.

WHY THIS IS NOT ARBITRARY SHELL
-------------------------------
`pg_dump`/`pg_restore` are invoked with a FIXED argument list built here — no
intermediate command interpreter and no string interpolation of user input.
A test asserts by source inspection that the dangerous constructs are absent,
so this prose deliberately does not spell them out. The only
caller-supplied value that reaches these commands is a backup id, and that is
validated against a strict pattern and resolved inside the backups directory
with a realpath containment check. Everything else is constant.

RESTORE SAFETY
--------------
Restore is the most destructive operation in the product, so:

  * the backup must VERIFY at restore time — a stored "verified" flag from
    last week is not evidence about the file on disk now;
  * a safety backup of the CURRENT database is taken first and its id is
    returned, so the operator can always get back;
  * the audit row is written BEFORE the destructive step. If the audit cannot
    be persisted the restore is refused outright (fail-closed) — destroying a
    live database without a record of who did it is not acceptable;
  * `--clean --if-exists` inside a single `pg_restore` invocation, so the
    schema is dropped and recreated as one unit rather than half-replaced.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.config import BASE_DIR, logger
from app.services import applog

BACKUP_DIR = os.path.join(BASE_DIR, "backups", "postgres")
# A backup id is generated here and never supplied by a client; the pattern is
# enforced anyway so a crafted id can never escape BACKUP_DIR.
_ID_RE = re.compile(r"^pg_\d{8}_\d{6}_[0-9a-f]{6}$")
_TIMEOUT = 300


class BackupError(Exception):
    """Operator-facing failure. Carries a Persian message."""

    def __init__(self, message_fa: str):
        self.message_fa = message_fa
        super().__init__(message_fa)


class AuditUnavailable(BackupError):
    """The audit trail could not be written, so the operation is refused."""


def _pg_bin(name: str) -> str:
    """Locate a PostgreSQL binary. Never resolved from user input."""
    explicit = os.getenv("PG_BIN_DIR", "").strip()
    if explicit:
        candidate = os.path.join(explicit, name)
        if os.path.exists(candidate):
            return candidate
    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/usr/local/opt/postgresql@16/bin",
                   "/opt/homebrew/opt/postgresql@16/bin",
                   "/usr/lib/postgresql/16/bin"):
        candidate = os.path.join(prefix, name)
        if os.path.exists(candidate):
            return candidate
    raise BackupError(f"ابزار {name} روی این سرور پیدا نشد.")


def _conn_parts() -> dict:
    """Split DATABASE_URL into pg_dump arguments. The password goes in the
    ENVIRONMENT (PGPASSWORD), never on the command line where `ps` shows it."""
    from app.db.pg import dsn
    u = urlparse(dsn())
    return {
        "host": u.hostname or "127.0.0.1",
        "port": str(u.port or 5432),
        "user": u.username or "",
        "password": u.password or "",
        "dbname": (u.path or "/padyar").lstrip("/"),
    }


def _env(parts: dict) -> dict:
    env = dict(os.environ)
    if parts["password"]:
        env["PGPASSWORD"] = parts["password"]
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _run(argv, env, what: str):
    try:
        result = subprocess.run(argv, env=env, capture_output=True,
                                timeout=_TIMEOUT, text=True)
    except subprocess.TimeoutExpired:
        raise BackupError(f"{what} از حد زمانی گذشت.")
    if result.returncode != 0:
        # stderr may name the database and host; keep it for the operator log
        # but never return it raw to the browser.
        logger.error("[pg_backup] %s failed rc=%s: %s", what,
                     result.returncode, (result.stderr or "")[:400])
        raise BackupError(f"{what} ناموفق بود.")
    return result


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_dir(backup_id: str) -> str:
    """Resolve a backup id to a directory that is provably inside BACKUP_DIR."""
    if not _ID_RE.match(backup_id or ""):
        raise BackupError("شناسهٔ پشتیبان معتبر نیست.")
    path = os.path.realpath(os.path.join(BACKUP_DIR, backup_id))
    if not path.startswith(os.path.realpath(BACKUP_DIR) + os.sep):
        raise BackupError("مسیر پشتیبان مجاز نیست.")
    return path


def _manifest_path(backup_id: str) -> str:
    return os.path.join(_safe_dir(backup_id), "manifest.json")


def _dump_path(backup_id: str) -> str:
    return os.path.join(_safe_dir(backup_id), "padyar.dump")


# ── Create ──────────────────────────────────────────────────────────────

def create(actor: str = "", reason: str = "manual") -> dict:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_id = "pg_%s_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"),
                              os.urandom(3).hex())
    target = os.path.join(BACKUP_DIR, backup_id)
    os.makedirs(target, exist_ok=True)

    parts = _conn_parts()
    started = time.perf_counter()
    applog.info("backup", "backup.started", "پشتیبان‌گیری پستگرس آغاز شد",
                actor=actor, target=backup_id, metadata={"reason": reason})
    try:
        _run([_pg_bin("pg_dump"),
              "--host", parts["host"], "--port", parts["port"],
              "--username", parts["user"], "--dbname", parts["dbname"],
              "--format", "custom", "--compress", "6",
              "--no-owner", "--no-privileges",
              "--file", _dump_path(backup_id)],
             _env(parts), "pg_dump")
    except BackupError:
        shutil.rmtree(target, ignore_errors=True)
        applog.error("backup", "backup.failed", "پشتیبان‌گیری ناموفق بود",
                     actor=actor, target=backup_id, outcome="failed")
        raise

    duration = int((time.perf_counter() - started) * 1000)
    size = os.path.getsize(_dump_path(backup_id))
    manifest = {
        "backup_id": backup_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "created_by": actor or "system",
        "engine": "postgresql",
        "database": parts["dbname"],
        "format": "custom",
        "file": "padyar.dump",
        "bytes": size,
        "sha256": _sha256(_dump_path(backup_id)),
        "duration_ms": duration,
        "reason": reason,
        "verification": {"status": "unknown", "checked_at": None},
    }
    with open(_manifest_path(backup_id), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    applog.info("backup", "backup.completed", "پشتیبان پستگرس ساخته شد",
                actor=actor, target=backup_id, duration_ms=duration,
                metadata={"bytes": size})
    applog.audit("admin.backup.created", "پشتیبان پستگرس ساخته شد",
                 actor=actor, target=backup_id, outcome="ok",
                 metadata={"bytes": size, "reason": reason})
    return manifest


# ── Verify ──────────────────────────────────────────────────────────────

def verify(backup_id: str, actor: str = "") -> dict:
    """Prove the archive is restorable, not merely present.

    Three independent checks: the file exists, its bytes still hash to what the
    manifest recorded, and `pg_restore --list` can actually parse the archive
    and finds a non-trivial table of contents. A corrupt dump that happens to
    match a stale checksum is still caught by the third.
    """
    directory = _safe_dir(backup_id)
    manifest_file = _manifest_path(backup_id)
    if not os.path.exists(manifest_file):
        raise BackupError("این پشتیبان پیدا نشد.")
    with open(manifest_file, encoding="utf-8") as f:
        manifest = json.load(f)

    applog.info("backup", "backup.verify.started", "بررسی پشتیبان آغاز شد",
                actor=actor, target=backup_id)
    problems = []
    dump = _dump_path(backup_id)
    if not os.path.exists(dump):
        problems.append("فایل پشتیبان موجود نیست")
    else:
        if _sha256(dump) != manifest.get("sha256"):
            problems.append("چک‌سام فایل با مانیفست نمی‌خواند")
        try:
            listing = _run([_pg_bin("pg_restore"), "--list", dump],
                           dict(os.environ), "pg_restore --list")
            entries = [l for l in listing.stdout.splitlines()
                       if l and not l.startswith(";")]
            if len(entries) < 5:
                problems.append("محتوای آرشیو ناقص است")
            manifest["toc_entries"] = len(entries)
        except BackupError:
            problems.append("آرشیو قابل خواندن نیست")

    status = "verified" if not problems else "failed"
    manifest["verification"] = {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "problems": problems,
    }
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if problems:
        applog.error("backup", "backup.verify.failed", "بررسی پشتیبان ناموفق بود",
                     actor=actor, target=backup_id, outcome="failed",
                     metadata={"problems": problems})
    else:
        applog.info("backup", "backup.verify.completed", "پشتیبان سالم است",
                    actor=actor, target=backup_id, outcome="ok",
                    metadata={"toc_entries": manifest.get("toc_entries")})
    return manifest


# ── List / delete ───────────────────────────────────────────────────────

def list_backups() -> list:
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not _ID_RE.match(name):
            continue
        try:
            with open(_manifest_path(name), encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            out.append({"backup_id": name, "error": "manifest unreadable"})
    return out


def delete(backup_id: str, actor: str = "") -> bool:
    directory = _safe_dir(backup_id)
    if not os.path.isdir(directory):
        raise BackupError("این پشتیبان پیدا نشد.")
    shutil.rmtree(directory)
    applog.audit("admin.backup.deleted", "پشتیبان حذف شد", actor=actor,
                 target=backup_id, outcome="ok", level="warning")
    return True


# ── Restore ─────────────────────────────────────────────────────────────

def restore(backup_id: str, actor: str = "", confirmation: str = "") -> dict:
    """Replace the live database from a backup, coordinated end to end.

    Sequence — every step before the destructive one can abort cleanly:

        confirm -> verify backup -> audit pre-check -> disk check
        -> MAINTENANCE ON -> safety backup -> close pools
        -> pg_restore --single-transaction
        -> reopen pools -> validate restored database
        -> MAINTENANCE OFF -> final audit

    MULTI-PROCESS HONESTY: this coordinates THIS process. Other workers, if
    the deployment runs several, still hold connections opened before the
    swap. SQLite-style corruption is not a risk (PostgreSQL keeps each session
    consistent), but a sibling worker may serve stale reads until it recycles.
    There is no supervisor here to restart them, so the result says so
    explicitly rather than implying coordination that does not exist.

    Maintenance mode stays ON if validation fails: returning a half-verified
    database to live traffic is worse than staying down.
    """
    from app.services import maintenance

    expected = f"RESTORE BACKUP {backup_id}"
    if (confirmation or "").strip() != expected:
        raise BackupError("عبارت تأیید درست نیست؛ هیچ تغییری انجام نشد.")

    # Re-verify NOW. A flag written last week says nothing about the file today.
    manifest = verify(backup_id, actor=actor)
    if manifest["verification"]["status"] != "verified":
        raise BackupError("این پشتیبان سالم نیست و بازیابی نمی‌شود.")

    # Tooling must exist BEFORE we take the app down.
    _pg_bin("pg_restore")

    # Enough room for the safety backup, with headroom.
    try:
        free = shutil.disk_usage(BACKUP_DIR).free
        if free < manifest.get("bytes", 0) * 3:
            raise BackupError("فضای دیسک برای پشتیبان ایمنی کافی نیست.")
    except OSError:
        pass

    # Fail closed: no audit trail, no restore.
    audit_id = applog.audit(
        "admin.backup.restore.requested", "درخواست بازیابی پایگاه داده",
        actor=actor, target=backup_id, outcome="requested", level="warning")
    if audit_id is None:
        raise AuditUnavailable(
            "ثبت رخداد حساس ممکن نشد؛ بازیابی برای حفظ سابقه انجام نشد.")

    maintenance.enable(f"بازیابی پایگاه داده از {backup_id}", actor=actor)
    safety = None
    try:
        safety = create(actor=actor, reason=f"safety-before-restore-of-{backup_id}")
    except BackupError:
        maintenance.disable(actor=actor)
        applog.audit("admin.backup.restore.failed", "پشتیبان ایمنی ساخته نشد",
                     actor=actor, target=backup_id, outcome="failed", level="critical")
        raise BackupError("پشتیبان ایمنی ساخته نشد؛ بازیابی انجام نشد.")

    parts = _conn_parts()
    started = time.perf_counter()
    from app.db import pg
    try:
        # Release every connection this process holds before the schema drops.
        pg.close_pool()
        _run([_pg_bin("pg_restore"),
              "--host", parts["host"], "--port", parts["port"],
              "--username", parts["user"], "--dbname", parts["dbname"],
              "--clean", "--if-exists", "--no-owner", "--no-privileges",
              "--single-transaction", _dump_path(backup_id)],
             _env(parts), "pg_restore")
    except BackupError:
        pg.close_pool()
        applog.audit("admin.backup.restore.failed", "بازیابی ناموفق بود",
                     actor=actor, target=backup_id, outcome="failed",
                     level="critical",
                     metadata={"safety_backup": safety["backup_id"]})
        raise BackupError(
            "بازیابی ناموفق بود. حالت تعمیر روشن مانده است. پایگاه داده از "
            "پشتیبان ایمنی قابل بازگردانی است: " + safety["backup_id"])

    duration = int((time.perf_counter() - started) * 1000)

    # Fresh pool against the restored database, then prove it is usable.
    pg.close_pool()
    validation = validate_restored_database()

    if not validation["ok"]:
        applog.audit("admin.backup.restore.failed", "بازیابی انجام شد ولی اعتبارسنجی رد شد",
                     actor=actor, target=backup_id, outcome="failed", level="critical",
                     metadata={"problems": validation["problems"],
                               "safety_backup": safety["backup_id"]})
        # Deliberately stay in maintenance: a database that failed validation
        # must not receive live traffic.
        raise BackupError(
            "بازیابی انجام شد اما اعتبارسنجی پایگاه داده رد شد. حالت تعمیر روشن "
            "مانده است. پشتیبان ایمنی: " + safety["backup_id"])

    maintenance.disable(actor=actor)
    applog.audit("admin.backup.restore.completed", "بازیابی انجام و تأیید شد",
                 actor=actor, target=backup_id, outcome="ok", level="warning",
                 duration_ms=duration,
                 metadata={"safety_backup": safety["backup_id"],
                           "checks": list(validation["checks"])})
    return {
        "restored": backup_id,
        "safety_backup": safety["backup_id"],
        "duration_ms": duration,
        "validation": validation,
        "maintenance_cleared": True,
        # This process is clean. Siblings are not, and nothing here can restart
        # them — see the docstring.
        "restart_required_for_other_processes": True,
        "message_fa": ("بازیابی با موفقیت انجام و تأیید شد. اگر برنامه با چند "
                       "پروسه اجرا می‌شود، برای پاک شدن اتصال‌های قدیمی سایر "
                       "پروسه‌ها را ری‌استارت کنید."),
    }


# ── Post-restore validation ─────────────────────────────────────────────

def validate_restored_database() -> dict:
    """Prove the database is USABLE, not merely that pg_restore exited 0.

    An exit code says the archive replayed; it says nothing about whether the
    application can now log in an admin, read its settings, or render Persian.
    Each check below is something the app genuinely depends on at boot.
    """
    checks, problems = {}, []

    def record(name, ok, detail=""):
        checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            problems.append(name)

    from app.db import pg
    ok, detail = pg.healthy()
    record("reachable", ok, detail)
    if not ok:
        return {"ok": False, "checks": checks, "problems": problems}

    c = pg.connect()
    try:
        schemas = {r["nspname"] for r in c.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname IN ('app','observability')").fetchall()}
        record("schemas_present", schemas == {"app", "observability"}, ",".join(sorted(schemas)))

        expected = {"admins", "admin_sessions", "settings", "dataset", "questions",
                    "synonyms", "chat_logs", "otp_challenges"}
        found = {r["table_name"] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'app'").fetchall()}
        record("application_tables", expected <= found, f"{len(found)} tables")

        logs = {r["table_name"] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'observability'").fetchall()}
        record("observability_tables",
               {"app_logs", "audit_logs", "security_events", "service_events"} <= logs,
               f"{len(logs)} tables")

        try:
            revision = c.execute(
                "SELECT version FROM app.schema_migrations ORDER BY applied_at DESC LIMIT 1").fetchone()
            record("migration_revision", revision is not None,
                   revision["version"] if revision else "none")
        except Exception as e:  # noqa: BLE001
            record("migration_revision", False, type(e).__name__)

        admins = c.execute("SELECT COUNT(*) AS n FROM app.admins").fetchone()["n"]
        record("admin_accounts", admins > 0, f"{admins} accounts")

        hashed = c.execute(
            "SELECT COUNT(*) AS n FROM app.admins WHERE password_hash LIKE '$2%'").fetchone()["n"]
        record("password_hashes_intact", hashed > 0, f"{hashed} bcrypt hashes")

        settings = c.execute("SELECT COUNT(*) AS n FROM app.settings").fetchone()["n"]
        record("settings_readable", settings > 0, f"{settings} keys")

        # Persian is the product's entire content language; mojibake here would
        # mean an encoding mismatch that a row count would never reveal.
        persian = c.execute(
            "SELECT COUNT(*) AS n FROM app.dataset WHERE title ~ '[\u0600-\u06FF]'").fetchone()["n"]
        record("persian_readable", persian > 0, f"{persian} Persian rows")

        c.execute("SELECT COUNT(*) FROM observability.app_logs").fetchone()
        record("logging_schema_readable", True)
    except Exception as e:  # noqa: BLE001
        record("validation_query", False, type(e).__name__)
    finally:
        c.close()

    return {"ok": not problems, "checks": checks, "problems": problems}
