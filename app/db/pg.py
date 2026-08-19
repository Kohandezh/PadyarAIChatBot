"""PostgreSQL access layer with a SQLite-shaped surface.

WHY AN ADAPTER AND NOT A REWRITE
--------------------------------
62 call sites use `get_db_connection()` and were written against sqlite3:
`?` placeholders, `conn.execute(...)` directly on the connection, `row["col"]`
AND `row[0]` access, and `cursor.lastrowid`. Rewriting all of them in one
change would be a very large, untestable diff across the whole application.

So this module presents the same surface on top of psycopg 3. Business code
keeps working unchanged; the SQLite-specific dialect is translated here, in one
auditable place. That is the seam the migration needed — not 62 edits.

WHAT IS TRANSLATED
------------------
  ?                     -> %s          (parameter placeholders)
  INSERT OR IGNORE      -> ON CONFLICT DO NOTHING
  INSERT OR REPLACE     -> ON CONFLICT ... DO UPDATE
  datetime('now', ...)  -> now() + interval
  PRAGMA ...            -> no-op (SQLite tuning has no PostgreSQL equivalent)
  cursor.lastrowid      -> RETURNING id, captured on execute

Placeholder translation is LITERAL-AWARE: a `?` inside a quoted string is left
alone. Naive replacement would corrupt any query containing a question mark in
Persian text — and this knowledge base is full of questions.

CONNECTION POOLING
------------------
One process-wide pool. The previous design opened a fresh connection per call;
against PostgreSQL that is a TCP + auth round-trip per query and would be far
slower than the SQLite it replaced.
"""
import os
import re
import threading

import psycopg
from psycopg import sql  # noqa: F401  (kept for callers building safe identifiers)
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import logger

_pool = None
_pool_lock = threading.Lock()


def dsn() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar")


def pool() -> ConnectionPool:
    """The process-wide pool, created once."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=dsn(),
                    min_size=int(os.getenv("DB_POOL_MIN_SIZE", "2")),
                    max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
                    timeout=float(os.getenv("DB_CONNECT_TIMEOUT", "10")),
                    max_lifetime=float(os.getenv("DB_POOL_MAX_LIFETIME", "1800")),
                    # search_path is set as a CONNECTION OPTION, not via a
                    # configure callback running SET: that leaves the
                    # connection INTRANS and psycopg's pool discards it, which
                    # exhausts the pool and every checkout times out.
                    kwargs={"options": "-c search_path=app,observability,public"},
                    open=True,
                    name="padyar",
                )
                logger.info("[pg] pool opened min=%s max=%s",
                            _pool.min_size, _pool.max_size)
    return _pool


def pool_stats() -> dict:
    try:
        s = pool().get_stats()
        return {"size": s.get("pool_size"), "available": s.get("pool_available"),
                "waiting": s.get("requests_waiting"), "min": pool().min_size,
                "max": pool().max_size}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


# ── Dialect translation ─────────────────────────────────────────────────

_PRAGMA_RE = re.compile(r"^\s*PRAGMA\b", re.I)
_DATETIME_NOW_RE = re.compile(
    r"datetime\(\s*'now'\s*(?:,\s*'([+-]?\d+)\s+(\w+)'\s*)?\)", re.I)


def _swap_placeholders(query: str) -> str:
    """`?` -> `%s`, skipping anything inside a quoted literal.

    A blind replace would rewrite the `?` in Persian question text stored in
    this knowledge base, producing broken SQL or a silently wrong query.
    """
    out, quote = [], None
    for ch in query:
        if quote:
            # A literal % must be doubled even INSIDE a string literal: psycopg
            # scans the whole query for % when parameters are supplied, so
            # `LIKE '%foo%'` with a bound parameter raises without this.
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")          # a literal % must survive psycopg
        else:
            out.append(ch)
    return "".join(out)


def translate(query: str) -> str:
    q = query
    if _PRAGMA_RE.match(q):
        return "SELECT 1 WHERE false"     # PRAGMA has no PostgreSQL meaning

    q = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", q, flags=re.I)
    q = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", q, flags=re.I)

    def _dt(m):
        if not m.group(1):
            return "now()"
        return f"now() + interval '{m.group(1)} {m.group(2)}'"
    q = _DATETIME_NOW_RE.sub(_dt, q)

    return _swap_placeholders(q)


def needs_on_conflict(original: str) -> str:
    """`OR IGNORE`/`OR REPLACE` lose their meaning once rewritten; give the
    statement the PostgreSQL equivalent instead of silently dropping it."""
    if re.search(r"INSERT\s+OR\s+IGNORE", original, re.I):
        return " ON CONFLICT DO NOTHING"
    return ""



_PK_CACHE: dict = {}


def _primary_key(conn, table: str):
    """The primary-key columns of `table`, cached per process.

    Needed to translate SQLite's `INSERT OR REPLACE` into PostgreSQL's
    `ON CONFLICT (...) DO UPDATE`. The conflict target must name real
    constraint columns, and they differ per table — `settings` is keyed on
    (key) while `synonyms` is keyed on (source, target). Guessing "first
    column" would silently corrupt one of them.
    """
    key = table.lower()
    if key in _PK_CACHE:
        return _PK_CACHE[key]
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = to_regclass(%s) AND i.indisprimary
            ORDER BY a.attnum""", (table,))
        cols = [r["attname"] for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        cols = []
    _PK_CACHE[key] = cols
    return cols


_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+([A-Za-z_][\w.]*)\s*\(([^)]*)\)", re.I)

_GENERATED_ID_CACHE = {}


def _generated_id_column(conn, table: str):
    """The auto-generated key column of `table`, or None. Cached per process.

    "Auto-generated" means a serial/identity column — one with a sequence
    behind it (`pg_get_serial_sequence` is non-null) or declared
    `GENERATED ... AS IDENTITY`. Those are the only inserts that can produce a
    new id worth reporting as `lastrowid`.

    Tables keyed on supplied values — `settings` on (key), `synonyms` on
    (source, target), `dataset` on a TEXT id — have no such column, and for
    them `lastrowid` must be None rather than some other table's number.
    """
    key = table.lower()
    if key in _GENERATED_ID_CACHE:
        return _GENERATED_ID_CACHE[key]
    col = None
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.attname
              FROM pg_attribute a
              JOIN pg_class c ON c.oid = a.attrelid
             WHERE a.attrelid = to_regclass(%s)
               AND a.attnum > 0
               AND NOT a.attisdropped
               AND (a.attidentity IN ('a', 'd')
                    OR pg_get_serial_sequence(%s, a.attname) IS NOT NULL)
             ORDER BY a.attnum
             LIMIT 1""", (table, table))
        row = cur.fetchone()
        if row:
            col = row["attname"]
    except Exception:  # noqa: BLE001 — unknown table, or no permission
        col = None
    _GENERATED_ID_CACHE[key] = col
    return col


def _upsert_clause(conn, translated: str) -> str:
    """Build the ON CONFLICT clause an `INSERT OR REPLACE` needs."""
    m = _INSERT_RE.search(translated)
    if not m:
        return ""
    table = m.group(1)
    columns = [c.strip().strip('"') for c in m.group(2).split(",")]
    pk = _primary_key(conn, table)
    if not pk:
        return ""
    target = ", ".join(f'"{c}"' for c in pk)
    updatable = [c for c in columns if c.lower() not in {k.lower() for k in pk}]
    if not updatable:
        # Every inserted column is part of the key (e.g. synonyms): there is
        # nothing to update, and REPLACE degenerates to "ensure present".
        return f" ON CONFLICT ({target}) DO NOTHING"
    assignments = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in updatable)
    return f" ON CONFLICT ({target}) DO UPDATE SET {assignments}"


class Row(dict):
    """A row that answers to BOTH `row["col"]` and `row[0]`.

    sqlite3.Row supported both and the codebase uses both; psycopg's dict_row
    supports only the first. Without this, roughly half the call sites would
    raise KeyError on an integer index.
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def keys(self):
        return super().keys()


def _as_rows(records):
    return [Row(r) for r in records]


class Cursor:
    """Thin cursor exposing the sqlite3 methods the codebase calls."""

    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return _as_rows(self._cur.fetchall())

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def description(self):
        return self._cur.description


class Connection:
    """A pooled PostgreSQL connection wearing the sqlite3 Connection surface."""

    def __init__(self):
        self._cm = pool().connection()
        self._conn = self._cm.__enter__()
        self._conn.row_factory = dict_row
        self._closed = False

    def execute(self, query, params=()):
        translated = translate(query)
        if "ON CONFLICT" not in translated.upper():
            if re.search(r"INSERT\s+OR\s+REPLACE", query, re.I):
                translated += _upsert_clause(self._conn, translated)
            else:
                translated += needs_on_conflict(query)
        # sqlite3 exposes `lastrowid`; PostgreSQL has no equivalent, so it is
        # emulated with RETURNING — appended to THIS statement, so the value
        # can only ever describe the row THIS insert created.
        #
        # It previously ran `SELECT lastval()` after the insert. `lastval()` is
        # SESSION-scoped, not statement-scoped: it reports the last sequence
        # touched anywhere on the connection. Because connections are pooled and
        # reused, an insert into a sequence-less table (`settings`, `synonyms`,
        # `dataset` — all keyed on supplied values) would return the id of some
        # earlier, unrelated insert on that same connection instead of None.
        # Proven in review: insert into `ai_provider_models`, then into
        # `ai_circuit_state`, and the second reported the model row's id.
        #
        # RETURNING also removes the SAVEPOINT that used to wrap the probe. That
        # savepoint existed only because `lastval()` raises on a sequence-less
        # table, and a raised statement aborts the entire PostgreSQL
        # transaction. Asking the catalog first means nothing has to fail.
        returning_col = None
        if re.match(r"\s*INSERT", translated, re.I) and \
                not re.search(r"\bRETURNING\b", translated, re.I):
            m = _INSERT_RE.search(translated)
            if m:
                returning_col = _generated_id_column(self._conn, m.group(1))
                if returning_col:
                    translated += f' RETURNING "{returning_col}"'

        cur = self._conn.cursor()
        cur.execute(translated, tuple(params) if params else None)

        lastrowid = None
        if returning_col and cur.rowcount:
            try:
                row = cur.fetchone()
                # An `ON CONFLICT DO NOTHING` that skipped returns no row, so
                # lastrowid is correctly None — nothing was inserted.
                lastrowid = row[returning_col] if row else None
            except Exception:  # noqa: BLE001 — no result set to read
                lastrowid = None
        return Cursor(cur, lastrowid)

    def executemany(self, query, seq):
        cur = self._conn.cursor()
        cur.executemany(translate(query), [tuple(p) for p in seq])
        return Cursor(cur)

    def cursor(self):
        return self

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._cm.__exit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            logger.error("[pg] returning connection failed: %s", type(e).__name__)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def connect() -> Connection:
    return Connection()


def healthy() -> tuple:
    """(ok, detail) — cheap liveness probe for the health service."""
    try:
        with pool().connection() as c:
            c.execute("SELECT 1")
        return True, "reachable"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__
