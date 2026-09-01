"""The organizer's bulk-confirm campaign.

THE SCENARIO
------------
Two weeks before the exhibition the operator wants every company's data
checked by the company itself. From the Companies page they press send; each
company with a mobile on file gets ONE SMS — «please review and confirm your
information for the exhibition» — carrying a one-time link that opens that
company's own edit form (the same /edit/{token} page a booth invite opens).

WHAT A LAUNCH DOES, PER COMPANY
-------------------------------
    has a pending draft        -> skipped (their edit is already in review;
                                  a second link would race the reviewer)
    has a live owner           -> its lead, a fresh invite (the old one dies)
    owns itself only on paper  -> a lead is created, origin='campaign',
                                  owner = the number the company itself filed
    then                       -> one paced SMS, recorded on sms_messages with
                                  campaign_id, so delivery is polled like any
                                  other message and the report is one query

WHY IT IS SLOW ON PURPOSE
-------------------------
One SMS per second. The gateway is not a bulk blaster, the daily budget is a
shared cap, and an exhibition's worth of messages arriving in one burst is
how a sender line gets flagged. The run lives in a background task and writes
its verdicts as it goes, so the panel shows progress, not a spinner.

WHY IT STOPS INSTEAD OF RETRYING
--------------------------------
Budget exhausted or the line refused the link (Asanak 1014): the campaign is
marked `stopped` with the reason, the messages already sent stand, and the
operator launches a new one when the reason is fixed. Silent retries are how
a budget gets spent twice on the same sentence.
"""
import secrets
import time
from datetime import datetime

from app.config import logger
from app.db.connection import get_db_connection

# One message per second (see the header). A test with a real audience sets
# this to 0; production never does.
PACE_SECONDS = 1.0

CAMPAIGN_STATUSES = ("running", "done", "stopped")

_TABLE = """
CREATE TABLE IF NOT EXISTS sms_campaigns (
    id            TEXT PRIMARY KEY,
    text_template TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'running',
    audience      INTEGER NOT NULL DEFAULT 0,
    sent          INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    stop_reason   TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    finished_at   TEXT
)
"""

_INDEX = ("CREATE INDEX IF NOT EXISTS ix_sms_campaigns_created"
          " ON sms_campaigns(created_at)")


def ensure_table() -> None:
    conn = get_db_connection()
    try:
        conn.execute(_TABLE)
        conn.execute(_INDEX)
        conn.commit()
    finally:
        conn.close()


class CampaignError(Exception):
    """A refusal the operator reads. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── The audience ─────────────────────────────────────────────────────────

def audience() -> list:
    """Every company that filed a mobile number, newest data first.

    The mobile is the company's own field (companies.contact_mobile — folded
    from a verified booth lead or typed by the organizer), which is exactly
    the consent line a campaign may text on.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, contact_mobile, contact_name FROM companies"
            " WHERE TRIM(contact_mobile) <> '' ORDER BY title"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── The text ─────────────────────────────────────────────────────────────

def campaign_text() -> str:
    from app.services import sms as sms_service
    return sms_service.setting("sms_campaign_text").strip()


def validate_text(text: str) -> str:
    body = (text or "").strip()
    if not body or "{magic_link}" not in body:
        raise CampaignError(
            "متن پیامک کمپین باید عبارت {magic_link} را دقیقاً یک بار داشته باشد — "
            "همان‌جا لینک یک‌بارمصرف هر شرکت جایگزین می‌شود.")
    if len(body) > 1000:
        raise CampaignError("متن پیامک بیش از حد بلند است.")
    return body


# ── Running ──────────────────────────────────────────────────────────────

def _campaign_item(campaign_id: str, dataset_id: str, title: str,
                   status: str, detail: str = "") -> None:
    """One verdict row on sms_messages — the campaign's per-company report.

    'skipped' and 'send_failed' are campaign-only statuses the outbox poller
    never touches (it only reads 'queued' rows); they exist so the report is
    one query over one table.
    """
    try:
        row_id = secrets.token_urlsafe(12)
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO sms_messages (id, provider, kind, msgid, destination,"
                " reference, campaign_id, status, status_detail, created_at)"
                " VALUES (?, 'campaign', 'campaign', '', '', ?, ?, ?, ?, ?)",
                (row_id, dataset_id, campaign_id, status,
                 f"{title}: {detail}" if detail else title,
                 datetime.utcnow().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a report row must not stop the run
        logger.warning("[campaigns] verdict row failed: %s", e)


def _ensure_campaign_lead(company: dict) -> str:
    """The lead this company's invite hangs from — its live owner, or a new
    campaign-sourced one.

    A company nobody registered still gets its link: the number it filed IS
    the consent line, and `origin='campaign'` keeps the funnel honest about
    where this owner came from. Duplicate-phone rules do not apply here — the
    organizer is texting the company's own filed number, not asking a booth
    visitor to vouch for it.
    """
    from app.services.leads import _digest, _live_owner
    conn = get_db_connection()
    try:
        owner = conn.execute(
            f"SELECT l.id FROM company_leads l WHERE l.dataset_id = ?"
            f" AND {_live_owner()} ORDER BY l.verified_at DESC LIMIT 1",
            (company["id"],),
        ).fetchone()
        if owner is not None:
            return owner["id"]
        lead_id = secrets.token_urlsafe(12)
        now = datetime.utcnow().isoformat()
        mobile = company["contact_mobile"].strip()
        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " first_name, last_name, position, phone, phone_hash, status,"
            " consent_script_version, origin, created_at, verified_at, ip, user_agent)"
            " VALUES (?, ?, ?, '', ?, '', '', ?, ?, 'verified', 'v1', 'campaign',"
            " ?, ?, '', 'campaign')",
            (lead_id, company["id"], company["title"] or "",
             (company.get("contact_name") or "")[:60], mobile, _digest(mobile),
             now, now),
        )
        conn.commit()
        return lead_id
    finally:
        conn.close()


def run(campaign_id: str, base_url: str, pace_seconds: float = PACE_SECONDS) -> None:
    """Send the campaign, paced, verdict by verdict. Runs in a background
    thread (the router hands it to asyncio.to_thread); never raises — every
    outcome is written on the campaign row or a verdict row.
    """
    from app.services import sms as sms_service
    from app.services.leads import (LeadError, create_invite, ensure_tables,
                                    pending_edit_for)
    ensure_tables()
    companies = audience()
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE sms_campaigns SET audience = ?, status = 'running' WHERE id = ?",
            (len(companies), campaign_id),
        )
        conn.commit()
    finally:
        conn.close()

    sent = skipped = failed = 0
    stop_reason = ""
    for i, company in enumerate(companies):
        if i and pace_seconds:
            time.sleep(pace_seconds)
        if pending_edit_for(company["id"]) is not None:
            skipped += 1
            _campaign_item(campaign_id, company["id"], company["title"],
                           "skipped", "پیش‌نویس در انتظار بررسی است")
            continue
        try:
            lead_id = _ensure_campaign_lead(company)
            invite = create_invite(lead_id, company["id"], base_url)
            sms_service.send_campaign_link(company["contact_mobile"],
                                           invite["invite_url"],
                                           campaign_id=campaign_id,
                                           reference=company["id"])
            sent += 1
        except LeadError as e:
            failed += 1
            _campaign_item(campaign_id, company["id"], company["title"],
                           "send_failed", str(e))
        except sms_service.SmsError as e:
            if getattr(e, "code", None) == sms_service.BUDGET_EXHAUSTED \
                    or sms_service.is_link_refusal(e):
                stop_reason = e.detail
                # The audience not yet texted is not "skipped" — it was never
                # asked. The counters say what happened; the status says why
                # it stopped early.
                break
            failed += 1
            _campaign_item(campaign_id, company["id"], company["title"],
                           "send_failed", e.detail)
        except Exception as e:  # noqa: BLE001 — one bad company stops nothing
            failed += 1
            _campaign_item(campaign_id, company["id"], company["title"],
                           "send_failed", f"{type(e).__name__}: {e}")
        _bump(campaign_id, sent=sent, skipped=skipped, failed=failed)

    _finish(campaign_id, sent, skipped, failed, stop_reason)


def _bump(campaign_id: str, **counts) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE sms_campaigns SET sent = ?, skipped = ?, failed = ? WHERE id = ?",
            (counts.get("sent", 0), counts.get("skipped", 0),
             counts.get("failed", 0), campaign_id),
        )
        conn.commit()
    finally:
        conn.close()


def _finish(campaign_id: str, sent: int, skipped: int, failed: int,
            stop_reason: str) -> None:
    status = "stopped" if stop_reason else "done"
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE sms_campaigns SET status = ?, sent = ?, skipped = ?, failed = ?,"
            " stop_reason = ?, finished_at = ? WHERE id = ?",
            (status, sent, skipped, failed, stop_reason,
             datetime.utcnow().isoformat(), campaign_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("[campaigns] %s finished: status=%s sent=%s skipped=%s failed=%s",
                campaign_id, status, sent, skipped, failed)


def launch(text_template: str, base_url: str, actor: str = "") -> dict:
    """Create the campaign row. The caller runs `run()` in the background."""
    ensure_table()
    validate_text(text_template)
    from app.services import sms_outbox
    sms_outbox.ensure_table()
    campaign_id = secrets.token_urlsafe(8)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO sms_campaigns (id, text_template, status, created_by,"
            " created_at) VALUES (?, ?, 'running', ?, ?)",
            (campaign_id, text_template, (actor or "")[:60],
             datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    # The text is part of the campaign row AND the effective setting, so the
    # send path (which reads the setting) and the audit row agree.
    from app.db.queries import set_setting
    set_setting("sms_campaign_text", text_template)
    return {"id": campaign_id, "status": "running"}


def list_campaigns(limit: int = 20) -> list:
    ensure_table()
    limit = max(1, min(int(limit or 20), 100))
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, text_template, status, audience, sent, skipped, failed,"
            " stop_reason, created_by, created_at, finished_at FROM sms_campaigns"
            " ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def campaign_detail(campaign_id: str) -> dict:
    """One campaign and its per-company verdicts, delivery status included."""
    ensure_table()
    from app.services import sms_outbox
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, text_template, status, audience, sent, skipped, failed,"
            " stop_reason, created_by, created_at, finished_at FROM sms_campaigns"
            " WHERE id = ?", (campaign_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise CampaignError("این کمپین پیدا نشد.", status=404)
    return {"campaign": dict(row),
            "messages": sms_outbox.list_messages(campaign_id=campaign_id),
            "counts": sms_outbox.status_counts(campaign_id=campaign_id)}


def capability() -> dict:
    """Whether a campaign can run RIGHT NOW, and what to do when not.

    Mirrors leads.sms_capability(): the operator picks "send" on a screen
    that already says whether anything will go out — finding out from a
    stopped campaign an hour later is finding out too late. `dev` counts as
    available (the sends land in the dev outbox, which is what makes this
    whole flow testable).
    """
    import os
    from app.db.queries import get_setting
    from app.services import sms as sms_service

    text = campaign_text()
    provider = (get_setting("sms_provider", "")
                or os.getenv("OTP_DELIVERY", "dev")).strip().lower()
    if provider == "dev":
        return {"available": True, "dev": True, "text": text,
                "audience": len(audience()),
                "reason": "پیامک آزمایشی: پیام‌ها به جای گوشی در صندوق آزمایشی "
                          "سرور می‌نشینند."}
    if not sms_service.asanak_configured():
        return {"available": False, "text": text, "audience": len(audience()),
                "reason": "نام کاربری، رمز عبور و شماره فرستنده را در تنظیمات "
                          "پیامک وارد کنید."}
    if not text or "{magic_link}" not in text:
        return {"available": False, "text": text, "audience": len(audience()),
                "reason": "متن پیامک کمپین تنظیم نشده یا فاقد {magic_link} است."}
    return {"available": True, "text": text, "audience": len(audience()),
            "reason": ""}
