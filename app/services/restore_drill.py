"""Restore drill: prove a backup brings a database back, not only that it reads.

WHY THIS EXISTS
---------------
`pg_backup.verify()` checks the archive is readable. It never builds a
database from it. A backup nobody has restored is a hope, not a backup. The
drill restores one backup into a separate database and compares the result with
numbers recorded at dump time (row counts and applied migrations, both read in
the snapshot pg_dump used, see pg_backup._open_snapshot).

THE DRILL DATABASE
------------------
`<live name>_drill`, same host, port and role as DATABASE_URL. The name is
derived from the live name and never taken from input, so a request can not
point the drill at another database. The app role has no CREATEDB, so
deploy/05-create-databases.sh creates the empty database ahead of time. When it
is missing the drill records "skipped". It does not crash and it does not
report "failed", because nothing is wrong with the backup.

Every destructive statement here runs only after `current_database()` has been
checked against the derived drill name. The live database is never dropped
from, restored into or cleaned.

RESULT
------
One `drill` block in the backup's manifest.json. `_finish()` is the only place
that block is written and the only place the outcome is logged.
"""
import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone

from app.config import logger
from app.services import applog, pg_backup

# One drill at a time per install. This is a PostgreSQL session advisory lock on
# a dedicated connection to the live database, not a threading.Lock. The app
# runs several workers, so a threading.Lock would allow one drill per worker. An
# advisory lock is per database, works across processes, and the server frees it
# if the process dies. Advisory locks belong to one database, so two installs on
# one cluster do not block each other. The value is the ASCII bytes "PADYRDRI".
_LOCK_KEY = 0x5041445952445249

# Free space needed before a restore: the LIVE database size times this factor,
# plus a fixed margin. The spike restored a database about 15x the size of its
# dump file, so the panel's "3x the dump file" check is far too small for a
# restore. The live size is the honest estimate of what the restore will write.
_DISK_FACTOR = 1.2
_DISK_MARGIN = 512 * 1024 * 1024

_MB = 1024 * 1024


class DrillAlreadyRunning(Exception):
    """Another drill holds the lock for this database."""


class DrillRefused(Exception):
    """The drill would not be safe to run. Carries a Persian message."""

    def __init__(self, message_fa: str):
        self.message_fa = message_fa
        super().__init__(message_fa)


# ── Names ───────────────────────────────────────────────────────────────

def drill_db_name(live_name: str) -> str:
    """`<live_name>_drill`, or DrillRefused when that is not safe.

    PostgreSQL cuts identifiers at 63 bytes. A longer derived name would be
    cut, and the cut name could be the live database or another one. Bytes are
    counted, not characters: a Persian name takes two bytes per letter.
    """
    if not live_name:
        raise DrillRefused("نام پایگاه داده خالی است؛ تمرین بازیابی انجام نشد.")
    if live_name.endswith("_drill"):
        raise DrillRefused("پایگاه دادهٔ اصلی خودش «_drill» است؛ تمرین بازیابی "
                           "انجام نشد تا به آن آسیبی نرسد.")
    derived = live_name + "_drill"
    if derived == live_name:
        raise DrillRefused("نام پایگاه دادهٔ تمرین با پایگاه دادهٔ اصلی یکی است؛ "
                           "تمرین بازیابی انجام نشد.")
    if len(derived.encode("utf-8")) > 63:
        raise DrillRefused("نام پایگاه داده برای ساخت نام تمرین بیش از حد بلند "
                           "است؛ تمرین بازیابی انجام نشد.")
    return derived


# ── Lock ────────────────────────────────────────────────────────────────

def _take_lock():
    """The lock connection, or None when the live database can not be reached.

    None is not an error here. The drill itself needs the live database on its
    next step and records a failed result with a reason, which is better for
    the operator than an exception from the nightly scheduler.
    Raises DrillAlreadyRunning when another session holds the lock.
    """
    try:
        conn = pg_backup._connect(pg_backup._conn_parts(),
                                  "padyar-restore-drill-lock", "")
    except Exception as e:  # noqa: BLE001
        logger.warning("[restore_drill] lock connection failed: %s",
                       type(e).__name__)
        return None
    try:
        got = conn.execute("SELECT pg_try_advisory_lock(%s)",
                           (_LOCK_KEY,)).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("[restore_drill] lock query failed: %s", type(e).__name__)
        _close(conn)
        return None
    if not got:
        _close(conn)
        raise DrillAlreadyRunning("یک تمرین بازیابی از قبل در حال اجراست.")
    return conn


def _release_lock(conn) -> None:
    if conn is None:
        return
    try:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    except Exception:  # noqa: BLE001  (closing the session frees it anyway)
        pass
    _close(conn)


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


# ── Public entry points ─────────────────────────────────────────────────

def _load(backup_id: str) -> dict:
    """The backup's manifest. BackupError when the id or the files are wrong.

    The id goes through pg_backup._safe_dir, so a crafted id can not leave the
    backups folder. No drill block is written for a bad id.
    """
    manifest_file = pg_backup._manifest_path(backup_id)
    if not os.path.isfile(manifest_file):
        raise pg_backup.BackupError("این پشتیبان پیدا نشد.")
    if not os.path.isfile(pg_backup._dump_path(backup_id)):
        raise pg_backup.BackupError("فایل پشتیبان موجود نیست.")
    try:
        with open(manifest_file, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        raise pg_backup.BackupError("مانیفست این پشتیبان خوانده نشد.")
    if not isinstance(manifest, dict):
        raise pg_backup.BackupError("مانیفست این پشتیبان خوانده نشد.")
    return manifest


def run(backup_id: str, actor: str = "system") -> dict:
    """Run one drill now and return the `drill` block it wrote.

    Raises pg_backup.BackupError for a bad id and DrillAlreadyRunning when
    another drill holds the lock. Any other problem is a result in the block.
    """
    manifest = _load(backup_id)
    lock = _take_lock()
    return _execute(backup_id, manifest, actor, lock)


def start(backup_id: str, actor: str) -> threading.Thread:
    """Start a drill in the background, for the admin button.

    The id check and the lock happen in the caller's thread, so the HTTP
    answer can say "already running" at once. The thread keeps the lock and
    releases it when the drill ends. The thread is returned so a test can join.
    """
    manifest = _load(backup_id)
    lock = _take_lock()
    thread = threading.Thread(
        target=_in_thread, args=(backup_id, manifest, actor, lock),
        name="restore-drill", daemon=True)
    try:
        thread.start()
    except BaseException:
        _release_lock(lock)
        raise
    return thread


def _in_thread(backup_id, manifest, actor, lock) -> None:
    try:
        _execute(backup_id, manifest, actor, lock)
    except Exception as e:  # noqa: BLE001  (a thread has nobody to raise to)
        logger.error("[restore_drill] drill thread ended with %s",
                     type(e).__name__)


def latest_drill(manifests) -> "dict | None":
    """The newest `drill` block among `manifests`, with its `backup_id` added.

    Old manifests without `drill` are skipped. Junk is skipped too: this feeds
    the admin page, and one broken manifest must not hide the others.
    """
    if not isinstance(manifests, (list, tuple)):
        return None
    best, best_at = None, None
    for manifest in manifests:
        if not isinstance(manifest, dict):
            continue
        block = manifest.get("drill")
        if not isinstance(block, dict):
            continue
        moment = _parse_time(block.get("checked_at"))
        if moment is None:
            continue
        if best_at is None or moment > best_at:
            best, best_at = {**block, "backup_id": manifest.get("backup_id")}, moment
    return best


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


# ── One drill ───────────────────────────────────────────────────────────

def _new_block(actor: str) -> dict:
    return {
        "status": None,
        "checked_at": None,
        "duration_ms": 0,
        "restore_duration_ms": None,
        "tables_checked": 0,
        "mismatches": [],
        "reason": "",
        # "passed", "failed" or "skipped" for each of the three checks.
        "checks": {"row_counts": "skipped", "schema_migrations": "skipped",
                   "validation": "skipped"},
        # "<schema>.<table>": {"expected", "actual"}, for the admin detail view.
        "tables": {},
        # Short English notes on failed checks other than row counts.
        "problems": [],
        "cleanup_ok": True,
        "drill_database": None,
        "actor": actor or "system",
    }


def _execute(backup_id: str, manifest: dict, actor: str, lock) -> dict:
    """Run the drill, write its block, release the lock. Never raises."""
    started = time.perf_counter()
    block = _new_block(actor)
    try:
        applog.info("backup", "backup.drill.started",
                    "تمرین بازیابی پشتیبان آغاز شد",
                    actor=actor, target=backup_id)
        try:
            _drill(backup_id, manifest, block)
        except Exception as e:  # noqa: BLE001  (a drill must not crash its caller)
            logger.error("[restore_drill] unexpected %s for %s",
                         type(e).__name__, backup_id)
            applog.exception("backup", "backup.drill.error", e,
                             "تمرین بازیابی با خطای غیرمنتظره روبه‌رو شد",
                             actor=actor, target=backup_id)
            block["status"] = "failed"
            block["reason"] = ("تمرین بازیابی به دلیل یک خطای غیرمنتظره ناموفق "
                               "بود. جزئیات در گزارش‌ها ثبت شده است.")
        block["duration_ms"] = int((time.perf_counter() - started) * 1000)
        block["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _finish(pg_backup._manifest_path(backup_id), backup_id, block)
    finally:
        _release_lock(lock)
    return block


def _skip(block: dict, reason: str) -> None:
    block["status"] = "skipped"
    block["reason"] = reason


def _drill(backup_id: str, manifest: dict, block: dict) -> None:
    """Fill `block` for one backup. May raise; _execute turns that into failed."""
    parts = pg_backup._conn_parts()
    try:
        drill_name = drill_db_name(parts["dbname"])
    except DrillRefused as e:
        _skip(block, e.message_fa)
        return
    block["drill_database"] = drill_name

    live = pg_backup._connect(parts, "padyar-restore-drill", "")
    try:
        exists = live.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                              (drill_name,)).fetchone()
        live_size = live.execute(
            "SELECT pg_database_size(current_database())").fetchone()[0]
    finally:
        _close(live)
    if not exists:
        _skip(block, "پایگاه دادهٔ تمرین وجود ندارد (drill database missing). "
                     "deploy/05-create-databases.sh را دوباره اجرا کنید.")
        return

    need = int(live_size * _DISK_FACTOR) + _DISK_MARGIN
    free = _free_bytes()
    if free < need:
        _skip(block, f"فضای خالی دیسک برای تمرین کافی نیست: {free // _MB} MB "
                     f"آزاد است و {need // _MB} MB لازم است.")
        return

    drill_parts = {**parts, "dbname": drill_name}
    # A drill that crashed earlier may have left schemas behind.
    conn = _connect_drill(drill_parts)
    try:
        _drop_app_schemas(conn, drill_name)
    finally:
        _close(conn)

    try:
        _restore_and_check(backup_id, manifest, block, parts, drill_parts)
    finally:
        # Success or failure, the drill database ends empty (the restored
        # copy holds every visitor and admin record, and nobody needs it).
        try:
            conn = _connect_drill(drill_parts)
            try:
                _drop_app_schemas(conn, drill_name)
            finally:
                _close(conn)
        except Exception as e:  # noqa: BLE001  (see below)
            # Never raised: the checks already have an answer, and a failed
            # cleanup is reported in the block for the operator to act on.
            block["cleanup_ok"] = False
            logger.error("[restore_drill] cleanup of %s failed: %s", drill_name,
                         type(e).__name__)
    if not block["cleanup_ok"]:
        block["reason"] += " پاک‌سازی پایگاه دادهٔ تمرین کامل نشد."


def _restore_and_check(backup_id, manifest, block, parts, drill_parts) -> None:
    drill_name = drill_parts["dbname"]
    started = time.perf_counter()
    try:
        pg_backup._run(
            [pg_backup._pg_bin("pg_restore"),
             "--host", parts["host"], "--port", parts["port"],
             "--username", parts["user"], "--dbname", drill_name,
             "--clean", "--if-exists", "--no-owner", "--no-privileges",
             "--single-transaction", pg_backup._dump_path(backup_id)],
            pg_backup._env(parts), "pg_restore (drill)",
            timeout=pg_backup._RESTORE_TIMEOUT)
    except pg_backup.BackupError:
        block["restore_duration_ms"] = int((time.perf_counter() - started) * 1000)
        block["status"] = "failed"
        block["reason"] = ("بازیابی پشتیبان در پایگاه دادهٔ تمرین ناموفق بود. "
                           "فایل پشتیبان احتمالاً خراب است.")
        return
    block["restore_duration_ms"] = int((time.perf_counter() - started) * 1000)

    conn = _connect_drill(drill_parts)
    try:
        _check_row_counts(conn, manifest.get("row_counts"), block)
        _check_schema_migrations(conn, manifest.get("schema_migrations"), block)
        result = pg_backup.validate_restored_database(conn=conn)
        if result["ok"]:
            block["checks"]["validation"] = "passed"
        else:
            block["checks"]["validation"] = "failed"
            block["problems"].append("validation: " + ", ".join(result["problems"]))
    finally:
        _close(conn)
    _conclude(block)


# ── Checks ──────────────────────────────────────────────────────────────

def _check_row_counts(conn, expected, block) -> None:
    from psycopg import errors, sql
    if not isinstance(expected, dict):
        return  # null: the count step failed at backup time. Stay "skipped".
    for key, want in expected.items():
        schema, _, table = str(key).partition(".")
        actual = None
        if schema and table:
            try:
                actual = conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(table))
                ).fetchone()["n"]
            except (errors.UndefinedTable, errors.InvalidSchemaName):
                actual = None  # the table did not come back
        block["tables"][key] = {"expected": want, "actual": actual}
        if actual != want:
            block["mismatches"].append(
                {"table": key, "expected": want, "actual": actual})
    block["tables_checked"] = len(expected)
    block["checks"]["row_counts"] = "failed" if block["mismatches"] else "passed"


def _check_schema_migrations(conn, expected, block) -> None:
    from psycopg import errors
    if not isinstance(expected, list) or not all(
            isinstance(m, dict) for m in expected):
        return  # an older backup, or counting failed. Stay "skipped".
    want = {(m.get("version"), m.get("checksum")) for m in expected}
    try:
        rows = conn.execute(
            "SELECT version, checksum FROM app.schema_migrations").fetchall()
    except (errors.UndefinedTable, errors.InvalidSchemaName):
        block["checks"]["schema_migrations"] = "failed"
        block["problems"].append("schema_migrations: table missing after restore")
        return
    got = {(r["version"], r["checksum"]) for r in rows}
    if got == want and len(rows) == len(expected):
        block["checks"]["schema_migrations"] = "passed"
        return
    lost = sorted(v for v, _ in want - got)[:3]
    extra = sorted(v for v, _ in got - want)[:3]
    block["checks"]["schema_migrations"] = "failed"
    block["problems"].append(
        f"schema_migrations: expected {len(expected)} rows, found {len(rows)}; "
        f"missing or changed {lost}, unexpected {extra}")


_CHECK_LABELS = {
    "row_counts": "تعداد ردیف‌ها",
    "schema_migrations": "نسخه‌های مایگریشن",
    "validation": "اعتبارسنجی پایگاه داده",
}


def _conclude(block: dict) -> None:
    failed = [k for k, v in block["checks"].items() if v == "failed"]
    skipped = [k for k, v in block["checks"].items() if v == "skipped"]
    if not failed:
        block["status"] = "passed"
        block["reason"] = ("تمرین بازیابی موفق بود: پشتیبان در پایگاه دادهٔ تمرین "
                           "برگشت و همهٔ بررسی‌ها درست بود.")
        if skipped:
            block["reason"] += (" چند بررسی انجام نشد چون پشتیبان اطلاعات آن را "
                                "نداشت: " + "، ".join(_CHECK_LABELS[k] for k in skipped) + ".")
        return
    block["status"] = "failed"
    block["reason"] = ("تمرین بازیابی ناموفق بود. بررسی‌های رد شده: "
                       + "، ".join(_CHECK_LABELS[k] for k in failed) + ".")
    if "row_counts" in failed:
        block["reason"] += (f" {len(block['mismatches'])} جدول با شمارش زمان "
                            "پشتیبان نمی‌خواند. این اختلاف ممکن است از DDL "
                            "(تغییر ساختار جدول) هم بیاید که هنگام پشتیبان‌گیری "
                            "اجرا شده است، چون شمارش‌ها پیش از قفل شدن جدول‌ها "
                            "توسط pg_dump گرفته می‌شوند.")


# ── Database access ─────────────────────────────────────────────────────

def _connect_drill(parts: dict):
    """Autocommit connection to the DRILL database with dict rows.

    statement_timeout is raised to the restore timeout. The app role has 30 s
    (deploy/05-create-databases.sh), and a count on a big restored table, or
    dropping its schema, must not die at 30 s.
    """
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(
        host=parts["host"], port=parts["port"], user=parts["user"],
        password=parts["password"] or None, dbname=parts["dbname"],
        connect_timeout=10, autocommit=True, row_factory=dict_row,
        application_name="padyar-restore-drill",
        options=f"-c statement_timeout={pg_backup._RESTORE_TIMEOUT}s")


def _first(row):
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


def _drop_app_schemas(conn, drill_name: str) -> None:
    """Drop every schema except public, information_schema and pg_*.

    Refuses unless the connection really is on the drill database. This is the
    one destructive statement of the drill, so the check is repeated here and
    does not trust the caller.
    """
    from psycopg import sql
    current = _first(conn.execute("SELECT current_database()").fetchone())
    if not drill_name.endswith("_drill") or current != drill_name:
        raise DrillRefused("پاک‌سازی فقط روی پایگاه دادهٔ تمرین مجاز است.")
    names = [_first(r) for r in conn.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname <> 'public'"
        " AND nspname <> 'information_schema' AND nspname !~ '^pg_'"
    ).fetchall()]
    for name in names:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def _free_bytes() -> int:
    """Free bytes on the disk that holds the database.

    Asks the server for its data directory and measures that disk, when the
    directory exists on this machine. `SHOW data_directory` needs a superuser
    or pg_read_all_settings, which the app role does not have. Then the backups
    folder is measured instead. In our deploys PostgreSQL and the app share one
    host and one disk, so it is the right number there. On a remote server it
    is the wrong disk, and the restore itself would fail later with a clear
    error rather than silently.
    """
    try:
        conn = pg_backup._connect(pg_backup._conn_parts(),
                                  "padyar-restore-drill-disk", "")
        try:
            data_dir = conn.execute("SHOW data_directory").fetchone()[0]
        finally:
            _close(conn)
        if data_dir and os.path.isdir(data_dir):
            return shutil.disk_usage(data_dir).free
    except Exception:  # noqa: BLE001  (no permission, or no local path)
        pass
    path = pg_backup.BACKUP_DIR
    # The folder does not exist before the first backup. Measure its parent.
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free


# ── The result ──────────────────────────────────────────────────────────

def _finish(manifest_file: str, backup_id: str, block: dict) -> None:
    """Write the block into the manifest and log the outcome.

    The one place a drill ends. It also publishes the drill metrics. It never
    raises: the checks have an answer, a manifest that can not be written
    (backup pruned mid-drill) is logged, and so is a metric that fails.

    The manifest is read again right before writing, and written through a
    temp file in the same folder plus os.replace, so a verify() write that
    landed during the drill is kept and a reader never sees a half file.
    """
    status = block["status"]
    try:
        with open(manifest_file, encoding="utf-8") as f:
            current = json.load(f)
        current["drill"] = block
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(manifest_file),
                                   prefix=".manifest.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            os.replace(tmp, manifest_file)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except (OSError, ValueError) as e:
        logger.error("[restore_drill] could not write the drill block for %s: %s",
                     backup_id, type(e).__name__)

    logger.info("[restore_drill] %s: %s (%s ms)", backup_id, status,
                block["duration_ms"])
    log = {"passed": applog.info, "skipped": applog.warning}.get(status, applog.error)
    log("backup", f"backup.drill.{status}", block["reason"],
        actor=block["actor"], target=backup_id,
        outcome="ok" if status == "passed" else status,
        duration_ms=block["duration_ms"],
        metadata={"tables_checked": block["tables_checked"],
                  "mismatches": len(block["mismatches"]),
                  "checks": block["checks"]})
    _publish_metrics(backup_id, block)


# ── Metrics ─────────────────────────────────────────────────────────────

def _passed_timestamp(block):
    """Unix seconds of a passed block's checked_at, or None if unusable.

    A time more than an hour ahead is refused, like
    pg_backup.record_verified does. The last-success gauge only moves forward,
    so one wrong clock would pin it in the future and a "drill is stale" alert
    could never fire again.
    """
    moment = _parse_time(block.get("checked_at"))
    if moment is None:
        return None
    timestamp = moment.timestamp()
    if timestamp > time.time() + pg_backup._FUTURE_MARGIN_SECONDS:
        return None
    return timestamp


def _publish_metrics(backup_id: str, block: dict) -> None:
    """Show a finished drill in /metrics. Never raises.

    A metric is a side effect. A drill that ran must still end with its block
    saved and its outcome logged, so a failing metric write is only logged.
    """
    try:
        from app.services import metrics
        metrics.backup_drill_last_duration_seconds.set(
            (block.get("duration_ms") or 0) / 1000)
        metrics.backup_drill_last_ok.set(1 if block.get("status") == "passed" else 0)
        _move_success_time(backup_id, block)
    except Exception as e:  # noqa: BLE001 (see docstring)
        logger.warning("[restore_drill] drill metrics not updated for %s: %s",
                       backup_id, type(e).__name__)


def _move_success_time(backup_id: str, block: dict) -> None:
    """Move the last-success time to this block's checked_at if it passed."""
    if block.get("status") != "passed":
        return
    timestamp = _passed_timestamp(block)
    if timestamp is None:
        logger.warning("[restore_drill] passed drill of %s has no usable "
                       "checked_at; the time metric is not moved", backup_id)
        return
    from app.services import metrics
    metrics.set_backup_drill_last_success(timestamp)


def seed_metrics() -> None:
    """At startup: the newest drill results already on disk -> the gauges.

    Without this, every restart would show "no drill" until the next night, and
    an alert would fire for a drill that passed. The time comes from the newest
    PASSED drill. Duration and ok come from the newest drill of any status,
    because that is what those gauges mean. Never raises: a missing directory or
    a broken manifest must not stop the app from booting.
    """
    try:
        manifests = pg_backup.list_backups()
        newest = latest_drill(manifests)
        if newest is not None:
            _publish_metrics(newest.get("backup_id", "?"), newest)
        # The newest drill may have failed while an older one passed. The time
        # metric must still show that pass (it only moves forward, so this
        # never undoes the step above).
        passed = [m for m in manifests
                  if isinstance(m, dict) and isinstance(m.get("drill"), dict)
                  and m["drill"].get("status") == "passed"]
        newest_pass = latest_drill(passed)
        if newest_pass is not None:
            _move_success_time(newest_pass.get("backup_id", "?"), newest_pass)
    except Exception as e:  # noqa: BLE001 (see docstring)
        logger.warning("[restore_drill] drill metrics not seeded: %s",
                       type(e).__name__)
