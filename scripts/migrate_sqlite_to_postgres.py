"""Migrate both SQLite databases into PostgreSQL, then PROVE the result.

Run:
    .venv/bin/python scripts/migrate_sqlite_to_postgres.py            # migrate + validate
    .venv/bin/python scripts/migrate_sqlite_to_postgres.py --validate # validate only

Why this is not a loop of INSERTs
---------------------------------
SQLite stored every timestamp as TEXT, every boolean as 0/1, and log metadata
as a JSON string. Copying those straight across would rebuild the SQLite
limitations inside PostgreSQL. So each column is coerced to the type the
PostgreSQL schema actually declares, and anything that will not coerce is
reported rather than silently dropped.

The script is idempotent: it TRUNCATEs each destination table inside one
transaction before loading it, so a re-run converges instead of duplicating.
The SQLite files are opened READ-ONLY and are never written.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

APP_TABLES = {
    "admins": ("username", "password_hash", "salt", "security_question", "security_answer_hash"),
    "settings": ("key", "value"),
    "dataset": ("id", "title", "text", "video_url", "title_en", "text_en"),
    "questions": ("id", "question", "dataset_id", "video_url"),
    "synonyms": ("source", "target"),
    "chat_logs": ("id", "query", "response", "response_type", "source",
                  "confidence", "tokens", "cost", "created_at"),
    "admin_sessions": ("token", "username", "expiry"),
    "otp_challenges": ("id", "destination", "code_hmac", "expires_at", "attempts",
                       "resends", "used", "created_at", "last_sent_at",
                       "first_name", "last_name", "job", "position", "interests"),
}

LOG_COLUMNS = ("id", "created_at", "level", "category", "subcategory", "event_name",
               "message", "outcome", "actor", "actor_type", "target", "ip",
               "user_agent", "provider", "model", "route", "http_method",
               "http_status", "duration_ms", "tokens_in", "tokens_out", "cost",
               "retry_count", "error_type", "error_code", "stack", "request_id",
               "correlation_id", "conversation_id", "metadata")
LOG_TABLES = ("app_logs", "audit_logs", "security_events", "service_events")

TIMESTAMP_COLUMNS = {"created_at", "expiry", "expires_at", "last_sent_at"}
BOOLEAN_COLUMNS = {"used"}
JSON_COLUMNS = {"metadata"}

# admin_sessions is migrated but is NOT load-bearing: a session token is a
# short-lived credential and re-authenticating is harmless. Listed so the
# parity report stays complete.
ORDER_APP = ("admins", "settings", "dataset", "questions", "synonyms",
             "chat_logs", "admin_sessions", "otp_challenges")


def dsn() -> str:
    return os.getenv("DATABASE_URL",
                     "postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar")


def _ts(value):
    """SQLite TEXT timestamp -> aware datetime. Naive values are UTC.

    The application wrote `datetime.utcnow().isoformat()` throughout, so a
    naive string genuinely means UTC. Assuming local time here would silently
    shift every historical record by the machine's offset.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat,
                  lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S"),
                  lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")):
        try:
            dt = parse(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    raise ValueError(f"unparseable timestamp: {value!r}")


def _bool(value):
    if value in (None, ""):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "t", "yes", "y")


def _json(value):
    """SQLite stored metadata as a JSON string (or ''). JSONB wants a value."""
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return psycopg.types.json.Jsonb(value)
    try:
        return psycopg.types.json.Jsonb(json.loads(value))
    except (ValueError, TypeError):
        # Not valid JSON: keep it as a JSON string rather than lose it.
        return psycopg.types.json.Jsonb({"_raw": str(value)})


NOT_NULL_TEXT = {
    "salt", "security_answer_hash", "title", "text", "video_url", "title_en",
    "text_en", "value", "query", "response", "response_type", "source",
    "first_name", "last_name", "job", "position", "interests", "level",
    "category", "subcategory", "event_name", "message", "outcome", "actor",
    "actor_type", "target", "ip", "user_agent", "provider", "model", "route",
    "http_method", "error_type", "error_code", "stack", "request_id",
    "correlation_id", "conversation_id",
}


def coerce(column: str, value):
    if value is None and column in NOT_NULL_TEXT:
        return ""
    if column in TIMESTAMP_COLUMNS:
        return _ts(value)
    if column in BOOLEAN_COLUMNS:
        return _bool(value)
    if column in JSON_COLUMNS:
        return _json(value)
    return value


def sqlite_ro(path: str):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def count_sqlite(path, table):
    if not os.path.exists(path):
        return 0
    con = sqlite_ro(path)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def copy_table(sqlite_path, pg, schema, table, columns):
    """Load one table inside a transaction. Returns rows written."""
    if not os.path.exists(sqlite_path):
        return 0
    con = sqlite_ro(sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()

    with pg.cursor() as cur:
        cur.execute(f"TRUNCATE {schema}.{table} CASCADE")
        if not rows:
            return 0
        available = [c for c in columns if c in rows[0].keys()]
        placeholders = ", ".join(["%s"] * len(available))
        collist = ", ".join(f'"{c}"' for c in available)
        payload = [tuple(coerce(c, r[c]) for c in available) for r in rows]
        cur.executemany(
            f"INSERT INTO {schema}.{table} ({collist}) VALUES ({placeholders})",
            payload)
        # Identity columns were fed explicit ids; realign the sequence or the
        # next natural insert collides with a migrated row.
        if "id" in available:
            cur.execute(
                "SELECT pg_get_serial_sequence(%s, 'id')", (f"{schema}.{table}",))
            sequence = cur.fetchone()[0]
            if sequence:
                cur.execute(
                    f"SELECT setval(%s, COALESCE((SELECT MAX(id) FROM {schema}.{table}), 1))",
                    (sequence,))
    return len(rows)


def migrate(validate_only=False):
    from app.config import DB_PATH, LOGS_DB_PATH
    report, ok = [], True

    with psycopg.connect(dsn()) as pg:
        pg.execute("SET search_path TO app, observability, public")

        plan = ([("app", t, APP_TABLES[t], DB_PATH) for t in ORDER_APP] +
                [("observability", t, LOG_COLUMNS, LOGS_DB_PATH) for t in LOG_TABLES])

        for schema, table, columns, source in plan:
            src_n = count_sqlite(source, table)
            if not validate_only:
                try:
                    copy_table(source, pg, schema, table, columns)
                    pg.commit()
                except Exception as e:
                    pg.rollback()
                    report.append((f"{schema}.{table}", src_n, "ERROR", f"FAIL: {type(e).__name__}: {e}"))
                    ok = False
                    continue
            dst_n = pg.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]
            verdict = "OK" if src_n == dst_n else "MISMATCH"
            if verdict != "OK":
                ok = False
            report.append((f"{schema}.{table}", src_n, dst_n, verdict))

        if not validate_only:
            pg.execute(
                "INSERT INTO app.schema_migrations (version, checksum) VALUES (%s, %s)"
                " ON CONFLICT (version) DO NOTHING",
                ("0001_initial+0002_observability", "sqlite-import"))
            pg.commit()

    width = max(len(r[0]) for r in report)
    print(f"\n{'TABLE'.ljust(width)}   {'SQLITE':>8} {'POSTGRES':>9}   RESULT")
    print("-" * (width + 32))
    for name, src, dst, verdict in report:
        print(f"{name.ljust(width)}   {str(src):>8} {str(dst):>9}   {verdict}")
    print("-" * (width + 32))
    print(("ALL TABLES MATCH" if ok else "PARITY FAILURE — see MISMATCH rows above") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--validate", action="store_true",
                    help="compare counts without copying")
    sys.exit(migrate(validate_only=ap.parse_args().validate))
