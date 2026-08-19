"""Per-database diagnostics and exactly six maintenance operations.

THE ALLOWLIST IS THE WHOLE SECURITY MODEL
-----------------------------------------
This module opens SQLite files, so a caller-supplied *path* must never reach
`sqlite3.connect`. The public API takes a short NAME — "app" or "logs" — which
is looked up in `_DATABASES`. "../../etc/passwd", "/etc/passwd" and "app;DROP"
are not keys in that dict, so they are refused before any file is touched. The
mapping name -> real path is resolved here and nowhere else.

NO SQL CONSOLE, EVER
--------------------
There are six operations and they are Python functions, listed in `ACTIONS`.
The router looks an action name up in that dict; nothing is ever fetched off a
module by a user-supplied attribute name, and no caller string is ever placed
into SQL text. The only identifiers that reach SQL come from `sqlite_master`
(the database describing itself) and still pass through `_quote_ident`.

ONE OPERATION AT A TIME
-----------------------
`_MAINT_LOCK` is a module-level, NON-blocking lock. VACUUM rewrites the whole
file while a concurrent wal_checkpoint(TRUNCATE) is truncating the write-ahead
log — running those together is how a day's data is lost. A second request is
REFUSED with a Persian message, never queued: an operator who double-clicks
should be told "busy", not silently schedule a second whole-file rewrite.

VACUUM NEEDS ROOM
-----------------
VACUUM builds a complete second copy of the database before swapping it in. If
free disk space is below twice the file size, it is refused up front. Running
out of space halfway through a rewrite is precisely the accident this check
exists to prevent.

TIMEOUTS ARE REAL, NOT HOPEFUL
------------------------------
Every operation installs a progress handler that aborts the statement once its
deadline passes. An integrity check on a damaged multi-gigabyte file would
otherwise hold the maintenance lock forever, and the panel would show a spinner
that never resolves.
"""
import os
import re
import sqlite3
import threading
import time

from app.config import logger
from app.services import applog
from app.services import storage


class UnknownDatabase(ValueError):
    """A name that is not in the allowlist. Message is operator-facing Persian."""


# The entire universe of databases this panel may touch. Two files, two names.
# `config_attr` is read from app.config at call time so the test suite can point
# both at throwaway files.
_DATABASES: dict[str, dict] = {
    "app": {
        "label_fa": "پایگاه دادهٔ اصلی",
        "description_fa": "محتوا، سوال‌ها، تنظیمات، حساب مدیر و تاریخچهٔ گفتگوها",
        "config_attr": "DB_PATH",
        "confirm_phrase": "VACUUM APPLICATION DATABASE",
    },
    "logs": {
        "label_fa": "پایگاه دادهٔ لاگ‌ها",
        "description_fa": "رخدادهای سامانه، سوابق تغییرات مدیر و رخدادهای امنیتی",
        "config_attr": "LOGS_DB_PATH",
        "confirm_phrase": "VACUUM LOGS DATABASE",
    },
}

NAMES: tuple = tuple(_DATABASES)

UNKNOWN_DB_FA = "پایگاه دادهٔ درخواست‌شده شناخته نشد."
UNKNOWN_ACTION_FA = "این عملیات پشتیبانی نمی‌شود."
BUSY_FA = "یک عملیات نگهداری دیگر هم‌اکنون در حال اجراست. لطفاً تا پایان آن صبر کنید و دوباره تلاش کنید."
MISSING_FILE_FA = "فایل این پایگاه داده روی سرور پیدا نشد."
TIMEOUT_FA = "عملیات به دلیل طولانی شدن یا قفل بودن پایگاه داده نیمه‌کاره متوقف شد. کمی بعد دوباره تلاش کنید."
DAMAGED_FA = "پایگاه داده خوانده نشد؛ به‌احتمال زیاد فایل آسیب دیده است. از آخرین نسخهٔ پشتیبان بازیابی کنید."
GENERIC_FAIL_FA = "انجام این عملیات ممکن نشد."

# Per-operation deadlines, in seconds. A full integrity check reads every page,
# so it gets the longest budget after VACUUM.
TIMEOUTS = {
    "integrity_check": 120.0,
    "quick_check": 60.0,
    "analyze": 60.0,
    "optimize": 60.0,
    "wal_checkpoint": 30.0,
    "vacuum": 300.0,
}

# Static SQL. Kept as constants so that every `.execute(` call site in this file
# is either a constant or an explicitly quoted identifier — a property the test
# suite checks by reading this source.
_SQL_VERSION = "SELECT sqlite_version()"
_SQL_JOURNAL_MODE = "PRAGMA journal_mode"
_SQL_PAGE_SIZE = "PRAGMA page_size"
_SQL_PAGE_COUNT = "PRAGMA page_count"
_SQL_FREELIST = "PRAGMA freelist_count"
_SQL_TABLE_COUNT = ("SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                    " AND name NOT LIKE 'sqlite_%'")
_SQL_INDEX_COUNT = ("SELECT COUNT(*) FROM sqlite_master WHERE type='index'"
                    " AND name NOT LIKE 'sqlite_%'")
_SQL_TABLE_NAMES = ("SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name NOT LIKE 'sqlite_%' ORDER BY name")
_SQL_INTEGRITY = "PRAGMA integrity_check"
_SQL_QUICK = "PRAGMA quick_check"
_SQL_ANALYZE = "ANALYZE"
_SQL_OPTIMIZE = "PRAGMA optimize"
_SQL_CHECKPOINT = "PRAGMA wal_checkpoint(TRUNCATE)"
_SQL_VACUUM = "VACUUM"

# Only one maintenance operation may run at a time — see the module docstring.
_MAINT_LOCK = threading.Lock()
_current: dict = {}


# ── Name resolution ─────────────────────────────────────────────────────

def _spec(name) -> tuple:
    key = name.strip().lower() if isinstance(name, str) else ""
    spec = _DATABASES.get(key)
    if spec is None:
        raise UnknownDatabase(UNKNOWN_DB_FA)
    return key, spec


def is_known(name) -> bool:
    try:
        _spec(name)
        return True
    except UnknownDatabase:
        return False


def db_path(name) -> str:
    """The real path for an allowlisted name. The only path source in here."""
    key, spec = _spec(name)
    import app.config as config
    return getattr(config, spec["config_attr"])


def confirm_phrase(name) -> str:
    _key, spec = _spec(name)
    return spec["confirm_phrase"]


def databases() -> list:
    return [{"name": key, "label_fa": spec["label_fa"],
             "description_fa": spec["description_fa"],
             "confirm_phrase": spec["confirm_phrase"]}
            for key, spec in _DATABASES.items()]


# ── SQL identifier safety ───────────────────────────────────────────────
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(identifier: str) -> str:
    """Validate and quote a table name that came out of `sqlite_master`.

    These names are never user input — the database is describing itself — but
    this is the ONE place in this module where any name is put into SQL text,
    which is what makes "no caller string reaches SQL" a checkable claim rather
    than a promise.
    """
    if not _IDENT_RE.match(identifier or ""):
        raise ValueError("unsafe identifier")
    return '"' + identifier + '"'


# ── Connections ─────────────────────────────────────────────────────────

def _connect(path: str, timeout_seconds: float):
    """A connection with a busy timeout AND a hard deadline.

    `set_progress_handler` returning non-zero aborts the running statement, so
    a runaway PRAGMA on a damaged file cannot hold the maintenance lock open
    indefinitely. isolation_level=None because VACUUM refuses to run inside a
    transaction and Python's legacy mode would open one.
    """
    conn = sqlite3.connect(path, timeout=min(timeout_seconds, 10.0))
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA busy_timeout=5000")
    deadline = time.monotonic() + timeout_seconds
    conn.set_progress_handler(
        lambda: 1 if time.monotonic() > deadline else 0, 5000)
    return conn


def _scalar(conn, sql: str):
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# ── Stored integrity result ─────────────────────────────────────────────
# Kept in the settings table so the page can show "last checked" without
# re-reading every page of every database on each visit.

def _status_key(name: str) -> str:
    return "db_integrity_status_" + name


def _checked_at_key(name: str) -> str:
    return "db_integrity_checked_at_" + name


def _store_integrity(name: str, status: str) -> None:
    try:
        from app.db.queries import set_setting
        set_setting(_status_key(name), status)
        set_setting(_checked_at_key(name), time.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as e:  # noqa: BLE001 — a bookkeeping write is never fatal
        logger.error("[dbadmin] could not store integrity result: %s", type(e).__name__)


def _read_stored(name: str) -> tuple:
    try:
        from app.db.queries import get_setting
        return ((get_setting(_status_key(name), "") or "unknown"),
                (get_setting(_checked_at_key(name), "") or ""))
    except Exception:  # noqa: BLE001
        return "unknown", ""


# ── Read-only reporting ─────────────────────────────────────────────────

def overview(name) -> dict:
    """Everything the Database page shows for one database.

    `path_basename` only — the panel is reachable over the network and the
    server's directory layout is not something it needs to publish.
    """
    key, spec = _spec(name)
    path = db_path(key)
    status, checked_at = _read_stored(key)

    info = {
        "name": key,
        "label_fa": spec["label_fa"],
        "description_fa": spec["description_fa"],
        "confirm_phrase": spec["confirm_phrase"],
        "path_basename": os.path.basename(path),
        "exists": os.path.exists(path),
        "size_bytes": _size(path),
        "wal_bytes": _size(path + "-wal"),
        "sqlite_version": sqlite3.sqlite_version,
        "journal_mode": "",
        "page_size": 0,
        "page_count": 0,
        "freelist_count": 0,
        "table_count": 0,
        "index_count": 0,
        "integrity_status": status,
        "integrity_checked_at": checked_at,
        "readable": False,
    }
    if not info["exists"]:
        # Do NOT connect: sqlite3.connect would create an empty file and the
        # panel would report a database that does not exist as healthy.
        return info

    try:
        conn = _connect(path, 10.0)
        try:
            info["sqlite_version"] = str(_scalar(conn, _SQL_VERSION) or sqlite3.sqlite_version)
            info["journal_mode"] = str(_scalar(conn, _SQL_JOURNAL_MODE) or "")
            info["page_size"] = int(_scalar(conn, _SQL_PAGE_SIZE) or 0)
            info["page_count"] = int(_scalar(conn, _SQL_PAGE_COUNT) or 0)
            info["freelist_count"] = int(_scalar(conn, _SQL_FREELIST) or 0)
            info["table_count"] = int(_scalar(conn, _SQL_TABLE_COUNT) or 0)
            info["index_count"] = int(_scalar(conn, _SQL_INDEX_COUNT) or 0)
            info["readable"] = True
        finally:
            conn.close()
    except sqlite3.Error as e:
        applog.exception("system", "database.overview.failed", e,
                         message="خواندن اطلاعات پایگاه داده ممکن نشد",
                         target=key, subcategory="database")
    return info


def tables(name) -> list:
    """[{table, rows, indexes}] — exact row counts, which is affordable here.

    This project's largest table is measured in tens of thousands of rows; an
    approximate count would buy nothing and would make the page lie.
    """
    key, _spec_unused = _spec(name)
    path = db_path(key)
    if not os.path.exists(path):
        return []

    out = []
    try:
        conn = _connect(path, 20.0)
    except sqlite3.Error as e:
        applog.exception("system", "database.tables.failed", e,
                         message="فهرست جدول‌ها خوانده نشد", target=key,
                         subcategory="database")
        return []
    try:
        names = [str(r[0]) for r in conn.execute(_SQL_TABLE_NAMES)]
        for table in names:
            try:
                _quote_ident(table)
            except ValueError:
                continue                      # a name SQLite itself would need quoting for
            rows = -1
            try:
                rows = int(conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()[0])
            except (sqlite3.Error, TypeError, ValueError):
                rows = -1
            indexes = []
            try:
                for r in conn.execute(f"PRAGMA index_list({_quote_ident(table)})"):
                    index_name = str(r["name"])
                    if not index_name.startswith("sqlite_autoindex"):
                        indexes.append(index_name)
            except (sqlite3.Error, IndexError, KeyError):
                indexes = []
            out.append({"table": table, "rows": rows, "indexes": sorted(indexes)})
    except sqlite3.Error as e:
        applog.exception("system", "database.tables.failed", e,
                         message="فهرست جدول‌ها خوانده نشد", target=key,
                         subcategory="database")
    finally:
        conn.close()
    return out


# ── Maintenance frame ───────────────────────────────────────────────────

def _maintenance(name, action: str, actor: str, runner) -> dict:
    """Lock, time, audit and translate one operation. Never raises.

    Every path through here writes exactly one audit row — including the
    refusals. An operator who is told "busy" and an operator whose VACUUM was
    denied for lack of disk both leave a trace.
    """
    event = "admin.database." + action

    try:
        key, _spec_unused = _spec(name)
    except UnknownDatabase as e:
        applog.audit(event, message="نام پایگاه داده معتبر نبود", actor=actor,
                     target=str(name)[:80], outcome="denied", level="warning",
                     subcategory="database")
        return {"ok": False, "message_fa": str(e), "duration_ms": 0,
                "detail": "unknown_database"}

    if not _MAINT_LOCK.acquire(blocking=False):
        applog.audit(event, message="عملیات نگهداری هم‌زمان رد شد", actor=actor,
                     target=key, outcome="denied", level="warning",
                     subcategory="database",
                     metadata={"running": dict(_current)})
        return {"ok": False, "message_fa": BUSY_FA, "duration_ms": 0,
                "detail": "busy"}

    _current.clear()
    _current.update({"name": key, "action": action})
    started = time.monotonic()
    try:
        path = db_path(key)
        if not os.path.exists(path):
            result = {"ok": False, "message_fa": MISSING_FILE_FA,
                      "detail": "missing_file"}
        else:
            result = runner(key, path)
    except sqlite3.OperationalError as e:
        applog.exception("system", "database." + action + ".failed", e,
                         message="عملیات نگهداری ناتمام ماند", target=key,
                         subcategory="database")
        result = {"ok": False, "message_fa": TIMEOUT_FA, "detail": type(e).__name__}
    except sqlite3.DatabaseError as e:
        applog.exception("system", "database." + action + ".failed", e,
                         message="پایگاه داده خوانده نشد", target=key,
                         subcategory="database")
        result = {"ok": False, "message_fa": DAMAGED_FA, "detail": type(e).__name__}
    except Exception as e:  # noqa: BLE001 — a panel button must not 500
        applog.exception("system", "database." + action + ".failed", e,
                         message="عملیات نگهداری با خطا مواجه شد", target=key,
                         subcategory="database")
        result = {"ok": False, "message_fa": GENERIC_FAIL_FA, "detail": type(e).__name__}
    finally:
        _current.clear()
        _MAINT_LOCK.release()

    duration_ms = int((time.monotonic() - started) * 1000)
    result.setdefault("detail", "")
    result["duration_ms"] = duration_ms
    applog.audit(event, message=result.get("message_fa", ""), actor=actor,
                 target=key, outcome="ok" if result.get("ok") else "error",
                 level="notice" if result.get("ok") else "warning",
                 duration_ms=duration_ms, subcategory="database",
                 metadata={"detail": str(result.get("detail", ""))[:400]})
    return result


# ── The six operations ──────────────────────────────────────────────────

def integrity_check(name, actor: str = "") -> dict:
    """PRAGMA integrity_check — reads every page. The authoritative answer."""
    def runner(key, path):
        try:
            conn = _connect(path, TIMEOUTS["integrity_check"])
        except sqlite3.DatabaseError:
            _store_integrity(key, "failed")
            raise
        try:
            rows = [str(r[0]) for r in conn.execute(_SQL_INTEGRITY)]
        except sqlite3.DatabaseError:
            # "file is not a database" arrives here, not as a result row. That
            # IS a failed integrity check and must be recorded as one.
            _store_integrity(key, "failed")
            raise
        finally:
            conn.close()

        healthy = len(rows) == 1 and rows[0].strip().lower() == "ok"
        _store_integrity(key, "ok" if healthy else "failed")
        if healthy:
            return {"ok": True, "detail": "ok",
                    "message_fa": "بررسی کامل سلامت انجام شد و هیچ مشکلی پیدا نشد."}
        return {"ok": False, "detail": " | ".join(rows[:5])[:500],
                "message_fa": "بررسی سلامت مشکل پیدا کرد. فوراً نسخهٔ پشتیبان تهیه کنید "
                              "و از آخرین پشتیبان سالم بازیابی کنید."}
    return _maintenance(name, "integrity_check", actor, runner)


def quick_check(name, actor: str = "") -> dict:
    """PRAGMA quick_check — the same answer minus the index cross-checks."""
    def runner(key, path):
        try:
            conn = _connect(path, TIMEOUTS["quick_check"])
        except sqlite3.DatabaseError:
            _store_integrity(key, "failed")
            raise
        try:
            rows = [str(r[0]) for r in conn.execute(_SQL_QUICK)]
        except sqlite3.DatabaseError:
            _store_integrity(key, "failed")
            raise
        finally:
            conn.close()

        healthy = len(rows) == 1 and rows[0].strip().lower() == "ok"
        _store_integrity(key, "ok" if healthy else "failed")
        if healthy:
            return {"ok": True, "detail": "ok",
                    "message_fa": "بررسی سریع سلامت انجام شد و مشکلی دیده نشد."}
        return {"ok": False, "detail": " | ".join(rows[:5])[:500],
                "message_fa": "بررسی سریع مشکل پیدا کرد. بررسی کامل سلامت را اجرا کنید."}
    return _maintenance(name, "quick_check", actor, runner)


def analyze(name, actor: str = "") -> dict:
    """ANALYZE — refresh the statistics the query planner chooses indexes with."""
    def runner(_key, path):
        conn = _connect(path, TIMEOUTS["analyze"])
        try:
            conn.execute(_SQL_ANALYZE)
        finally:
            conn.close()
        return {"ok": True, "detail": "analyze",
                "message_fa": "آمار جستجو به‌روزرسانی شد."}
    return _maintenance(name, "analyze", actor, runner)


def optimize(name, actor: str = "") -> dict:
    """PRAGMA optimize — SQLite decides for itself what is worth doing."""
    def runner(_key, path):
        conn = _connect(path, TIMEOUTS["optimize"])
        try:
            conn.execute(_SQL_OPTIMIZE)
        finally:
            conn.close()
        return {"ok": True, "detail": "optimize",
                "message_fa": "بهینه‌سازی خودکار انجام شد."}
    return _maintenance(name, "optimize", actor, runner)


def wal_checkpoint(name, actor: str = "") -> dict:
    """PRAGMA wal_checkpoint(TRUNCATE) — fold the write-ahead log back in."""
    def runner(_key, path):
        before = _size(path + "-wal")
        conn = _connect(path, TIMEOUTS["wal_checkpoint"])
        try:
            row = conn.execute(_SQL_CHECKPOINT).fetchone()
        finally:
            conn.close()
        busy = int(row[0]) if row is not None else 0
        after = _size(path + "-wal")
        if busy:
            return {"ok": False, "detail": "busy_writers",
                    "message_fa": "به دلیل استفادهٔ هم‌زمان، فایل موقت به‌طور کامل "
                                  "پاک نشد. چند دقیقهٔ دیگر دوباره تلاش کنید."}
        return {"ok": True, "detail": f"wal {before} -> {after}",
                "message_fa": "فایل موقت پایگاه داده پاک‌سازی شد.",
                "freed_bytes": max(0, before - after)}
    return _maintenance(name, "wal_checkpoint", actor, runner)


def vacuum(name, actor: str = "") -> dict:
    """VACUUM — rewrite the file, returning free pages to the filesystem.

    Refused when free disk space is under twice the current file size: VACUUM
    writes a full second copy before swapping, so starting it on a nearly full
    volume is how an install ends up with neither copy complete.
    """
    def runner(_key, path):
        size = _size(path)
        if size and not storage.has_space_for(size * 2):
            return {"ok": False, "detail": "insufficient_space",
                    "message_fa": "فضای آزاد دیسک برای فشرده‌سازی کافی نیست. این کار به "
                                  "فضایی حدود دو برابر اندازهٔ پایگاه داده نیاز دارد؛ "
                                  "ابتدا فضا آزاد کنید."}
        conn = _connect(path, TIMEOUTS["vacuum"])
        try:
            conn.execute(_SQL_VACUUM)
        finally:
            conn.close()
        after = _size(path)
        return {"ok": True, "detail": f"{size} -> {after}",
                "message_fa": "فشرده‌سازی پایگاه داده انجام شد.",
                "freed_bytes": max(0, size - after)}
    return _maintenance(name, "vacuum", actor, runner)


# ── The allowlist the router dispatches through ─────────────────────────
# A dict of functions, not a module to be getattr()'d. An action name that is
# not a key here reaches nothing at all.
ACTIONS = {
    "integrity_check": integrity_check,
    "quick_check": quick_check,
    "analyze": analyze,
    "optimize": optimize,
    "wal_checkpoint": wal_checkpoint,
    "vacuum": vacuum,
}

# Operations that rewrite the whole file need a typed confirmation first.
DANGEROUS_ACTIONS = ("vacuum",)

ACTION_LABELS_FA = {
    "integrity_check": "بررسی کامل سلامت",
    "quick_check": "بررسی سریع سلامت",
    "analyze": "به‌روزرسانی آمار جستجو",
    "optimize": "بهینه‌سازی خودکار",
    "wal_checkpoint": "پاک‌سازی فایل موقت",
    "vacuum": "فشرده‌سازی و آزادسازی فضا",
}

ACTION_HELP_FA = {
    "integrity_check": "همهٔ صفحه‌های پایگاه داده را می‌خواند و سالم بودن آن را تأیید می‌کند. کمی طول می‌کشد.",
    "quick_check": "نسخهٔ سریع‌تر بررسی سلامت. برای بررسی روزانه مناسب است.",
    "analyze": "آمار داخلی جستجو را تازه می‌کند تا پاسخ‌ها سریع‌تر پیدا شوند.",
    "optimize": "خود پایگاه داده تصمیم می‌گیرد چه کار کوچکی لازم است و همان را انجام می‌دهد.",
    "wal_checkpoint": "فایل موقتی که هنگام نوشتن ساخته می‌شود را در فایل اصلی ادغام و کوچک می‌کند.",
    "vacuum": "کل فایل را بازنویسی می‌کند و فضای خالی را به دیسک برمی‌گرداند. کند است و به فضای آزاد زیاد نیاز دارد.",
}


def action_catalog() -> list:
    """What the page renders its buttons from."""
    return [{"key": key,
             "label_fa": ACTION_LABELS_FA[key],
             "help_fa": ACTION_HELP_FA[key],
             "danger": key in DANGEROUS_ACTIONS}
            for key in ACTIONS]
