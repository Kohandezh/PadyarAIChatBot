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
                # Emitted by the worker that WON the conditional UPDATE, and
                # only by that one. `rowcount` is the race winner, so racing
                # workers cannot each log a transition that happened once.
                _emit_transition(instance_id, "open", "half_open",
                                 "cooldown elapsed; probe lease acquired",
                                 probe_lease_s=PROBE_LEASE_S)
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
                # Not a state change — still half_open — but an operationally
                # meaningful one: the previous probe holder died and its lease
                # expired. Silence here looks identical to a wedged circuit.
                _emit_transition(instance_id, "half_open", "half_open",
                                 "stale probe lease reclaimed",
                                 probe_lease_s=PROBE_LEASE_S)
                return True, "half-open probe (reclaimed)"
            return False, "half-open probe in flight"
        return True, ""
    finally:
        conn.close()


# `open` keeps the event name it has always had. Renaming it to match the
# state string would silently break any existing alert or log query built on
# `llm.circuit.opened`.
_EVENT_NAMES = {"open": "llm.circuit.opened",
                "half_open": "llm.circuit.half_open",
                "closed": "llm.circuit.closed"}


def _safe_log(fn, *args, **kwargs) -> None:
    """Best-effort telemetry: a logging failure must never break routing.

    `applog` is documented never to raise, but that is a property of one
    module, not a guarantee this one should depend on. A full disk or a locked
    log database must degrade to "no row", never to a circuit that stops
    recovering because logging the recovery failed.
    """
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — observability must never break routing
        pass


def _emit_transition(instance_id: str, previous: str, new: str, reason: str,
                     **extra) -> None:
    """Log a circuit state change. Only ever called on a PROVEN transition.

    Recovery used to be invisible: `open` was logged, `half_open` and `closed`
    were not. An operator could watch a provider go down and never see it come
    back — the absence of a further event looked the same as a circuit stuck
    open forever.

    Carries no secret: an instance id, two state names and a reason. The
    provider's own error text is deliberately not repeated here; it is already
    recorded, redacted, on the failure event.
    """
    from app.services import applog
    _safe_log(applog.info, "llm", _EVENT_NAMES.get(new, f"llm.circuit.{new}"),
              f"مدار سرویس‌دهنده: {previous} → {new}",
              target=instance_id,
              metadata={"provider_instance_id": instance_id,
                        "previous_state": previous, "new_state": new,
                        "reason": reason, **extra})


def record_success(instance_id: str) -> None:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        _ensure_row(conn, instance_id)
        # The prior state NAMES the transition; `rowcount` PROVES it. Reading
        # the state and then emitting on `previous != closed` would be a stale
        # pre-read: two workers whose probes both succeed while the circuit is
        # half_open would each read `half_open` and each log a recovery that
        # happened once. The conditional UPDATE can only succeed for one of
        # them, so gating on its rowcount makes the event exactly-once.
        row = conn.execute(
            "SELECT state FROM ai_circuit_state WHERE provider_instance_id=?",
            (instance_id,)).fetchone()
        previous = (row["state"] if row else "closed") or "closed"
        cur = conn.execute(
            "UPDATE ai_circuit_state SET state='closed', failure_count=0,"
            " window_started_at=NULL, opened_at=NULL, cooldown_until=NULL,"
            " probe_owner='', probe_expires_at=NULL, last_success_at=?,"
            " updated_at=? WHERE provider_instance_id=? AND state <> 'closed'",
            (_now_iso(), _now_iso(), instance_id))
        recovered = bool(cur.rowcount)
        if not recovered:
            # Already closed. The success still has to clear the failure
            # window — an ordinary successful request, no transition to log.
            conn.execute(
                "UPDATE ai_circuit_state SET failure_count=0,"
                " window_started_at=NULL, last_success_at=?, updated_at=?"
                " WHERE provider_instance_id=?",
                (_now_iso(), _now_iso(), instance_id))
        conn.commit()
    finally:
        conn.close()
    # AFTER the commit: an event an operator can read before the state it
    # describes is durable is worse than a slightly late one.
    if recovered:
        _emit_transition(instance_id, previous, "closed",
                         "probe succeeded; provider recovered")


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
            _safe_log(applog.security, "llm.circuit.opened",
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
            # The probe FAILING is half of the recovery story. Without this an
            # operator sees `half_open` and then nothing, which is
            # indistinguishable from a probe still in flight.
            _emit_transition(instance_id, "half_open", "open",
                             "probe failed; cooldown restarted",
                             error_code=error.code)
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
            _safe_log(applog.warning, "llm", "llm.circuit.opened",
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
