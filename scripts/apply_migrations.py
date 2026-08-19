#!/usr/bin/env python3
"""Apply the versioned PostgreSQL migrations in `migrations/`.

WHY THIS EXISTS
---------------
`migrations/0001_initial.sql` and `0002_observability.sql` were applied by hand
during the SQLite -> PostgreSQL migration and recorded as a single combined row:

    version = '0001_initial+0002_observability'   checksum = 'sqlite-import'

That worked once, for one operator, on one machine. It is not repeatable, and
the AI control-plane phase adds migrations that must land the same way on every
install. This runner is the smallest thing that fixes that.

DELIBERATELY NOT ALEMBIC
------------------------
Alembic brings autogenerate, a revision graph, branch merges and a downgrade
story. This project has a linear list of hand-written SQL files and no need to
generate schema from models. Adding a migration framework to run six files in
order would be more machinery than the problem deserves. If branching or
programmatic downgrades ever become real requirements, revisit that decision.

There is NO downgrade path here, and that is stated rather than hidden: rolling
back a migration means restoring a backup (`app/services/pg_backup.py`).

GUARANTEES
----------
* Idempotent. Re-running applies nothing and exits 0.
* Each file runs inside its own transaction. A failure leaves that migration
  entirely unapplied rather than half-applied.
* The checksum of every applied file is recorded. If a file is edited after it
  was applied, the runner REFUSES to continue rather than silently ignoring the
  drift — an edited applied migration means two installs have different schemas
  under the same version name.
* Raw psycopg, not `app.db.pg`. The adapter translates SQLite dialect (`?` ->
  `%s`, `PRAGMA` -> no-op, `%` doubling); migration files are native
  PostgreSQL and must reach the server byte-for-byte.

Usage:
    .venv/bin/python scripts/apply_migrations.py            # apply pending
    .venv/bin/python scripts/apply_migrations.py --dry-run  # show pending only
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")

# The hand-applied combined row covers these two files. Treat them as applied
# so this runner never re-executes CREATE TABLE against a populated database.
LEGACY_COMBINED = "0001_initial+0002_observability"
LEGACY_COVERS = ("0001_initial", "0002_observability")


# The application's pool sets this as a connection option (see app/db/pg.py).
# Migrations must resolve unqualified table names to the SAME schemas, or a
# statement like `ALTER TABLE dataset ...` fails with "relation does not
# exist" while the app itself works fine — the schema is `app`, not `public`.
# 0001 and 0002 were applied by hand, so this runner never exercised the path
# until the first migration it actually executed.
SEARCH_PATH = "-c search_path=app,observability,public"


def dsn() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar")


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover():
    """Every migration file, ordered by filename. Returns [(version, path)]."""
    if not os.path.isdir(MIGRATIONS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(MIGRATIONS_DIR)):
        if name.endswith(".sql"):
            out.append((name[:-4], os.path.join(MIGRATIONS_DIR, name)))
    return out


def applied_versions(conn) -> dict:
    """version -> checksum, expanding the legacy combined row."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'app' AND table_name = 'schema_migrations'
        """)
        if cur.fetchone() is None:
            return {}
        cur.execute("SELECT version, checksum FROM app.schema_migrations")
        rows = cur.fetchall()

    out = {}
    for version, csum in rows:
        if version == LEGACY_COMBINED:
            for covered in LEGACY_COVERS:
                out[covered] = "sqlite-import"
        else:
            out[version] = csum
    return out


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    migrations = discover()
    if not migrations:
        print("no migration files found")
        return 0

    with psycopg.connect(dsn(), options=SEARCH_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app.schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                checksum   TEXT NOT NULL
            )
        """)
        conn.commit()

        done = applied_versions(conn)
        pending = []

        for version, path in migrations:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            here = checksum(body)

            if version in done:
                recorded = done[version]
                # 'sqlite-import' predates checksumming; nothing to compare against.
                if recorded not in (here, "sqlite-import"):
                    print(f"REFUSING TO CONTINUE: {version}.sql changed after it "
                          f"was applied.\n  recorded {recorded}\n  on disk  {here}\n"
                          "  An applied migration must never be edited — write a "
                          "new migration instead.", file=sys.stderr)
                    return 2
                continue
            pending.append((version, path, body, here))

        if not pending:
            print(f"up to date — {len(done)} migration(s) already applied")
            return 0

        print(f"{len(pending)} pending migration(s):")
        for version, _p, _b, _c in pending:
            print(f"  - {version}")
        if dry_run:
            print("(dry run — nothing applied)")
            return 0

        for version, path, body, here in pending:
            print(f"applying {version} ...", end=" ", flush=True)
            try:
                with conn.transaction():
                    conn.execute(body)
                    conn.execute(
                        "INSERT INTO app.schema_migrations (version, checksum) "
                        "VALUES (%s, %s)", (version, here))
            except Exception as exc:                      # noqa: BLE001
                print("FAILED")
                print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
                print("  This migration was rolled back in full; earlier "
                      "migrations remain applied.", file=sys.stderr)
                return 1
            print("ok")

    print(f"applied {len(pending)} migration(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
