#!/usr/bin/env python3
"""Import the guide knowledge from the crawler's `crawl` schema into the
app's own tables.

    .venv/bin/python scripts/import-guide-from-crawl.py            # dry run
    .venv/bin/python scripts/import-guide-from-crawl.py --apply     # writes

SOURCE
------
The crawler (a separate service) fills five tables in the `crawl` schema of
the production PostgreSQL database (padyar_elecomp), crawled 2026-08-31:

    crawl.guide_facts(key, value)            hours, dates, weather, ...
    crawl.gates(name, gate_type, route_text) venue entrances + parking
    crawl.stations(name, kind, line, description, lat, lng)
                                              metro / BRT stations nearby
    crawl.restaurants(id, name, cuisine, area, distance, note, links, in_venue)
    crawl.news(slug, title, date_iso, date_jalali, summary, body, featured)

The chatbot must answer from the APP's own tables (migrations/
0020_guide_tables.sql) — the owner explicitly wants one table per entity,
never loose dataset rows — so this script copies each crawl row across with
INSERT ... ON CONFLICT (pk) DO UPDATE. Reading and writing both go through
the app layer (app.db.connection), so the app tables can live on PostgreSQL
(production) or SQLite (a test run) unchanged. The crawl tables themselves
are only ever READ.

SAFETY
------
Dry-run by default: reports what it found, writes nothing. --apply upserts
row by row — a re-run refreshes every value in place and never duplicates
(see the idempotency test in tests/test_guide_service.py). The same guards
as scripts/import-content.py: SEED_DEFAULT_CONTENT is forced off and the
admin seed is patched out, so an import never squeezes starter content or a
default-credential admin row into the target database.

On PostgreSQL, run scripts/apply_migrations.py FIRST (the app tables must
exist) and set DB_BACKEND=postgres plus DATABASE_URL — the sqlite default
here is deliberate, so a slip cannot point an import at production by
accident.

ROLLBACK
--------
There is no downgrade to run: the app.* tables are ours now, and the crawl
side was never modified. If a crawl turns out bad, fix the crawler and
re-run this import — the upserts replace every value with the fresh ones.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Same pins as scripts/import-content.py, for the same reasons: never pick a
# production backend by accident, and never SEED while importing.
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("SEED_DEFAULT_CONTENT", "false")
os.environ.setdefault("OPENAI_API_KEY", "import")

# crawl table -> (SELECT, target table, columns, primary key). The column
# order is the contract read_crawl() returns and the upsert consumes.
_GUIDE_TABLES = (
    ("facts", "SELECT key, value FROM crawl.guide_facts",
     "guide_facts", ("key", "value"), "key"),
    ("gates", "SELECT name, gate_type, route_text FROM crawl.gates",
     "gates", ("name", "gate_type", "route_text"), "name"),
    ("stations",
     "SELECT name, kind, line, description, lat, lng FROM crawl.stations",
     "stations", ("name", "kind", "line", "description", "lat", "lng"),
     "name"),
    ("restaurants",
     "SELECT id, name, cuisine, area, distance, note, links, in_venue"
     " FROM crawl.restaurants",
     "restaurants",
     ("id", "name", "cuisine", "area", "distance", "note", "links",
      "in_venue"), "id"),
    ("news", "SELECT slug, title, date_iso, date_jalali, summary, body,"
             " featured FROM crawl.news",
     "news", ("slug", "title", "date_iso", "date_jalali", "summary",
              "body", "featured"), "slug"),
)


def read_crawl(conn) -> dict:
    """Every crawl row, per table, as tuples in _GUIDE_TABLES column order.

    The crawl schema exists only on the production PostgreSQL database (the
    SQLite test backend has no crawl tables), so tests monkeypatch THIS
    function — see tests/test_guide_service.py. A missing crawl table on a
    real run is a stop, not a skip: half-imported guide knowledge would
    answer some kinds and silently drop others.
    """
    out = {}
    for name, sql, _t, cols, _pk in _GUIDE_TABLES:
        try:
            # The pg layer returns rows as DICTS (the app-wide convention,
            # r["column"]) — tuple(r) on a dict yields its KEYS, which is
            # how the first production run fed the literal string "lat"
            # into the lat column and died on the double-precision cast.
            # Order every row by the table's own column contract instead.
            out[name] = [tuple(r[c] for c in cols)
                         for r in conn.execute(sql).fetchall()]
        except Exception as e:  # noqa: BLE001 — any backend, any dialect
            sys.exit(f"cannot read the crawl tables ({type(e).__name__}: {e}).\n"
                     "The crawl schema lives only on the production"
                     " PostgreSQL database — run this script with"
                     " DB_BACKEND=postgres and DATABASE_URL set.")
    return out


def _normalize(name: str, row: tuple) -> tuple:
    """Coerce one crawl row to the app table's portable shape.

    jsonb `links` arrives from psycopg as a Python object and must be TEXT.
    The BOOLEAN columns stay Python bools ON PURPOSE: psycopg adapts them
    to PostgreSQL boolean, and SQLite stores Python bools natively as 0/1 —
    the int(bool(...)) cast this used to do fed SMALLINT into a PostgreSQL
    BOOLEAN column and died with DatatypeMismatch on the first real run.
    """
    row = list(row)
    if name == "restaurants":
        links = row[6]
        if not isinstance(links, str):
            links = json.dumps(links if links is not None else [],
                               ensure_ascii=False)
        row[6] = links
        row[7] = bool(row[7])
    elif name == "news":
        row[6] = bool(row[6])
    return tuple(row)


def _upsert_sql(table: str, columns, pk: str) -> str:
    placeholders = ", ".join(["?"] * len(columns))
    assignments = ", ".join(f"{c} = excluded.{c}"
                            for c in columns if c != pk)
    return (f"INSERT INTO {table} ({', '.join(columns)})"
            f" VALUES ({placeholders})"
            f" ON CONFLICT({pk}) DO UPDATE SET {assignments}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Copy the crawl.* guide tables into the app's own"
                    " guide tables (upsert).")
    p.add_argument("--apply", action="store_true",
                   help="actually write; without it everything is a dry-run")
    args = p.parse_args(argv)

    import app.config as appconfig
    from app.db import connection as dbconn
    from app.db.connection import get_db_connection

    # An import must not create login accounts or seed starter content (same
    # guards, same reasons as scripts/import-content.py).
    _real_seed_admin = dbconn._seed_admin
    dbconn._seed_admin = lambda cursor: None
    _real_seed_content = appconfig.SEED_DEFAULT_CONTENT
    appconfig.SEED_DEFAULT_CONTENT = False
    try:
        dbconn.init_db()
    finally:
        dbconn._seed_admin = _real_seed_admin
        appconfig.SEED_DEFAULT_CONTENT = _real_seed_content

    conn = get_db_connection()
    try:
        data = read_crawl(conn)

        written = {}
        for name, sql, table, columns, pk in _GUIDE_TABLES:
            rows = data[name]
            written[name] = len(rows)
            sample = rows[0][0] if rows else "-"
            print(f"{table:<13} {len(rows):>4} rows   (e.g. {str(sample)[:40]!r})")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to import.")
            return 0

        for name, sql, table, columns, pk in _GUIDE_TABLES:
            statement = _upsert_sql(table, columns, pk)
            for row in data[name]:
                conn.execute(statement, _normalize(name, row))
        conn.commit()

        print("\nAPPLIED (rows now in the app tables):")
        for name, sql, table, columns, pk in _GUIDE_TABLES:
            n = conn.execute(f"SELECT COUNT(*) AS c FROM {table}"
                             ).fetchone()["c"]
            print(f"{table:<13} {n:>4}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
