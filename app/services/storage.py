"""Disk facts for the operator — measured, never estimated.

WHY THIS EXISTS
---------------
An install dies of a full disk long before it dies of anything interesting.
SQLite stops accepting writes, the WAL cannot check-point, the nightly backup
silently produces a truncated file, and the first symptom anyone notices is a
chatbot that has stopped answering. This module answers one question honestly —
"how much room is left, and what is using it" — from `shutil.disk_usage` and
`os.stat`. Nothing here is a guess.

ONLY REAL CATEGORIES
--------------------
Every entry in `_category_spec()` is a directory or file this repository
actually creates. A category that reports zero because the install has not used
that feature yet is honest. A category for a service this project does not run
(a cache, a queue, a vector store) would be a lie on an operator's screen, so
there are none.

NO PATHS LEAVE THIS MODULE
--------------------------
The API returns sizes and Persian labels, never absolute paths. The admin panel
is reachable over the network; the server's filesystem layout is not something
it needs to publish.

RATE-LIMITED ALERTS
-------------------
A full disk is exactly the moment when logging hurts most: every alert row
costs the space the alert is complaining about. So a threshold alert fires at
most once per hour per state, tracked in memory. The check itself still runs on
every call — only the writing is throttled.
"""
import os
import shutil
import time

from app.config import BASE_DIR, logger
from app.services import applog

# Percent of the filesystem in use at which the operator is told. Overridable
# per install from the settings table; the defaults are what ships.
DEFAULT_WARN_PERCENT = 80.0
DEFAULT_CRITICAL_PERCENT = 90.0

WARN_SETTING_KEY = "storage_warn_percent"
CRITICAL_SETTING_KEY = "storage_critical_percent"

# One alert per state per hour. See the module docstring.
ALERT_INTERVAL_SECONDS = 3600.0
_last_alert: dict[str, float] = {}


def _disk_usage(path: str):
    """Seam for the real syscall — the one place tests replace."""
    return shutil.disk_usage(path)


# ── Thresholds ──────────────────────────────────────────────────────────

def _percent_setting(key: str, default: float) -> float:
    try:
        from app.db.queries import get_setting
        raw = (get_setting(key, "") or "").strip()
    except Exception:  # noqa: BLE001 — an unreadable setting is not fatal
        return default
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    # A threshold outside 1..100 is a typo, not an instruction. Silently
    # honouring "0" would put the panel in permanent red.
    if value < 1.0 or value > 100.0:
        return default
    return value


def thresholds() -> tuple[float, float]:
    """(warning, critical). Critical is never below warning."""
    warn = _percent_setting(WARN_SETTING_KEY, DEFAULT_WARN_PERCENT)
    critical = _percent_setting(CRITICAL_SETTING_KEY, DEFAULT_CRITICAL_PERCENT)
    if critical < warn:
        critical = warn
    return warn, critical


def classify(percent: float) -> str:
    """ok | warning | critical for a used-percentage."""
    warn, critical = thresholds()
    if percent >= critical:
        return "critical"
    if percent >= warn:
        return "warning"
    return "ok"


STATE_LABELS_FA = {
    "ok": "وضعیت فضای دیسک مناسب است",
    "warning": "فضای دیسک رو به اتمام است",
    "critical": "فضای دیسک بحرانی است",
    "unknown": "اندازه‌گیری فضای دیسک ممکن نشد",
}


# ── Sizes ───────────────────────────────────────────────────────────────

def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _sqlite_group_size(path: str) -> int:
    """A SQLite database is three files on disk, not one: the DB, its
    write-ahead log and its shared-memory index. Reporting only the first
    understates a busy database by however large its WAL has grown."""
    return sum(_file_size(path + suffix) for suffix in ("", "-wal", "-shm"))


def _dir_size(path: str, exclude: tuple = ()) -> int:
    """Bytes under `path`, skipping any directory listed in `exclude`.

    `os.lstat` (not `stat`) so a symlink counts as the link it is and a link
    into another tree is never double-counted or followed off the volume.
    """
    if not os.path.isdir(path):
        return 0
    skip = {os.path.normpath(p) for p in exclude}
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda _e: None):
        if os.path.normpath(root) in skip:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs
                   if os.path.normpath(os.path.join(root, d)) not in skip]
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _category_spec() -> list[tuple]:
    """(key, label_fa, kind, path, exclude) for what this install really has.

    Paths are resolved on every call, not captured at import, because the two
    database paths are configuration and the test suite redirects them.
    """
    import app.config as config

    data_dir = os.path.join(BASE_DIR, "data")
    models_dir = os.path.join(data_dir, "models")
    spec = [
        ("database_app", "پایگاه دادهٔ اصلی (محتوا، تنظیمات، گفتگوها)",
         "sqlite", config.DB_PATH, ()),
        ("database_logs", "پایگاه دادهٔ لاگ‌ها",
         "sqlite", config.LOGS_DB_PATH, ()),
        ("videos", "ویدیوهای پاسخ",
         "dir", os.path.join(BASE_DIR, "media", "videos"), ()),
        ("uploads", "فایل‌های بارگذاری‌شده",
         "dir", os.path.join(BASE_DIR, "media", "uploads"), ()),
        ("backups", "نسخه‌های پشتیبان",
         "dir", os.path.join(BASE_DIR, "backups"), ()),
        ("models", "مدل جستجوی آفلاین",
         "dir", models_dir, ()),
        # data/ minus data/models, so the model is not counted twice.
        ("data", "دادهٔ برنامه (تنظیمات فرم، ارزیابی)",
         "dir", data_dir, (models_dir,)),
    ]
    # Only present on installs where the knowledge-map tool has been run.
    graphify = os.path.join(BASE_DIR, "graphify-out")
    if os.path.isdir(graphify):
        spec.append(("graphify", "خروجی نقشهٔ دانش", "dir", graphify, ()))
    return spec


def categories() -> list[dict]:
    """Per-category sizes. No path ever appears in the return value."""
    out = []
    for key, label_fa, kind, path, exclude in _category_spec():
        try:
            if kind == "sqlite":
                size = _sqlite_group_size(path)
                exists = os.path.exists(path)
            else:
                size = _dir_size(path, exclude)
                exists = os.path.isdir(path)
        except OSError:
            size, exists = 0, False
        out.append({"key": key, "label_fa": label_fa, "kind": kind,
                    "exists": exists, "bytes": size})
    return out


# ── Disk ────────────────────────────────────────────────────────────────

def disk() -> dict:
    """Total/used/free/percent for the volume the project lives on."""
    try:
        usage = _disk_usage(BASE_DIR)
        total, used, free = int(usage.total), int(usage.used), int(usage.free)
    except (OSError, ValueError, AttributeError) as e:
        logger.error("[storage] disk usage unavailable: %s", type(e).__name__)
        warn, critical = thresholds()
        return {"total_bytes": 0, "used_bytes": 0, "free_bytes": 0,
                "percent_used": 0.0, "state": "unknown",
                "state_label_fa": STATE_LABELS_FA["unknown"],
                "warn_percent": warn, "critical_percent": critical}

    percent = round(used / total * 100, 1) if total > 0 else 0.0
    warn, critical = thresholds()
    state = classify(percent)
    return {"total_bytes": total, "used_bytes": used, "free_bytes": free,
            "percent_used": percent, "state": state,
            "state_label_fa": STATE_LABELS_FA[state],
            "warn_percent": warn, "critical_percent": critical}


def free_bytes() -> int:
    try:
        return int(_disk_usage(BASE_DIR).free)
    except (OSError, ValueError, AttributeError):
        return -1


def has_space_for(bytes_needed) -> bool:
    """Can the volume take `bytes_needed` more?

    Consulted by the backup and VACUUM paths before they start writing. An
    unmeasurable disk answers False on purpose: "I could not check" is not a
    reason to begin rewriting a whole database.
    """
    try:
        needed = int(bytes_needed)
    except (TypeError, ValueError):
        return False
    if needed <= 0:
        return True
    available = free_bytes()
    if available < 0:
        return False
    return available >= needed


def _maybe_alert(info: dict) -> None:
    """Write at most one row per state per hour. See the module docstring."""
    state = info.get("state")
    if state not in ("warning", "critical"):
        return
    now = time.monotonic()
    last = _last_alert.get(state)
    if last is not None and (now - last) < ALERT_INTERVAL_SECONDS:
        return
    _last_alert[state] = now

    fields = {
        "subcategory": "storage",
        "outcome": "warning" if state == "warning" else "critical",
        "metadata": {
            "percent_used": info.get("percent_used"),
            "free_bytes": info.get("free_bytes"),
            "total_bytes": info.get("total_bytes"),
            "warn_percent": info.get("warn_percent"),
            "critical_percent": info.get("critical_percent"),
        },
    }
    percent = info.get("percent_used")
    if state == "critical":
        applog.critical("system", "storage.disk.critical",
                        f"فضای دیسک بحرانی است — {percent} درصد پر شده است.",
                        **fields)
    else:
        applog.warning("system", "storage.disk.low",
                       f"فضای دیسک رو به اتمام است — {percent} درصد پر شده است.",
                       **fields)


def reset_alert_state() -> None:
    """Forget the rate-limit window. For tests and for a manual re-check."""
    _last_alert.clear()


def overview() -> dict:
    """Everything the Storage page shows, in one call."""
    info = disk()
    cats = categories()
    _maybe_alert(info)
    warn, critical = thresholds()
    return {
        "disk": info,
        "categories": cats,
        "tracked_bytes": sum(c["bytes"] for c in cats),
        "thresholds": {"warn_percent": warn, "critical_percent": critical},
    }
