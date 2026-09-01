"""The SMS outbox: every gateway send, kept with the handle that can prove
delivery.

A 200 from Asanak means the message was QUEUED. "Queued" is not "arrived": a
handset can be off, a number can be mistyped, and the freetext line can hold
a message forever (Status 20, measured 2026-08-17). `asanak_status(msgid)`
is the only way back to the truth and Asanak has no webhooks — it is pull or
nothing. This module is the pull:

    record()          - one row per send, msgid beside it (never raises into
                        the send path: the outbox is telemetry, not a
                        dependency)
    poll_deliveries() - queued rows younger than the window get their status
                        asked; the answer lands on the row where the admin
                        panel and the campaign report already read

THE STATUS VOCABULARY (kept honest, see migrations/0023)
    queued    accepted, no final word yet
    delivered the gateway said success (Asanak code 6)
    unknown   no final word within 24h, or no msgid to ask (dev outbox)
    failed    an explicit failure word — none is known on Asanak today, the
              column is the place one would land

Nothing here sends anything. The send paths in app/services/sms.py call
record() on their way out; the poller only reads the gateway.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.config import logger
from app.db.connection import get_db_connection
from app.db.timeutil import to_naive_utc

# How long a queued row keeps being asked. Iranian carriers answer within
# minutes; anything still wordless after a day is a message that did not
# happen, and asking forever is how a polling loop buys itself a rate limit.
POLL_WINDOW_HOURS = 24

# Asanak's msgstatus code for a delivered message ("Success", measured
# 2026-08-17 on the live gateway; code 20 was a freetext message that never
# arrived). One code is the whole certainty we have; everything else stays
# queued with the raw code recorded for the operator.
DELIVERED_CODES = (6,)

_TABLE = """
CREATE TABLE IF NOT EXISTS sms_messages (
    id                TEXT PRIMARY KEY,
    provider          TEXT NOT NULL DEFAULT '',
    kind              TEXT NOT NULL DEFAULT '',
    msgid             TEXT NOT NULL DEFAULT '',
    destination       TEXT NOT NULL DEFAULT '',
    reference         TEXT NOT NULL DEFAULT '',
    campaign_id       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'queued',
    status_detail     TEXT NOT NULL DEFAULT '',
    status_checked_at TEXT,
    created_at        TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_sms_messages_status ON sms_messages(status)",
    "CREATE INDEX IF NOT EXISTS ix_sms_messages_campaign ON sms_messages(campaign_id)",
    "CREATE INDEX IF NOT EXISTS ix_sms_messages_created ON sms_messages(created_at)",
)


def ensure_table() -> None:
    conn = get_db_connection()
    try:
        conn.execute(_TABLE)
        for ddl in _INDEXES:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.utcnow()


def record(provider: str, kind: str, destination: str, msgid: str = "",
           reference: str = "", campaign_id: str = "") -> Optional[str]:
    """One row per send. Never raises: a broken outbox must not break a send.

    `destination` arrives RAW from the send path and is stored MASKED — the
    panel reads this table, and the raw number already lives on the row that
    needs it (the lead, the challenge). A send with no msgid (the dev outbox,
    or a gateway that kept no id) is recorded as `unknown` rather than
    `queued`: there is nothing to poll and the row should not sit in the
    "waiting on the gateway" list forever.
    """
    from app.services import applog
    msgid = str(msgid or "").strip()
    try:
        ensure_table()
        row_id = secrets.token_urlsafe(12)
        status = "queued" if msgid else "unknown"
        detail = "" if msgid else "no gateway message id (dev outbox or id-less send)"
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO sms_messages (id, provider, kind, msgid, destination,"
                " reference, campaign_id, status, status_detail, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row_id, (provider or "")[:20], (kind or "")[:20], msgid,
                 applog.mask_phone(destination or ""), (reference or "")[:120],
                 (campaign_id or "")[:40], status, detail, _now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        return row_id
    except Exception as e:  # noqa: BLE001 — telemetry must never break a send
        logger.warning("[sms-outbox] record failed: %s", e)
        return None


def _status_code(answer) -> Optional[int]:
    """The numeric status code out of a msgstatus answer, whatever shape the
    gateway picked that day ({"data": {"status": 6}}, {"status": 6}, 6, …)."""
    if isinstance(answer, int):
        return answer
    if not isinstance(answer, dict):
        return None
    data = answer.get("data", answer)
    for source in (data if isinstance(data, dict) else {}, answer):
        for key in ("status", "Status", "status_code", "code"):
            value = source.get(key) if isinstance(source, dict) else None
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def poll_deliveries(limit: int = 50) -> dict:
    """Ask the gateway about queued messages and write the answer down.

    Rows older than the poll window are closed as `unknown` without asking:
    a message with no word after a day is a message that did not happen, and
    the operator needs that answer more than another day of "queued". Returns
    a small summary for the log and the admin button that triggered it.
    """
    ensure_table()
    now = _now()
    cutoff = (now - timedelta(hours=POLL_WINDOW_HOURS)).isoformat()

    conn = get_db_connection()
    try:
        stale = conn.execute(
            "SELECT id FROM sms_messages WHERE status = 'queued' AND created_at < ?"
            " LIMIT ?", (cutoff, limit),
        ).fetchall()
        for row in stale:
            conn.execute(
                "UPDATE sms_messages SET status = 'unknown', status_detail = ?,"
                " status_checked_at = ? WHERE id = ?",
                ("no final status within "
                 f"{POLL_WINDOW_HOURS}h", now.isoformat(), row["id"]),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT id, provider, msgid FROM sms_messages"
            " WHERE status = 'queued' AND msgid <> '' AND created_at >= ?"
            " ORDER BY created_at LIMIT ?", (cutoff, limit),
        ).fetchall()
    finally:
        conn.close()

    asked = delivered = failed = 0
    for row in rows:
        answer = _ask_gateway(row["provider"], row["msgid"])
        if answer is None:
            continue  # the gateway could not be asked; it stays queued
        asked += 1
        code = answer
        if code in DELIVERED_CODES:
            status, detail = "delivered", f"gateway status {code}"
            delivered += 1
        else:
            # Not a failure word — just not the success word. Stay queued,
            # with the code recorded so the operator sees movement.
            status, detail = "queued", f"gateway status {code}"
            if code is None:
                detail = "unparseable status answer"
        conn = get_db_connection()
        try:
            conn.execute(
                "UPDATE sms_messages SET status = ?, status_detail = ?,"
                " status_checked_at = ? WHERE id = ?",
                (status, detail, now.isoformat(), row["id"]),
            )
            conn.commit()
        finally:
            conn.close()
    summary = {"asked": asked, "delivered": delivered, "failed": failed,
               "closed_unknown": len(stale), "candidates": len(rows)}
    logger.info("[sms-outbox] poll: %s", summary)
    return summary


def _ask_gateway(provider: str, msgid: str) -> Optional[int]:
    """One msgstatus call, or None when the gateway could not be asked.

    Only Asanak exists today (see sms.PROVIDERS); the provider column is
    checked anyway so a second gateway's rows wait for their own asker
    instead of being asked with the wrong credentials.
    """
    if (provider or "").lower() != "asanak":
        return None
    try:
        from app.services import sms as sms_service
        code = _status_code(sms_service.asanak_status(msgid))
        return code
    except Exception as e:  # noqa: BLE001 — a poll must survive its gateway
        logger.warning("[sms-outbox] msgstatus failed for %s: %s", msgid, e)
        return None


def list_messages(campaign_id: str = "", kind: str = "", limit: int = 100) -> list:
    """The admin panel's view: newest first, filterable to one campaign."""
    ensure_table()
    limit = max(1, min(int(limit or 100), 500))
    where, args = [], []
    if (campaign_id or "").strip():
        where.append("campaign_id = ?")
        args.append(campaign_id.strip())
    if (kind or "").strip():
        where.append("kind = ?")
        args.append(kind.strip())
    sql = ("SELECT id, provider, kind, msgid, destination, reference, campaign_id,"
           " status, status_detail, status_checked_at, created_at FROM sms_messages"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY created_at DESC LIMIT ?")
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(sql, (*args, limit)).fetchall()]
    finally:
        conn.close()


def status_counts(campaign_id: str = "") -> dict:
    """delivered / queued / unknown / failed for one campaign (or all)."""
    ensure_table()
    conn = get_db_connection()
    try:
        if (campaign_id or "").strip():
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM sms_messages WHERE campaign_id = ?"
                " GROUP BY status", (campaign_id.strip(),)).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM sms_messages GROUP BY status"
            ).fetchall()
    finally:
        conn.close()
    counts = {r["status"]: r["n"] for r in rows}
    return {"total": sum(counts.values()),
            "delivered": counts.get("delivered", 0),
            "queued": counts.get("queued", 0),
            "unknown": counts.get("unknown", 0),
            "failed": counts.get("failed", 0)}
