#!/usr/bin/env python3
"""SQLite backup primitives for PadyarAIChatbot.

Makes a consistent copy of a live SQLite database using SQLite's ONLINE BACKUP
API (`source.backup(dest)`). That choice is not cosmetic: every database in
this app runs in WAL mode, so the newest committed transactions live in the
`-wal` sidecar and have not reached the main `.db` file yet. Copying the `.db`
file with `shutil.copy` therefore silently loses whatever the WAL still holds.
`copy_database()` is the one place that copy is performed, and every caller —
the CLI below, the scheduler, and app/services/backup_center.py — goes through
it so the rule can never be forgotten in one branch.

This module stays pure stdlib for standalone use:

    python backup_db.py        # take one backup now + prune old ones

`app.config` is imported LAZILY and defensively (see `db_path`): when the app
is importable, its env-resolved DB_PATH/LOGS_DB_PATH win, so an install that
sets DB_PATH in .env is backed up correctly instead of the module guessing the
default location. Standalone use still works with no app on the path.
"""
import os
import re
import glob
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chat_history.db")
LOGS_DB_PATH = os.path.join(BASE_DIR, "application_logs.db")
# Overridable so an install can put backups on another volume, and so tests
# never write into the operator's real backups/ directory.
BACKUP_DIR = os.getenv("BACKUP_DIR") or os.path.join(BASE_DIR, "backups")
KEEP = 14  # keep the last 14 backups, delete the rest

# A backup file name we created — used to validate download/delete requests
# so a caller can never reach outside the backups directory.
_NAME_RE = re.compile(r"^chat_history_\d{8}_\d{6}\.db$")


def _app_config():
    """app.config if this process has the app on its path, else None."""
    try:
        from app import config
        return config
    except Exception:  # noqa: BLE001 — standalone use is a supported mode
        return None


def db_path() -> str:
    """The live main database, resolved the same way the app resolves it."""
    cfg = _app_config()
    return getattr(cfg, "DB_PATH", "") or DB_PATH


def logs_db_path() -> str:
    """The live logging database, resolved the same way the app resolves it."""
    cfg = _app_config()
    return getattr(cfg, "LOGS_DB_PATH", "") or LOGS_DB_PATH


def backup_dir() -> str:
    """Where backups are written. Read through the module global so a test (or
    an install) can redirect every caller by setting one attribute."""
    return BACKUP_DIR


def copy_database(src_path: str, dest_path: str, standalone: bool = False) -> str:
    """WAL-SAFE copy of one SQLite database. Returns `dest_path`.

    Uses the online backup API, never a file copy: a plain copy of a WAL
    database can miss committed transactions still sitting in the `-wal`, and
    can capture a torn page mid-checkpoint. This is the only copy routine in
    the backup subsystem — do not add another.

    `standalone=True` folds the destination out of WAL mode. The backup API
    copies page 1 verbatim, so a backup of a WAL database is itself a WAL
    database — and every later read of it (a verification, a download) drops a
    `-shm`/`-wal` pair beside the very file whose SHA-256 we recorded. A
    backup has to be ONE self-contained artefact whose bytes do not move when
    somebody looks at it. Pass it for backup destinations, never when
    restoring INTO a live database: that one must stay in WAL mode.

    `busy_timeout` on both ends means a concurrent writer delays the copy
    instead of failing it. A failed copy removes its half-written destination
    rather than leaving a file that looks like a backup and is not.
    """
    if not os.path.isfile(src_path):
        raise FileNotFoundError(f"No database at {src_path}")

    parent = os.path.dirname(os.path.abspath(dest_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    src = sqlite3.connect(src_path)
    src.execute("PRAGMA busy_timeout=5000")
    dst = sqlite3.connect(dest_path)
    dst.execute("PRAGMA busy_timeout=5000")
    try:
        with dst:
            src.backup(dst)
        if standalone:
            # Outside the transaction — journal_mode cannot change inside one.
            # Leaving WAL mode checkpoints and removes the -wal on the way out.
            dst.execute("PRAGMA journal_mode=DELETE")
    except Exception:
        try:
            dst.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except OSError:
            pass
        src.close()
        raise
    finally:
        try:
            dst.close()
        except Exception:  # noqa: BLE001
            pass
        src.close()
    return dest_path


def create_backup() -> str:
    """Make one backup of the main database now. Returns its absolute path."""
    live = db_path()
    if not os.path.exists(live):
        raise FileNotFoundError(f"No database at {live}")

    directory = backup_dir()
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = os.path.join(directory, f"chat_history_{stamp}.db")
    return copy_database(live, dest_path, standalone=True)


def list_backups() -> list:
    """Return backups newest-first: [{name, size, created}]."""
    if not os.path.isdir(backup_dir()):
        return []
    items = []
    for path in glob.glob(os.path.join(backup_dir(), "chat_history_*.db")):
        name = os.path.basename(path)
        if not _NAME_RE.match(name):
            continue
        st = os.stat(path)
        items.append({
            "name": name,
            "size": st.st_size,
            "created": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    items.sort(key=lambda i: i["name"], reverse=True)
    return items


def prune_backups(keep: int = KEEP) -> list:
    """Delete all but the newest `keep` backups. Returns removed names.

    `keep <= 0` means keep nothing. The old expression (`backups[:-keep]`)
    turned keep=0 into `backups[:0]` — an empty slice — so asking to keep zero
    backups silently kept ALL of them."""
    backups = sorted(glob.glob(os.path.join(backup_dir(), "chat_history_*.db")))
    if keep <= 0:
        doomed = backups
    else:
        doomed = backups[:-keep] if len(backups) > keep else []
    removed = []
    for path in doomed:
        os.remove(path)
        removed.append(os.path.basename(path))
    return removed


def safe_backup_path(name: str):
    """Resolve a backup file name to its path, or None if the name is invalid.

    Two independent guards, because one of them being enough is not something
    a future edit should have to know: (1) the name must match our own
    timestamped pattern — which admits no separator, no dot-dot and no drive
    letter — and (2) the resolved real path must still sit inside the backups
    directory, which also catches a symlink planted inside backups/."""
    if not name or not _NAME_RE.match(name):
        return None
    root = os.path.realpath(backup_dir())
    path = os.path.realpath(os.path.join(root, name))
    if path != root and not path.startswith(root + os.sep):
        return None
    if not os.path.isfile(path):
        return None
    return path


def delete_backup(name: str) -> bool:
    """Delete one backup by name. Returns True if it was removed."""
    path = safe_backup_path(name)
    if not path:
        return False
    os.remove(path)
    return True


# Core tables every valid backup of this app must contain. Used to reject a
# stray/corrupt file before it can overwrite the live database.
_REQUIRED_TABLES = {"dataset", "questions", "settings", "admins"}


def is_valid_backup(path: str) -> bool:
    """True if `path` is a SQLite database that holds our core tables."""
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return False
    except OSError:
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return _REQUIRED_TABLES.issubset({r[0] for r in rows})


def restore_backup(src_path: str) -> str:
    """Replace the live database with the contents of `src_path`.

    Validates the source first, takes a safety backup of the CURRENT database
    (so a bad restore can be undone), then copies the source into the live DB
    via SQLite's online backup API — WAL-safe, no file swapping. Returns the
    safety-backup file name (or "" if there was no live DB to save)."""
    if not is_valid_backup(src_path):
        raise ValueError("فایل پشتیبان معتبر نیست یا ساختار درستی ندارد.")

    live = db_path()
    safety_name = ""
    if os.path.exists(live):
        safety_name = os.path.basename(create_backup())

    copy_database(src_path, live)  # copies the backup INTO the live database
    return safety_name


def main():
    try:
        dest = create_backup()
    except FileNotFoundError as e:
        print(e)
        return
    print(f"Backup written: {dest}")
    for name in prune_backups():
        print(f"Removed old backup: {name}")


if __name__ == "__main__":
    main()
