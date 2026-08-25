#!/usr/bin/env python3
"""Operator-only script: back up the database and reset content to the
bundled INOTEX defaults.

WHY THIS EXISTS
---------------
The application NEVER deletes or overwrites existing customer content
automatically — `init_db()` only seeds a brand-new, empty database. If an
operator wants to wipe an old install (e.g. one that still holds content from
a previous event) and start from the verifiable INOTEX defaults sourced from
inotex.com, they must do it explicitly. This script is that explicit, safe path.

SAFETY
------
1. It ALWAYS creates a backup before touching anything — a timestamped file
   copy on SQLite, a `pg_dump` archive on PostgreSQL. If the backup cannot be
   written, the script aborts.
2. It requires interactive confirmation (or --yes) and shows exactly what will
   be reset.
3. By default it resets the content tables only (dataset, questions, synonyms).
   Admin accounts, chat logs and settings (theme, branding) are preserved.
   Use --full to also clear chat logs; use --all to additionally reset settings.

USAGE (run from the project root)
--------------------------------
    python3 scripts/reset-content-to-defaults.py            # interactive, content only
    python3 scripts/reset-content-to-defaults.py --yes      # non-interactive (CI/ops)
    python3 scripts/reset-content-to-defaults.py --full     # also clear chat logs
    python3 scripts/reset-content-to-defaults.py --db /path/to/chat_history.db
                                       # (SQLite file override; PostgreSQL is
                                       #  selected automatically from the env)

After running, restart the app so the in-memory search index reloads.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Make `import app.*` work when run from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_BACKEND  # noqa: E402
from app.default_content import (  # noqa: E402
    INOTEX_DATASET,
    INOTEX_QUESTIONS,
    INOTEX_SYNONYMS,
    seed_default_content,
    seed_default_synonyms,
)
from app.db.connection import ensure_dataset_columns, get_db_connection  # noqa: E402


def find_db_path(explicit: str) -> Path:
    if explicit:
        return Path(explicit).resolve()
    # Mirror app.config.BASE_DIR resolution.
    base = Path(__file__).resolve().parent.parent
    return (base / "chat_history.db").resolve()


def backup_db(db_path: Path) -> Path:
    if not db_path.exists():
        sys.exit(f"✗ Database not found: {db_path}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = db_path.with_name(f"{db_path.stem}.backup.{stamp}{db_path.suffix}")
    # SQLite-aware copy: copy the main DB file. WAL/SHM (if present) are
    # checkpointed into a fresh connection first so no committed data is lost.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        conn.close()
    shutil.copy2(db_path, dest)
    return dest


def backup_postgres() -> str:
    from app.services import pg_backup
    manifest = pg_backup.create(reason="reset-content")
    return f"backups/postgres/{manifest['backup_id']}/{manifest['file']}"


def row_counts(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def reset_content(conn):
    """Replace dataset/questions/synonyms with the INOTEX defaults."""
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM synonyms")
    cur = conn.cursor()
    if DB_BACKEND != "postgres":
        # The DB being reset may predate the bilingual columns. On PostgreSQL
        # migrations/0004 already owns the columns, and ensure_dataset_columns
        # speaks only the SQLite dialect.
        ensure_dataset_columns(cur)
    seed_default_content(cur)
    seed_default_synonyms(cur)
    conn.commit()


def main():
    p = argparse.ArgumentParser(description="Reset DB content to INOTEX defaults (with backup).")
    p.add_argument("--db", default="", help="Path to a SQLite database (default: ./chat_history.db)."
                                            " Ignored when the configured backend is PostgreSQL.")
    p.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    p.add_argument("--full", action="store_true", help="Also clear chat_logs.")
    p.add_argument("--all", action="store_true", help="Also clear settings (branding/theme). Implies --full.")
    args = p.parse_args()

    use_postgres = DB_BACKEND == "postgres" and not args.db
    if use_postgres:
        conn = get_db_connection()
        print("Database : PostgreSQL (configured backend)")
        backup = backup_postgres()
    else:
        db_path = find_db_path(args.db)
        print(f"Database : {db_path}")
        backup = backup_db(db_path)
        conn = sqlite3.connect(str(db_path))
    print(f"Backup   : {backup}")

    scope = []
    scope.append("dataset, questions, synonyms  →  INOTEX defaults")
    if args.full or args.all:
        scope.append("chat_logs  →  cleared")
    if args.all:
        scope.append("settings   →  cleared (theme/branding return to defaults on next boot)")
    print("Will reset:")
    for s in scope:
        print("   • " + s)

    if not args.yes:
        try:
            answer = input("\nType RESET to proceed: ").strip()
        except EOFError:
            answer = ""
        if answer != "RESET":
            conn.close()
            print("Aborted. No changes were made.")
            return

    print("\nResetting content ...")
    if args.full or args.all:
        conn.execute("DELETE FROM chat_logs")
    if args.all:
        # Keep core keys the app needs; clear everything else.
        conn.execute("DELETE FROM settings")
    reset_content(conn)

    conn.commit()
    print("  dataset   :", row_counts(conn, "dataset"), "rows")
    print("  questions :", row_counts(conn, "questions"), "rows")
    print("  synonyms  :", row_counts(conn, "synonyms"), "rows")
    conn.close()

    print("\n✓ Done. Restart the application so the search index reloads.")
    print(f"  (Your previous data is safe in: {backup})")


if __name__ == "__main__":
    main()
