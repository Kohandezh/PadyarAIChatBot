"""PostgreSQL-native database administration.

Replaces the SQLite-only operations that became meaningless at cutover:
`PRAGMA integrity_check`, `wal_checkpoint` and SQLite's `VACUUM` describe a
storage engine this application no longer uses. Leaving them on the page would
be worse than removing them — an operator would run "integrity check", see a
green tick, and believe something had been verified.

WHAT IS OFFERED INSTEAD, AND WHY EACH IS SAFE
---------------------------------------------
  connectivity   SELECT 1 plus pool statistics
  statistics     ANALYZE — refreshes the planner's statistics; safe, online
  table_stats    pg_stat_user_tables — live/dead tuples, last autovacuum
  index_stats    pg_stat_user_indexes — spots indexes nothing ever uses
  bloat_estimate n_dead_tup per table, which is what actually drives VACUUM
  migration      which migrations have been applied
  activity       long-running queries, waiting locks, connection counts

DELIBERATELY NOT OFFERED
------------------------
  VACUUM FULL  — takes an ACCESS EXCLUSIVE lock and rewrites the table; it
                 would freeze the chatbot for the duration. Routine VACUUM is
                 autovacuum's job and it is already running.
  REINDEX      — same locking problem.
  raw SQL      — prohibited outright; there is no code path that executes a
                 caller-supplied statement.

Every statement below is a module-level constant. The only dynamic values are
bound parameters.
"""
import time

from app.services import applog

# Statements are constants. Nothing here interpolates caller input into SQL.
_Q_VERSION = "SELECT version()"
_Q_DBNAME = "SELECT current_database()"
_Q_DBSIZE = "SELECT pg_database_size(current_database())"
_Q_SCHEMA_SIZE = """
    SELECT n.nspname AS schema,
           COALESCE(SUM(pg_total_relation_size(c.oid)), 0) AS bytes,
           COUNT(*) FILTER (WHERE c.relkind = 'r') AS tables
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('app', 'observability')
    GROUP BY n.nspname ORDER BY n.nspname"""
_Q_TABLE_STATS = """
    SELECT schemaname AS schema, relname AS table_name,
           n_live_tup AS live_rows, n_dead_tup AS dead_rows,
           last_autovacuum, last_autoanalyze,
           pg_total_relation_size(relid) AS bytes
    FROM pg_stat_user_tables
    WHERE schemaname IN ('app', 'observability')
    ORDER BY pg_total_relation_size(relid) DESC"""
_Q_INDEX_STATS = """
    SELECT schemaname AS schema, relname AS table_name,
           indexrelname AS index_name, idx_scan AS scans,
           pg_relation_size(indexrelid) AS bytes
    FROM pg_stat_user_indexes
    WHERE schemaname IN ('app', 'observability')
    ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC"""
_Q_ACTIVITY = """
    SELECT COUNT(*) AS total,
           COUNT(*) FILTER (WHERE state = 'active') AS active,
           COUNT(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_txn,
           COUNT(*) FILTER (WHERE wait_event_type = 'Lock') AS waiting_on_lock,
           COALESCE(MAX(EXTRACT(EPOCH FROM (now() - query_start)))
                    FILTER (WHERE state = 'active'), 0) AS longest_query_seconds
    FROM pg_stat_activity WHERE datname = current_database()"""
_Q_SETTINGS = """
    SELECT name, setting, unit FROM pg_settings
    WHERE name IN ('max_connections','shared_buffers','work_mem',
                   'statement_timeout','idle_in_transaction_session_timeout',
                   'server_encoding','ssl')"""
_Q_MIGRATIONS = "SELECT version, applied_at FROM app.schema_migrations ORDER BY applied_at"


def _conn():
    from app.db import pg
    return pg.connect()


def overview() -> dict:
    """Engine facts for the admin page. No filesystem paths are exposed."""
    from app.db import pg
    c = _conn()
    try:
        version = c.execute(_Q_VERSION).fetchone()[0]
        out = {
            "engine": "PostgreSQL",
            "version": version.split(" on ")[0].replace("PostgreSQL ", ""),
            "database": c.execute(_Q_DBNAME).fetchone()[0],
            "size_bytes": c.execute(_Q_DBSIZE).fetchone()[0],
            "schemas": [dict(r) for r in c.execute(_Q_SCHEMA_SIZE).fetchall()],
            "activity": dict(c.execute(_Q_ACTIVITY).fetchone()),
            "settings": {r["name"]: (str(r["setting"]) + (" " + r["unit"] if r["unit"] else ""))
                         for r in c.execute(_Q_SETTINGS).fetchall()},
            "pool": pg.pool_stats(),
        }
        try:
            out["migrations"] = [dict(r) for r in c.execute(_Q_MIGRATIONS).fetchall()]
        except Exception:  # noqa: BLE001 — table absent on a fresh install
            out["migrations"] = []
        return out
    finally:
        c.close()


def table_stats() -> list:
    c = _conn()
    try:
        return [dict(r) for r in c.execute(_Q_TABLE_STATS).fetchall()]
    finally:
        c.close()


def index_stats() -> list:
    """Indexes ordered by LEAST used first — an index with 0 scans costs write
    throughput and buys nothing."""
    c = _conn()
    try:
        return [dict(r) for r in c.execute(_Q_INDEX_STATS).fetchall()]
    finally:
        c.close()


# ── Allowlisted maintenance ─────────────────────────────────────────────

def _check_connectivity():
    from app.db import pg
    ok, detail = pg.healthy()
    if not ok:
        raise RuntimeError(detail)
    return f"اتصال برقرار است · استخر {pg.pool_stats().get('size')}"


def _refresh_statistics():
    """ANALYZE. Safe online: it takes only a lightweight lock and is what the
    query planner needs after a bulk import such as the migration."""
    c = _conn()
    try:
        c.execute("ANALYZE")
        c.commit()
    finally:
        c.close()
    return "آمار برنامه‌ریز پرس‌وجو به‌روزرسانی شد."


def _bloat_report():
    rows = table_stats()
    worst = sorted(rows, key=lambda r: r.get("dead_rows") or 0, reverse=True)[:3]
    if not worst or (worst[0].get("dead_rows") or 0) == 0:
        return "هیچ ردیف مردهٔ قابل توجهی وجود ندارد؛ autovacuum کار می‌کند."
    return " · ".join(f"{r['table_name']}: {r['dead_rows']} ردیف مرده" for r in worst)


ACTIONS = {
    "check_connectivity": (_check_connectivity, "بررسی اتصال و استخر", False),
    "refresh_statistics": (_refresh_statistics, "به‌روزرسانی آمار پرس‌وجو", False),
    "bloat_report":       (_bloat_report,       "گزارش ردیف‌های مرده", False),
}


def run_action(action: str, actor: str = "") -> dict:
    entry = ACTIONS.get(action)
    if entry is None:
        applog.security("admin.database.action.rejected",
                        "درخواست عملیات ناشناختهٔ پایگاه داده",
                        actor=actor, target=str(action)[:60], outcome="denied")
        raise ValueError("این عملیات تعریف‌شده نیست.")
    fn, label, _destructive = entry
    started = time.perf_counter()
    try:
        message = fn()
    except Exception as e:  # noqa: BLE001
        applog.audit("admin.database.action.failed", f"ناموفق: {label}",
                     actor=actor, target=action, outcome="failed",
                     level="warning", error_type=type(e).__name__)
        raise ValueError(f"اجرای «{label}» ناموفق بود.")
    duration = int((time.perf_counter() - started) * 1000)
    applog.audit("admin.database.action.completed", f"انجام شد: {label}",
                 actor=actor, target=action, outcome="ok", duration_ms=duration)
    return {"ok": True, "action": action, "label_fa": label,
            "message_fa": message, "duration_ms": duration}


def available_actions() -> list:
    return [{"name": n, "label_fa": l, "destructive": d}
            for n, (_f, l, d) in ACTIONS.items()]
