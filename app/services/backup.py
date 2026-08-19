"""Backup scheduling — on top of the stdlib primitives in backup_db.py.

The admin panel lets staff pick how often backups run and at what time of day.
Those choices live in the `settings` table (so they survive restarts), and a
background task in the app honours them.

Multi-worker safe: gunicorn runs several workers, each with its own scheduler
loop. Before a backup runs, one worker atomically "claims" the due slot by
advancing `backup_next_run` in the DB — only the winner actually backs up, so
we never get duplicate backups from parallel workers.
"""
import asyncio
from datetime import datetime, timedelta

import backup_db
from app.config import logger
from app.db.connection import get_db_connection
from app.db.queries import get_setting, set_setting

# Setting keys + defaults
DEFAULTS = {
    "backup_auto_enabled": "true",
    "backup_interval_hours": "24",
    "backup_time": "03:00",  # used when interval >= 24h
}
CHECK_EVERY_SECONDS = 60


def get_schedule() -> dict:
    # Read all five keys over one connection. This runs every 60s in every
    # worker, so opening five separate connections (one per get_setting) is
    # needless DB churn.
    keys = ["backup_auto_enabled", "backup_interval_hours", "backup_time",
            "backup_last_run", "backup_next_run"]
    conn = get_db_connection()
    try:
        placeholders = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})", keys
        ).fetchall()
    finally:
        conn.close()
    vals = {row["key"]: row["value"] for row in rows}

    return {
        "enabled": vals.get("backup_auto_enabled", DEFAULTS["backup_auto_enabled"]) == "true",
        "interval_hours": int(vals.get("backup_interval_hours", DEFAULTS["backup_interval_hours"])),
        "time": vals.get("backup_time", DEFAULTS["backup_time"]),
        "last_run": vals.get("backup_last_run", ""),
        "next_run": vals.get("backup_next_run", ""),
    }


def save_schedule(enabled: bool, interval_hours: int, time_str: str) -> dict:
    set_setting("backup_auto_enabled", "true" if enabled else "false")
    set_setting("backup_interval_hours", str(interval_hours))
    set_setting("backup_time", time_str)
    # Recompute the next run from now using the new settings.
    next_run = compute_next_run(datetime.now(), interval_hours, time_str)
    set_setting("backup_next_run", next_run.isoformat())
    return get_schedule()


def compute_next_run(now: datetime, interval_hours: int, time_str: str) -> datetime:
    """Next backup time after `now` for the given cadence.

    Sub-daily (< 24h): just now + interval. Daily or longer: anchored to the
    chosen time of day."""
    if interval_hours < 24:
        return now + timedelta(hours=interval_hours)
    try:
        hh, mm = (int(x) for x in time_str.split(":"))
    except (ValueError, AttributeError):
        hh, mm = 3, 0
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(hours=interval_hours)
    return candidate


def _claim_due_slot(current_next: str, new_next: str) -> bool:
    """Atomically advance backup_next_run. Returns True only for the one worker
    that wins the race — that worker performs the backup."""
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'backup_next_run' AND value = ?",
            (new_next, current_next),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _run_backup_now(actor: str = "scheduler", kind: str = "scheduled"):
    """Take a backup SET + prune, and record the time.

    A SET, not a single file: this install has TWO databases (content and
    logging) and a copy of one without the other cannot restore the system.
    app/services/backup_center.py owns that, including the WAL-safe copy and
    the manifest — this function only decides WHEN.

    Returns the new set's directory path, keeping the old string contract so
    the legacy admin endpoint (`os.path.basename(path)`) still reports a
    sensible name — it now reports the backup id."""
    from app.services import backup_center

    summary = backup_center.create(actor=actor, kind=kind)
    removed = backup_center.prune()
    set_setting("backup_last_run", datetime.now().isoformat())
    logger.info("Backup set created: %s%s", summary["backup_id"],
                f" (pruned {len(removed)})" if removed else "")
    return backup_center.set_dir(summary["backup_id"]) or summary["backup_id"]


def create_backup_now(actor: str = "admin"):
    return _run_backup_now(actor=actor, kind="manual")


async def scheduler_loop():
    """Background task: every minute, run a backup if one is due."""
    logger.info("Backup scheduler started")
    from app.services import applog
    applog.service("backup.scheduler.started", "زمان‌بند پشتیبان‌گیری آغاز شد")
    while True:
        try:
            await asyncio.sleep(CHECK_EVERY_SECONDS)
            sched = get_schedule()
            if not sched["enabled"]:
                continue

            now = datetime.now()
            # Seed next_run on first tick if missing.
            if not sched["next_run"]:
                set_setting(
                    "backup_next_run",
                    compute_next_run(now, sched["interval_hours"], sched["time"]).isoformat(),
                )
                continue

            from app.db.timeutil import as_datetime
            next_run = as_datetime(sched["next_run"])
            if now < next_run:
                continue

            new_next = compute_next_run(now, sched["interval_hours"], sched["time"]).isoformat()
            if _claim_due_slot(sched["next_run"], new_next):
                # We won the race — run the backup in a thread (sqlite is blocking).
                await asyncio.get_running_loop().run_in_executor(None, _run_backup_now)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Backup scheduler error: {e}")
            # The loop keeps running, so the failure has to be visible
            # somewhere durable — a scheduler that has been silently failing
            # for a month looks exactly like one that has nothing to do.
            applog.exception("backup", "backup.scheduler.error", e,
                             "چرخهٔ زمان‌بند پشتیبان‌گیری با خطا مواجه شد",
                             outcome="error")
