"""Circuit breaker with shared state in the control-plane database.

CLOSED → OPEN → HALF_OPEN → (CLOSED | OPEN), state in `ai_circuit_state` so
every worker/process agrees — a breaker that lives in one Python process
would let each worker trip its own breaker and hammer a down provider N times.

Policy (tunable via settings, defaults conservative):
  * Trips after `ai_circuit_threshold` consecutive FAILOVER-ELIGIBLE failures
    inside `ai_circuit_window_s` (default 5 in 120 s).
  * AUTH failures trip IMMEDIATELY — a broken key must not be retried per
    request; it marks the provider and lets routing fail over.
  * Open means: skip the provider entirely until `cooldown_until`
    (default 60 s; auth failures get a longer 600 s cooldown).
  * Half-open is entered by exactly ONE worker via an atomic conditional
    UPDATE (a probe lease with owner + expiry). The loser workers see the
    active lease and treat the circuit as still open.
  * Probe success → CLOSED everywhere; probe failure → OPEN again.
  * Non-failover-eligible errors (invalid_request, content_rejected,
    context_limit) are OUR fault and never count against the provider.

All transitions are single UPDATE ... WHERE statements — concurrency-safe
without long transactions, and never holding one across a provider call.
"""
import secrets

from . import errors as ai_errors

DEFAULT_THRESHOLD = 5
DEFAULT_WINDOW_S = 120
DEFAULT_COOLDOWN_S = 60
AUTH_COOLDOWN_S = 600
PROBE_LEASE_S = 45


def _setting_int(key: str, default: int) -> int:
    from app.db.queries import get_setting
    try:
        return int(get_setting(key, "") or default)
    except (TypeError, ValueError):
        return default


def _now_iso(offset_s: float = 0.0) -> str:
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone.utc) + timedelta(seconds=offset_s)
    return t.isoformat(timespec="seconds")


def _as_dt(value):
    """Normalize a timestamp column value to a comparable datetime.

    SQLite gives back the exact ISO STRING we wrote; PostgreSQL gives back a
    native datetime for TIMESTAMPTZ. Comparing the two forms directly raises
    TypeError — every read goes through here first. Returns None for NULL.
    """
    from datetime import datetime
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_utc())
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _utc():
    from datetime import timezone
    return timezone.utc


def _state_row(conn, instance_id: str):
    return conn.execute(
        "SELECT * FROM ai_circuit_state WHERE provider_instance_id = ?",
        (instance_id,)).fetchone()


def _ensure_row(conn, instance_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO ai_circuit_state (provider_instance_id) VALUES (?)",
        (instance_id,))


def allows(instance_id: str) -> tuple:
    """(allowed?, reason). Closed → yes. Open → only after cooldown, and
    then by WINNING the single half-open probe lease. Half-open with an
    active lease held by someone else → no."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = _state_row(conn, instance_id)
        if row is None:
            return True, ""
        state = row["state"]
        if state == "closed":
            return True, ""
        if state == "open":
            cooldown = _as_dt(row["cooldown_until"])
            if cooldown and cooldown > _as_dt(_now_iso()):
                return False, f"open until {row['cooldown_until']}"
            # cooldown elapsed → try to become THE half-open probe
            token = secrets.token_hex(8)
            cur = conn.execute(
                "UPDATE ai_circuit_state SET state='half_open', probe_owner=?,"
                " probe_expires_at=?, updated_at=?"
                " WHERE provider_instance_id=? AND state='open'"
                " AND (cooldown_until IS NULL OR cooldown_until <= ?)",
                (token, _now_iso(PROBE_LEASE_S), _now_iso(), instance_id, _now_iso()))
            conn.commit()
            if cur.rowcount:
                return True, "half-open probe"
            return False, "another worker holds the probe"
        if state == "half_open":
            # A stale lease (probe worker died) may be reclaimed.
            lease = _as_dt(row["probe_expires_at"])
            if lease and lease > _as_dt(_now_iso()):
                return False, "half-open probe in flight"
            token = secrets.token_hex(8)
            cur = conn.execute(
                "UPDATE ai_circuit_state SET probe_owner=?, probe_expires_at=?,"
                " updated_at=? WHERE provider_instance_id=? AND state='half_open'"
                " AND (probe_expires_at IS NULL OR probe_expires_at <= ?)",
                (token, _now_iso(PROBE_LEASE_S), _now_iso(), instance_id, _now_iso()))
            conn.commit()
            if cur.rowcount:
                return True, "half-open probe (reclaimed)"
            return False, "half-open probe in flight"
        return True, ""
    finally:
        conn.close()


def record_success(instance_id: str) -> None:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        _ensure_row(conn, instance_id)
        conn.execute(
            "UPDATE ai_circuit_state SET state='closed', failure_count=0,"
            " window_started_at=NULL, opened_at=NULL, cooldown_until=NULL,"
            " probe_owner='', probe_expires_at=NULL, last_success_at=?,"
            " updated_at=? WHERE provider_instance_id=?",
            (_now_iso(), _now_iso(), instance_id))
        conn.commit()
    finally:
        conn.close()


def record_failure(instance_id: str, error: "ai_errors.AIError") -> str:
    """Record a provider failure. Returns the resulting state.

    CONCURRENCY: the failure counter is incremented BY THE DATABASE inside a
    single UPDATE (a CASE expression decides window-reset vs. increment) — it
    is never read into Python and written back. The old read-modify-write lost
    counts whenever two workers failed at the same instant, and with real
    parallelism the breaker simply never reached its threshold: measured on
    PostgreSQL, five concurrent failover-eligible failures left
    failure_count=2 and the circuit CLOSED, so every worker kept hammering a
    provider that was already down. That first UPDATE also takes the row's
    write lock, so the SELECT after it — and the state transition decided from
    it — are serialized against every other worker until this connection
    commits.
    """
    from app.db.connection import get_db_connection
    from app.services import applog
    conn = get_db_connection()
    try:
        _ensure_row(conn, instance_id)
        conn.commit()

        # Our own fault (invalid request / content rejected / context limit):
        # not provider health. Recorded, but never trips the breaker.
        counts = error.failover_eligible or error.retryable

        if not counts:
            conn.execute(
                "UPDATE ai_circuit_state SET last_failure_at=?, last_failure_code=?,"
                " updated_at=? WHERE provider_instance_id=?",
                (_now_iso(), error.code, _now_iso(), instance_id))
            row = _state_row(conn, instance_id)
            conn.commit()
            return row["state"] if row else "closed"

        # Auth failure: immediate trip with a long cooldown.
        if error.code == ai_errors.AUTHENTICATION_FAILED:
            conn.execute(
                "UPDATE ai_circuit_state SET state='open', failure_count=?,"
                " window_started_at=?, last_failure_at=?, last_failure_code=?,"
                " opened_at=?, cooldown_until=?, probe_owner='',"
                " probe_expires_at=NULL, updated_at=? WHERE provider_instance_id=?",
                (_setting_int("ai_circuit_threshold", DEFAULT_THRESHOLD),
                 _now_iso(), _now_iso(), error.code, _now_iso(),
                 _now_iso(_setting_int("ai_circuit_auth_cooldown_s", AUTH_COOLDOWN_S)),
                 _now_iso(), instance_id))
            conn.commit()
            applog.security("llm.circuit.opened",
                            "مدار سرویس‌دهنده به دلیل خطای اعتبارنامه باز شد",
                            level="warning", target=instance_id,
                            metadata={"error": error.code})
            return "open"

        threshold = _setting_int("ai_circuit_threshold", DEFAULT_THRESHOLD)
        window_s = _setting_int("ai_circuit_window_s", DEFAULT_WINDOW_S)
        now, cutoff = _now_iso(), _now_iso(-window_s)

        # ATOMIC windowed increment. The CASE resets the counter when the
        # existing window has aged out, so failures spread wider than
        # `window_s` can never accumulate into a trip. `_now_iso` is now the
        # ONLY writer of this column, so the comparison is a real TIMESTAMPTZ
        # comparison on PostgreSQL and a correct lexicographic one on SQLite's
        # TEXT column. (The previous code bound a datetime OBJECT here, which
        # SQLite stored space-separated while the reset path stored it
        # T-separated — two formats in one column. A row left in the old form
        # sorts below any 'T' form, so it simply starts a fresh window once and
        # self-heals on the first write.) This statement takes the row's write
        # lock, held until commit.
        conn.execute(
            "UPDATE ai_circuit_state SET"
            " failure_count = CASE WHEN window_started_at IS NULL"
            "   OR window_started_at <= ? THEN 1 ELSE failure_count + 1 END,"
            " window_started_at = CASE WHEN window_started_at IS NULL"
            "   OR window_started_at <= ? THEN ? ELSE window_started_at END,"
            " last_failure_at=?, last_failure_code=?, updated_at=?"
            " WHERE provider_instance_id=?",
            (cutoff, cutoff, now, now, error.code, now, instance_id))

        # Same transaction, and we hold the row lock: this is the authoritative
        # post-increment view, not a racy re-read.
        row = _state_row(conn, instance_id)
        state = row["state"] if row else "closed"
        count = (row["failure_count"] or 0) if row else 0

        if state == "half_open":
            # The probe FAILED — back to open with a fresh cooldown.
            conn.execute(
                "UPDATE ai_circuit_state SET state='open', failure_count=?,"
                " last_failure_at=?, last_failure_code=?, opened_at=?,"
                " cooldown_until=?, probe_owner='', probe_expires_at=NULL,"
                " updated_at=? WHERE provider_instance_id=?",
                (threshold, _now_iso(), error.code, _now_iso(),
                 _now_iso(_setting_int("ai_circuit_cooldown_s", DEFAULT_COOLDOWN_S)),
                 _now_iso(), instance_id))
            conn.commit()
            return "open"

        if state != "open" and count >= threshold:
            conn.execute(
                "UPDATE ai_circuit_state SET state='open', last_failure_at=?,"
                " last_failure_code=?, opened_at=?, cooldown_until=?,"
                " probe_owner='', probe_expires_at=NULL, updated_at=?"
                " WHERE provider_instance_id=? AND state <> 'open'",
                (_now_iso(), error.code, _now_iso(),
                 _now_iso(_setting_int("ai_circuit_cooldown_s", DEFAULT_COOLDOWN_S)),
                 _now_iso(), instance_id))
            conn.commit()
            applog.warning("llm", "llm.circuit.opened",
                           "مدار سرویس‌دهنده پس از خطاهای پیاپی باز شد",
                           target=instance_id, error_code=error.code,
                           metadata={"failures": count, "window_s": window_s})
            return "open"
        conn.commit()
        return state
    finally:
        conn.close()


def reset(instance_id: str, actor: str = "") -> None:
    """Admin action: force CLOSED. Audited by the caller (admin router)."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        _ensure_row(conn, instance_id)
        conn.execute(
            "UPDATE ai_circuit_state SET state='closed', failure_count=0,"
            " window_started_at=NULL, opened_at=NULL, cooldown_until=NULL,"
            " probe_owner='', probe_expires_at=NULL, updated_at=?"
            " WHERE provider_instance_id=?", (_now_iso(), instance_id))
        conn.commit()
    finally:
        conn.close()


def snapshot(instance_id: str = "") -> list:
    """Circuit rows for the admin UI / health derivation."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        if instance_id:
            rows = conn.execute(
                "SELECT * FROM ai_circuit_state WHERE provider_instance_id = ?",
                (instance_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM ai_circuit_state").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
