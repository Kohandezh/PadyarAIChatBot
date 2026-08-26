"""Exhibition lead capture: visitors, company contacts, invites, pending edits.

THE FLOW THIS SERVES
--------------------
A field visitor stands at a company's booth and asks for a contact. What the
system has to guarantee is not "a form was filled in" but "the person whose
number this is agreed to it, and only they can change the company's text":

    visitor opens their own link      -> a visitor session (12 h)
    visitor searches the company      -> a row of `dataset`, never free text,
                                         and never a company someone already owns
    visitor enters name/role/phone    -> a lead, status `unverified`, OTP sent
    contact reads the code out loud   -> OTP verified, status `verified`
    system mints a ONE-TIME invite    -> a QR on the visitor's screen, or an SMS
    contact opens it and reads        -> nothing burns; the link still works
    contact submits their text        -> THE INVITE BURNS, status `completed`,
                                         and a PENDING edit, not the live answer
    admin approves                    -> the text lands in `dataset`

WHY THE INVITE BURNS ON SUBMIT AND NOT ON OPEN
---------------------------------------------
A token that dies on GET also kills the POST that follows it, so the previous
design needed a second table to carry the same window forward. Burning on the
successful submit removes that table, that cookie and the contradiction behind
them: the token in the URL is the credential for the whole interaction, and it
dies at the moment the work is done. Until then it lives 24 hours, because a
contact at a busy booth may not scan anything until the evening.

THREE STATUSES, NOT SIX
-----------------------
`unverified`, `verified`, `completed` say where the CONTACT got to. The review
outcome is a different axis and lives on `dataset_edits.status`, so an operator
can look at one number and know how many companies are actually done.
`verified` is a normal resting state, not an error: plenty of contacts never
open the link, which is why an admin can release one (`release_lead`).

WHAT IS DELIBERATELY NOT AUTOMATIC
----------------------------------
An approved edit becomes an answer the chatbot gives to the public. Anyone
holding an invite could otherwise write anything into it, so an edit lands in
`dataset_edits` as `pending` and a human approves it. A repeated phone number
is not blocked and not silently flagged either: the visitor is warned, and
going ahead anyway is an explicit act that gets written down.

NUMBERS AT REST
---------------
The raw phone is stored, because the whole point of the exercise is to be able
to contact these companies afterwards. `phone_hash` (keyed HMAC, same key as
the OTP codes) sits beside it and is what duplicate detection compares, so
duplicate checks never need the plaintext and never have to normalise twice.
"""
import hmac
import hashlib
import io
import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.config import logger
from app.db.connection import get_db_connection
from app.db.timeutil import to_naive_utc
from app.services import otp as otp_service

# --- Lifetimes -----------------------------------------------------------
# 24 hours, and the invite dies earlier the moment the contact submits. The
# window is this wide because the contact is standing in a loud hall with a
# queue behind them; the thing that actually limits exposure is that the link
# is handed over face to face and stops working as soon as it is used.
INVITE_TTL_SECONDS = 24 * 3600
# A visitor works one exhibition day on one phone. Long enough that nobody is
# re-scanning their own badge between booths, short enough that a lost phone
# stops being an open door overnight.
VISITOR_SESSION_TTL_SECONDS = 12 * 3600

VISITOR_COOKIE = "padyar_visitor"

# Where the contact got to. Nothing else is a status: `approved`/`rejected` are
# a review outcome and live on `dataset_edits`.
STATUSES = ("unverified", "verified", "completed")

# One sentence for a used link, an expired link and a link that never existed.
# The status codes differ so tests and logs can tell them apart; the body never
# does, because a different page for "used" tells a stranger the token was real.
DEAD_INVITE_MESSAGE = "اینجا چیزی برای نمایش نیست."

MAX_EDIT_CHARS = 4000

# How the invite reaches the contact. `qr` needs no gateway, no permission and
# no delivery: the visitor shows their own screen. `sms` waits on Asanak
# approving a template that may carry a link.
INVITE_CHANNELS = ("qr", "sms")
DEFAULT_INVITE_CHANNEL = "qr"

# The approved booth script. Said out loud at the booth and shown again above
# the text box, because thirty seconds in a loud hall is not informed consent.
CONSENT_SCRIPT_DEFAULT = (
    "سلام، من از تیم چت‌بات نمایشگاه هستم. کاری که انجام می‌دهیم تأیید اطلاعات "
    "شرکت شما برای نمایش در چت‌بات نمایشگاه است. الان یک کد پیامکی برای شما "
    "می‌آید، لطفاً آن را برای من بخوانید. بعد از ثبت، خودتان باید بروید و "
    "ببینید اطلاعات شرکتتان درست وارد شده باشد. این اطلاعات شامل یک متن معرفی "
    "است که شرکت شما و محصولاتش را توصیف می‌کند."
)


class LeadError(Exception):
    """A refusal a visitor or a contact is allowed to read.

    `code` is what the UI branches on. A duplicate phone and a company that is
    already taken are both `409` and need two different screens, and matching
    on a Persian sentence is not a contract.
    """

    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.status = status
        self.code = code


def _now() -> datetime:
    return datetime.utcnow()


def _hmac_key() -> bytes:
    from app.auth.security import _get_hmac_key
    return _get_hmac_key().encode()


def _digest(value: str) -> str:
    """Keyed HMAC. Used for phone numbers and for invite tokens alike.

    Keyed, not plain SHA-256: a bare hash of an Iranian mobile number is
    reversible by enumerating the ~10^9 possible numbers, which is minutes of
    work. The key makes the stored value useless to anyone who only has the
    database.
    """
    return hmac.new(_hmac_key(), (value or "").encode(), hashlib.sha256).hexdigest()


def _live_owner(alias: str = "l") -> str:
    """The company-ownership rule, written once and used by four queries.

    A company has a live owner while one registration of it is `verified` or
    `completed` and has not been released. That row is why the company leaves
    every visitor's search, why a second registration is refused, and why an
    admin release puts the company straight back in the list.
    """
    return f"{alias}.status IN ('verified', 'completed') AND {alias}.released_at IS NULL"


# ── Schema ───────────────────────────────────────────────────────────────
# Same shape as the OTP module's ensure_table(): the module owns its tables, so
# an install without `leads` never grows them. The SQL is the dialect the db
# adapter translates for PostgreSQL (`?` placeholders, TEXT timestamps); the
# PostgreSQL-native version with real types lives in migrations/0005_leads.sql
# and migrations/0006_lead_status.sql.

_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS lead_visitors (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL DEFAULT '',
        code        TEXT NOT NULL,
        active      INTEGER DEFAULT 1,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_leads (
        id                     TEXT PRIMARY KEY,
        dataset_id             TEXT NOT NULL,
        company_name           TEXT NOT NULL DEFAULT '',
        visitor_id             TEXT NOT NULL DEFAULT '',
        first_name             TEXT NOT NULL DEFAULT '',
        last_name              TEXT NOT NULL DEFAULT '',
        position               TEXT NOT NULL DEFAULT '',
        phone                  TEXT NOT NULL DEFAULT '',
        phone_hash             TEXT NOT NULL DEFAULT '',
        status                 TEXT NOT NULL DEFAULT 'unverified',
        challenge_id           TEXT NOT NULL DEFAULT '',
        duplicate_override_of  TEXT,
        duplicate_override_at  TEXT,
        released_at            TEXT,
        consent_script_version TEXT NOT NULL DEFAULT 'v1',
        created_at             TEXT NOT NULL,
        verified_at            TEXT,
        ip                     TEXT NOT NULL DEFAULT '',
        user_agent             TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS edit_invites (
        token_hash        TEXT PRIMARY KEY,
        lead_id           TEXT NOT NULL,
        dataset_id        TEXT NOT NULL,
        issued_by_session TEXT NOT NULL DEFAULT '',
        expires_at        TEXT NOT NULL,
        used_at           TEXT,
        created_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dataset_edits (
        id           TEXT PRIMARY KEY,
        dataset_id   TEXT NOT NULL,
        lead_id      TEXT NOT NULL DEFAULT '',
        old_text     TEXT NOT NULL DEFAULT '',
        new_text     TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL,
        reviewed_at  TEXT,
        reviewed_by  TEXT NOT NULL DEFAULT ''
    )
    """,
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_leads_visitor ON company_leads(visitor_id)",
    "CREATE INDEX IF NOT EXISTS ix_leads_phone   ON company_leads(phone_hash)",
    "CREATE INDEX IF NOT EXISTS ix_leads_dataset ON company_leads(dataset_id)",
    "CREATE INDEX IF NOT EXISTS ix_leads_status  ON company_leads(status)",
    "CREATE INDEX IF NOT EXISTS ix_edits_status  ON dataset_edits(status)",
    "CREATE INDEX IF NOT EXISTS ix_visitor_code  ON lead_visitors(code)",
)

# The SQLite half of migrations/0006_lead_status.sql. PostgreSQL never runs
# this: there the migration file owns the schema, and a failed statement there
# would poison the surrounding transaction.
_SQLITE_UPGRADE = (
    "ALTER TABLE company_leads ADD COLUMN duplicate_override_of TEXT",
    "ALTER TABLE company_leads ADD COLUMN duplicate_override_at TEXT",
    "ALTER TABLE company_leads ADD COLUMN released_at TEXT",
    "ALTER TABLE company_leads ADD COLUMN consent_script_version TEXT NOT NULL DEFAULT 'v1'",
    "ALTER TABLE edit_invites ADD COLUMN issued_by_session TEXT NOT NULL DEFAULT ''",
    "UPDATE company_leads SET status = CASE status"
    "   WHEN 'submitted' THEN 'unverified'"
    "   WHEN 'link_opened' THEN 'verified'"
    "   WHEN 'edit_submitted' THEN 'completed'"
    "   WHEN 'approved' THEN 'completed'"
    "   WHEN 'duplicate' THEN 'unverified'"
    "   ELSE status END",
    "ALTER TABLE company_leads DROP COLUMN is_duplicate",
    "DROP TABLE IF EXISTS edit_sessions",
)


def ensure_tables() -> None:
    from app.config import DB_BACKEND
    conn = get_db_connection()
    try:
        for ddl in _TABLES:
            conn.execute(ddl)
        for ddl in _INDEXES:
            conn.execute(ddl)
        conn.commit()
        if DB_BACKEND != "postgres":
            _upgrade_sqlite(conn)
    finally:
        conn.close()


def _upgrade_sqlite(conn) -> None:
    """Bring an install created before the three-state vocabulary up to date.

    Probed with a SELECT rather than run every time, the same way
    app/db/connection.py detects an older `admins` table: on a current database
    this costs one query and writes nothing. Each statement is still guarded,
    because DROP COLUMN needs SQLite 3.35 and an older interpreter simply keeps
    an unused column, which harms nothing.
    """
    try:
        conn.execute("SELECT released_at FROM company_leads LIMIT 1")
        return
    except Exception:  # noqa: BLE001 (any dialect's "no such column")
        pass
    for statement in _SQLITE_UPGRADE:
        try:
            conn.execute(statement)
        except Exception as e:  # noqa: BLE001
            logger.info("[leads] sqlite upgrade skipped: %s (%s)", statement[:60], e)
    conn.commit()


def _audit(event: str, detail: str = "", **fields):
    logger.info("[leads] event=%s %s", event, detail)
    from app.services import applog
    warn = any(word in event for word in ("fail", "invalid", "expired", "refused",
                                          "unknown", "reused"))
    applog.record(
        "leads", f"leads.{event}",
        level="warning" if warn else "info",
        message=detail, metadata=fields,
    )


# ── Settings the operator owns ───────────────────────────────────────────

def invite_channel() -> str:
    from app.db.queries import get_setting
    value = (get_setting("leads_invite_channel", DEFAULT_INVITE_CHANNEL) or "").strip()
    return value if value in INVITE_CHANNELS else DEFAULT_INVITE_CHANNEL


def set_invite_channel(value: str) -> str:
    from app.db.queries import set_setting
    channel = (value or "").strip()
    if channel not in INVITE_CHANNELS:
        raise LeadError("کانال تحویل باید qr یا sms باشد.", code="bad_channel")
    set_setting("leads_invite_channel", channel)
    _audit("invite_channel_changed", channel)
    return channel


def sms_capability() -> dict:
    """Whether the invite can actually be texted, and what to do when it cannot.

    The admin picks the delivery channel once and finds out weeks later that
    nothing arrived, so the answer belongs on the settings screen rather than in
    a log. Nothing here calls the gateway: it reads the same settings the send
    path reads.

    `dev` counts as AVAILABLE, because its send path genuinely succeeds — the
    link lands in the gitignored dev outbox instead of a phone. That is what
    makes the whole invite-by-SMS flow testable before Asanak approves a link
    template, and the `reason` says where the message really goes so nobody
    mistakes it for delivery.
    """
    import os
    from app.db.queries import get_setting
    from app.services import sms as sms_service

    provider = (get_setting("sms_provider", "")
                or os.getenv("OTP_DELIVERY", "dev")).strip().lower()
    if provider == "dev":
        return {"available": True, "dev": True,
                "reason": "پیامک آزمایشی: لینک به جای گوشی در صندوق آزمایشی سرور "
                          "(data/otp-dev-outbox.log) می‌نشیند."}
    if not sms_service.asanak_configured():
        return {"available": False,
                "reason": "نام کاربری، رمز عبور و شماره فرستنده را در تنظیمات پیامک وارد کنید."}
    if not sms_service.setting("sms_asanak_invite_template_id").strip():
        return {"available": False,
                "reason": "شناسهٔ قالب پیامکِ لینک دعوت تنظیم نشده است. یک قالب حاوی "
                          "لینک را در پنل آسانک تأیید بگیرید و شناسه‌اش را وارد کنید."}
    return {"available": True, "reason": ""}


def consent_script() -> dict:
    from app.db.queries import get_setting
    return {
        "text": get_setting("leads_consent_script", CONSENT_SCRIPT_DEFAULT),
        "version": get_setting("leads_consent_script_version", "v1"),
    }


def set_consent_script(text: str) -> dict:
    """Save a new script and mint a new version for it.

    Registrations keep the version they were captured under, so rewording the
    script never rewrites what an earlier contact was told. An unchanged text
    keeps its version. A save that changed nothing did not tell anyone
    anything new.
    """
    from app.db.queries import set_setting
    body = (text or "").strip()
    if not body:
        raise LeadError("متن رضایت نمی‌تواند خالی باشد.", code="empty_consent")
    current = consent_script()
    if body == current["text"]:
        return current
    try:
        version = f"v{int(current['version'].lstrip('v')) + 1}"
    except ValueError:
        version = "v2"
    set_setting("leads_consent_script", body)
    set_setting("leads_consent_script_version", version)
    _audit("consent_script_changed", version)
    return {"text": body, "version": version}


# ── Visitors ─────────────────────────────────────────────────────────────

def create_visitor(name: str) -> dict:
    """Add a field visitor and mint the personal link they will scan."""
    ensure_tables()
    visitor_id = secrets.token_urlsafe(8)
    code = secrets.token_urlsafe(24)
    conn = get_db_connection()
    try:
        conn.execute(
            # TRUE, not 1: `active` is BOOLEAN and PostgreSQL will not take an
            # integer there. SQLite treats the two as the same thing.
            "INSERT INTO lead_visitors (id, name, code, active, created_at)"
            " VALUES (?, ?, ?, TRUE, ?)",
            (visitor_id, (name or "").strip()[:80], code, _now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    _audit("visitor_created", name, visitor_id=visitor_id)
    return {"id": visitor_id, "name": name, "code": code}


def list_visitors() -> list:
    ensure_tables()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT v.id, v.name, v.code, v.active, v.created_at,"
            " (SELECT COUNT(*) FROM company_leads l WHERE l.visitor_id = v.id) AS total,"
            " (SELECT COUNT(*) FROM company_leads l WHERE l.visitor_id = v.id"
            "    AND l.status IN ('verified', 'completed')) AS verified"
            " FROM lead_visitors v ORDER BY v.created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def set_visitor_active(visitor_id: str, active: bool) -> bool:
    """Revoke or restore a visitor's link. Revoking is instant: the session
    cookie carries only the id, and every request re-reads `active`."""
    ensure_tables()
    conn = get_db_connection()
    try:
        # `bool(...)`, not `1 if active else 0`. `active` is BOOLEAN, psycopg
        # sends a Python int as `integer`, and PostgreSQL has no cast for that
        # in an assignment. SQLite took it, which is how it got this far.
        cur = conn.execute("UPDATE lead_visitors SET active = ? WHERE id = ?",
                           (bool(active), visitor_id))
        conn.commit()
        changed = (cur.rowcount or 0) > 0
    finally:
        conn.close()
    _audit("visitor_active_changed", visitor_id, active=active)
    return changed


def delete_visitor(visitor_id: str, actor: str = "") -> bool:
    """Remove a field visitor from the roster. The personal link dies with
    the row: `visitor_by_code` and `visitor_by_id` read the row back, and no
    row means no session, on the very next tap.

    The leads this visitor captured stay exactly where they are. A lead is
    the record of a company this exhibition reached, owned by its own row
    and not by whoever was holding the phone; only the attribution falls
    back to "—" in the lists. A company one of their leads still owns keeps
    that owner until an admin releases it, so deleting a colleague can
    never quietly hand a verified company back to the search list.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        cur = conn.execute("DELETE FROM lead_visitors WHERE id = ?", (visitor_id,))
        conn.commit()
        changed = (cur.rowcount or 0) > 0
    finally:
        conn.close()
    if changed:
        from app.services import applog
        applog.audit("leads.visitor_deleted", "همکار غرفه از فهرست حذف شد",
                     actor=actor, target=visitor_id)
        _audit("visitor_deleted", visitor_id, visitor_id=visitor_id, actor=actor)
    return changed


def rotate_visitor_code(visitor_id: str) -> Optional[str]:
    """Mint a new personal link and kill the old one in the same statement.

    The only way to see a visitor's link twice. A lost phone is answered here
    rather than by re-displaying the code that is on the lost phone.
    """
    ensure_tables()
    code = secrets.token_urlsafe(24)
    conn = get_db_connection()
    try:
        cur = conn.execute("UPDATE lead_visitors SET code = ? WHERE id = ?",
                           (code, visitor_id))
        conn.commit()
        if (cur.rowcount or 0) == 0:
            return None
    finally:
        conn.close()
    _audit("visitor_code_rotated", visitor_id, visitor_id=visitor_id)
    return code


def visitor_by_code(code: str) -> Optional[dict]:
    ensure_tables()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, name, active FROM lead_visitors WHERE code = ?", (code,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["active"]:
        return None
    return dict(row)


def visitor_by_id(visitor_id: str) -> Optional[dict]:
    ensure_tables()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, name, active FROM lead_visitors WHERE id = ?", (visitor_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["active"]:
        return None
    return dict(row)


# ── Company search ───────────────────────────────────────────────────────

def search_companies(query: str, limit: int = 20) -> list:
    """Search the knowledge base by title, minus the companies already taken.

    The visitor picks from this list and can never type a company name: a typed
    name creates a company that does not exist, and no later cleanup finds it.
    A company with a live owner is gone from EVERY visitor's list, including the
    one who registered it, because two people cannot work the same booth.
    """
    ensure_tables()
    term = (query or "").strip()
    unowned = (" AND NOT EXISTS (SELECT 1 FROM company_leads l"
               f" WHERE l.dataset_id = dataset.id AND {_live_owner()})")
    conn = get_db_connection()
    try:
        if term:
            # % and _ are user input here — escape them so a visitor typing a
            # wildcard cannot broaden the match beyond what they typed.
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = conn.execute(
                "SELECT id, title FROM dataset WHERE title LIKE ? ESCAPE '\\'" + unowned
                + " ORDER BY title LIMIT ?", (f"%{escaped}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title FROM dataset WHERE 1 = 1" + unowned
                + " ORDER BY title LIMIT ?", (limit,)
            ).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "title": r["title"]} for r in rows]


def _dataset_row(dataset_id: str):
    conn = get_db_connection()
    try:
        return conn.execute(
            "SELECT id, title, text FROM dataset WHERE id = ?", (dataset_id,)
        ).fetchone()
    finally:
        conn.close()


# ── Registering a contact ────────────────────────────────────────────────

def register_contact(visitor_id: str, dataset_id: str, first_name: str,
                     last_name: str, position: str, phone: str,
                     override_duplicate: bool = False,
                     ip: str = "", user_agent: str = "") -> dict:
    """Create the lead and send the contact an OTP.

    Two refusals share the `409` and are told apart by `LeadError.code`. A
    company that already has a live owner is final. A phone number that already
    owns another company is a WARNING: one person really can run two booths, so
    the visitor is told, and coming back with `override_duplicate` sends the
    code and writes down who overrode what.
    """
    ensure_tables()
    company = _dataset_row(dataset_id)
    if company is None:
        raise LeadError("این شرکت در فهرست نیست.", status=404, code="unknown_company")

    destination = otp_service.normalize_destination(phone)
    if destination is None:
        raise LeadError("شماره واردشده معتبر نیست.", code="bad_phone")
    if not (first_name or "").strip():
        raise LeadError("نام مخاطب را وارد کنید.", code="missing_name")

    phone_hash = _digest(destination)
    lead_id = secrets.token_urlsafe(12)
    consent = consent_script()
    now = _now()

    # Both checks, the OTP and the insert on ONE connection: a duplicate found
    # by a connection that is then closed is a duplicate found in the past.
    conn = get_db_connection()
    try:
        owner = conn.execute(
            f"SELECT l.id FROM company_leads l WHERE l.dataset_id = ? AND {_live_owner()}"
            " LIMIT 1", (dataset_id,),
        ).fetchone()
        if owner is not None:
            raise LeadError("این شرکت قبلاً ثبت شده است.", status=409, code="company_taken")

        prior = conn.execute(
            "SELECT l.id, l.visitor_id FROM company_leads l WHERE l.phone_hash = ?"
            f" AND l.dataset_id <> ? AND {_live_owner()} ORDER BY l.created_at LIMIT 1",
            (phone_hash, dataset_id),
        ).fetchone()
        if prior is not None and not override_duplicate:
            # The other company is NOT named. Whoever is holding this phone is
            # not entitled to learn which booths this number already answered
            # for.
            raise LeadError(
                "این شماره قبلاً برای شرکت دیگری ثبت شده است. اگر مطمئنید ادامه بدهید.",
                status=409, code="duplicate_phone",
            )

        # The OTP goes FIRST. If delivery fails the visitor sees the gateway's
        # own refusal and no half-registered lead is left behind.
        challenge = otp_service.request_challenge(
            destination, first_name=first_name, last_name=last_name, position=position,
        )

        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " first_name, last_name, position, phone, phone_hash, status,"
            " challenge_id, duplicate_override_of, duplicate_override_at,"
            " consent_script_version, created_at, ip, user_agent)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unverified', ?, ?, ?, ?, ?, ?, ?)",
            (lead_id, dataset_id, company["title"], visitor_id,
             (first_name or "").strip()[:60], (last_name or "").strip()[:60],
             (position or "").strip()[:80], destination, phone_hash,
             challenge["challenge_id"],
             prior["id"] if prior is not None else None,
             now.isoformat() if prior is not None else None,
             consent["version"], now.isoformat(), (ip or "")[:60],
             (user_agent or "")[:200]),
        )
        conn.commit()
    finally:
        conn.close()

    _audit("contact_registered", company["title"], lead_id=lead_id, visitor_id=visitor_id)
    if prior is not None:
        # A control that can be waved away in silence is not a control.
        from app.services import applog
        applog.audit(
            "leads.duplicate_override",
            "شماره تکراری با تأیید ویزیتور ثبت شد",
            actor=visitor_id, target=lead_id, ip=ip, user_agent=user_agent,
            metadata={"prior_lead_id": prior["id"], "prior_visitor_id": prior["visitor_id"]},
        )
    return {
        "lead_id": lead_id,
        "company": company["title"],
        "destination_masked": challenge.get("destination_masked", ""),
        "expires_in": challenge.get("expires_in", 0),
        "consent_version": consent["version"],
    }


def admin_add_contact(dataset_id: str, first_name: str, last_name: str,
                      position: str, phone: str, base_url: str = "",
                      override_duplicate: bool = False,
                      actor: str = "", ip: str = "") -> dict:
    """Record a company contact straight from the admin panel, with an invite.

    The scenario: the contact was met OUTSIDE the booth flow — a phone call, a
    corridor conversation — and the operator wants exactly what the booth
    produces: the responsible person on file for this `dataset` row, and a
    one-time link they can hand over (WhatsApp, email, in person) so the
    company's text still goes through the same human review.

    The admin stands where the OTP stood: `verified` means the operator vouches
    for the number, and the row owns its company until an explicit release —
    the same rule every other live owner follows. Every refusal the booth can
    meet is met here too, with the same codes, so the UI can share its logic.
    """
    ensure_tables()
    company = _dataset_row(dataset_id)
    if company is None:
        raise LeadError("این شرکت در فهرست نیست.", status=404, code="unknown_company")

    destination = otp_service.normalize_destination(phone)
    if destination is None:
        raise LeadError("شماره واردشده معتبر نیست.", code="bad_phone")
    if not (first_name or "").strip():
        raise LeadError("نام مخاطب را وارد کنید.", code="missing_name")

    phone_hash = _digest(destination)
    lead_id = secrets.token_urlsafe(12)
    now = _now()

    conn = get_db_connection()
    try:
        owner = conn.execute(
            f"SELECT l.id FROM company_leads l WHERE l.dataset_id = ? AND {_live_owner()}"
            " LIMIT 1", (dataset_id,),
        ).fetchone()
        if owner is not None:
            raise LeadError("این شرکت قبلاً ثبت شده است.", status=409, code="company_taken")

        prior = conn.execute(
            "SELECT l.id FROM company_leads l WHERE l.phone_hash = ?"
            f" AND l.dataset_id <> ? AND {_live_owner()} ORDER BY l.created_at LIMIT 1",
            (phone_hash, dataset_id),
        ).fetchone()
        if prior is not None and not override_duplicate:
            raise LeadError(
                "این شماره قبلاً برای شرکت دیگری ثبت شده است. اگر مطمئنید ادامه بدهید.",
                status=409, code="duplicate_phone",
            )

        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " first_name, last_name, position, phone, phone_hash, status,"
            " duplicate_override_of, duplicate_override_at, consent_script_version,"
            " created_at, verified_at, ip, user_agent)"
            " VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 'verified', ?, ?, 'admin', ?, ?, ?, '')",
            (lead_id, dataset_id, company["title"],
             (first_name or "").strip()[:60], (last_name or "").strip()[:60],
             (position or "").strip()[:80], destination, phone_hash,
             prior["id"] if prior is not None else None,
             now.isoformat() if prior is not None else None,
             now.isoformat(), now.isoformat(), (ip or "")[:60]),
        )
        conn.commit()
    finally:
        conn.close()

    invite = create_invite(lead_id, dataset_id, base_url)
    from app.services import applog
    applog.audit("leads.admin_contact_added", "مسئول شرکت از پنل ادمین ثبت شد",
                 actor=actor, target=lead_id, ip=ip,
                 metadata={"dataset_id": dataset_id})
    _audit("admin_contact_added", company["title"], lead_id=lead_id, actor=actor)
    return {"lead_id": lead_id, "company": company["title"],
            "link": invite["invite_url"], "qr": qr_svg(invite["invite_url"]),
            "expires_at": invite["expires_at"]}


def _lead(conn, lead_id: str):
    return conn.execute("SELECT * FROM company_leads WHERE id = ?", (lead_id,)).fetchone()


def verify_contact(lead_id: str, code: str, base_url: str,
                   visitor_session: str = "") -> dict:
    """Check the code the contact read out, claim the company, deliver the invite.

    The raw invite token never appears in what this returns. The visitor who
    captured the lead gets a QR to hold up, or a report that the SMS went out;
    either way they cannot open the contact's link themselves.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        lead = _lead(conn, lead_id)
    finally:
        conn.close()
    if lead is None:
        raise LeadError("این ثبت پیدا نشد.", status=404, code="unknown_lead")
    if lead["status"] != "unverified":
        raise LeadError("این شماره قبلاً تأیید شده است.", code="already_verified")

    ok, message = otp_service.verify(lead["challenge_id"], code)
    if not ok:
        _audit("verify_failed", message, lead_id=lead_id)
        raise LeadError(message, code="bad_code")

    now = _now()
    conn = get_db_connection()
    try:
        # The company is claimed by the UPDATE itself. Two booths verifying the
        # same company at the same second both pass their own SELECT; only one
        # of them can pass this WHERE.
        cur = conn.execute(
            "UPDATE company_leads SET status = 'verified', verified_at = ?"
            " WHERE id = ? AND status = 'unverified'"
            " AND NOT EXISTS (SELECT 1 FROM company_leads o WHERE o.dataset_id = ?"
            f"   AND o.id <> ? AND {_live_owner('o')})",
            (now.isoformat(), lead_id, lead["dataset_id"], lead_id),
        )
        claimed = (cur.rowcount or 0) == 1
        conn.commit()
    finally:
        conn.close()
    if not claimed:
        _audit("claim_refused", lead["company_name"], lead_id=lead_id)
        raise LeadError("این شرکت قبلاً ثبت شده است.", status=409, code="company_taken")

    invite = create_invite(lead_id, lead["dataset_id"], base_url,
                           issued_by_session=visitor_session)
    _audit("contact_verified", lead["company_name"], lead_id=lead_id)

    channel = invite_channel()
    result = {"lead_id": lead_id, "company": lead["company_name"],
              "channel": channel, "expires_at": invite["expires_at"],
              "destination_masked": otp_service.mask_destination(lead["phone"] or "")}
    if channel == "sms":
        sent, reason = _send_link(lead["phone"], invite["invite_url"], "invite", lead_id)
        if sent:
            return result
        # REQ-057: never silent, and never dead either. The gateway's own
        # reason goes to the log and the admin panel, and the booth gets the QR
        # so the work in front of the visitor can finish. A `qr` alongside
        # `channel: "sms"` IS the failure report.
        _audit("invite_sms_failed", reason, lead_id=lead_id)
    return {**result, "qr": qr_svg(invite["invite_url"])}


def _send_link(destination: str, url: str, kind: str, reference: str = ""):
    """Hand a link to the SMS module. Returns (sent, operator reason).

    Every caller here can carry on without the SMS: the invite falls back to a
    QR and a rejection notice that never left is shown to the admin instead. So
    a failure is reported, never raised, including the failure where the
    gateway is not configured at all.
    """
    from app.services import sms as sms_service
    try:
        if kind == "invite":
            sms_service.send_invite_link(destination, url, reference)
        else:
            sms_service.send_reject_notice(destination, url, reference)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, (getattr(e, "detail", "") or str(e))


# ── The one-time invite ──────────────────────────────────────────────────

def create_invite(lead_id: str, dataset_id: str, base_url: str,
                  issued_by_session: str = "") -> dict:
    """Mint a single-use, 24-hour invite.

    The raw token is returned to this module's own callers and never stored:
    the table keeps only its keyed HMAC, so a database read cannot forge an
    invite. `issued_by_session` records the visitor session that minted it,
    which is the session refused when the edit is submitted.
    """
    ensure_tables()
    token = secrets.token_urlsafe(32)
    # Hashed BEFORE the connection opens. _digest reads the HMAC key, which on
    # a fresh install writes it, and a second writer inside an open write
    # transaction is a self-inflicted "database is locked".
    token_hash = _digest(token)
    now = _now()
    expires = now + timedelta(seconds=INVITE_TTL_SECONDS)
    conn = get_db_connection()
    try:
        # A lead has ONE live invite. Re-issuing kills the previous one, so a
        # rejected edit does not leave two working links behind.
        conn.execute("DELETE FROM edit_invites WHERE lead_id = ? AND used_at IS NULL",
                     (lead_id,))
        conn.execute(
            "INSERT INTO edit_invites (token_hash, lead_id, dataset_id,"
            " issued_by_session, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token_hash, lead_id, dataset_id, (issued_by_session or "")[:120],
             expires.isoformat(), now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    _audit("invite_created", "", lead_id=lead_id)
    return {"invite_url": f"{base_url.rstrip('/')}/edit/{token}",
            "expires_at": expires.isoformat(), "expires_in": INVITE_TTL_SECONDS}


def invite_view(token: str, ip: str = "") -> dict:
    """What the invite opens, WITHOUT burning it.

    Opening the link, reading it, closing it and coming back an hour later is
    normal behaviour, not an attack. Only a successful submit ends the invite.
    """
    ensure_tables()
    token_hash = _digest(token or "")
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM edit_invites WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            _audit("invite_unknown", "", ip=ip)
            raise LeadError(DEAD_INVITE_MESSAGE, status=404, code="dead_invite")
        if row["used_at"]:
            _audit("invite_reused", "", lead_id=row["lead_id"], ip=ip)
            raise LeadError(DEAD_INVITE_MESSAGE, status=410, code="dead_invite")
        if to_naive_utc(row["expires_at"]) < _now():
            _audit("invite_expired", "", lead_id=row["lead_id"], ip=ip)
            raise LeadError(DEAD_INVITE_MESSAGE, status=410, code="dead_invite")
        company = conn.execute(
            "SELECT id, title, text FROM dataset WHERE id = ?", (row["dataset_id"],)
        ).fetchone()
    finally:
        conn.close()
    if company is None:
        # The company row is gone. Same sentence as a dead link: there is
        # genuinely nothing here to show.
        _audit("invite_company_missing", "", lead_id=row["lead_id"], ip=ip)
        raise LeadError(DEAD_INVITE_MESSAGE, status=410, code="dead_invite")

    pending = pending_edit_for(row["dataset_id"])
    return {
        "lead_id": row["lead_id"], "dataset_id": company["id"],
        "issued_by_session": row["issued_by_session"],
        "company": company["title"],
        # What they see in the box: their own unreviewed text if there is one,
        # otherwise the live answer. Coming back must not silently discard a
        # rewrite that was already sent.
        "text": pending["new_text"] if pending else company["text"],
        "live_text": company["text"],
        "pending": bool(pending),
        "expires_at": row["expires_at"],
    }


def pending_edit_for(dataset_id: str) -> Optional[dict]:
    """The one unreviewed edit of a company.

    Keyed on the COMPANY, not on the registration: two registrations of the
    same company must not put two competing drafts in front of the reviewer.
    """
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM dataset_edits WHERE dataset_id = ? AND status = 'pending'"
            " ORDER BY created_at DESC LIMIT 1", (dataset_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ── The edit itself ──────────────────────────────────────────────────────

def submit_edit(token: str, new_text: str, visitor_session: str = "",
                ip: str = "") -> dict:
    """Queue the contact's rewrite for review and burn the invite.

    The live answer is untouched. Everything that can refuse this edit refuses
    it BEFORE the burn, so a rejected submit leaves the link working.
    """
    view = invite_view(token, ip=ip)
    text = (new_text or "").strip()
    if not text:
        raise LeadError("متن پاسخ نمی‌تواند خالی باشد.", code="empty_text")
    if len(text) > MAX_EDIT_CHARS:
        raise LeadError(f"متن پاسخ نباید از {MAX_EDIT_CHARS} نویسه بیشتر باشد.",
                        code="text_too_long")
    if visitor_session and visitor_session == view["issued_by_session"]:
        _audit("edit_refused_own_session", view["company"], lead_id=view["lead_id"], ip=ip)
        raise LeadError(DEAD_INVITE_MESSAGE, status=403, code="dead_invite")

    now = _now()
    token_hash = _digest(token or "")
    conn = get_db_connection()
    try:
        # The burn is the gate. The condition lives in the UPDATE, so a hundred
        # simultaneous submits produce exactly one row with `used_at` set and
        # exactly one edit; the other ninety-nine read a rowcount of 0 here and
        # never reach the INSERT.
        cur = conn.execute(
            "UPDATE edit_invites SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now.isoformat(), token_hash),
        )
        if (cur.rowcount or 0) != 1:
            _audit("invite_reused", "", lead_id=view["lead_id"], ip=ip)
            raise LeadError(DEAD_INVITE_MESSAGE, status=410, code="dead_invite")

        # One live pending edit per company. Sending again replaces the draft
        # instead of queueing a second one for the reviewer to reconcile.
        conn.execute(
            "UPDATE dataset_edits SET status = 'superseded', reviewed_at = ?"
            " WHERE dataset_id = ? AND status = 'pending'",
            (now.isoformat(), view["dataset_id"]),
        )
        conn.execute(
            "INSERT INTO dataset_edits (id, dataset_id, lead_id, old_text, new_text,"
            " status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (secrets.token_urlsafe(12), view["dataset_id"], view["lead_id"],
             view["live_text"], text, now.isoformat()),
        )
        conn.execute(
            "UPDATE company_leads SET status = 'completed' WHERE id = ? AND status = 'verified'",
            (view["lead_id"],),
        )
        conn.commit()
    finally:
        conn.close()
    _audit("edit_submitted", view["company"], lead_id=view["lead_id"], ip=ip)
    return {"ok": True, "company": view["company"]}


def list_edits(status: str = "pending", limit: int = 200) -> list:
    """The review queue, or the approved list the revert action works from.

    Columns are named rather than starred: this row goes to a browser, and
    `SELECT *` on a joined table hands over whatever column the next migration
    adds.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT e.id, e.dataset_id, e.lead_id, e.old_text, e.new_text, e.status,"
            " e.created_at, e.reviewed_at, e.reviewed_by, l.company_name,"
            " l.first_name, l.last_name, l.position, l.phone"
            " FROM dataset_edits e LEFT JOIN company_leads l ON l.id = e.lead_id"
            " WHERE e.status = ? ORDER BY e.created_at DESC LIMIT ?", (status, limit)
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["phone"] = otp_service.mask_destination(d.get("phone") or "")
        out.append(d)
    return out


def review_edit(edit_id: str, approve: bool, reviewer: str = "",
                base_url: str = "") -> dict:
    """Approve (write into `dataset`, reindex) or reject a pending edit.

    Approval sends nothing: the text appears on the chatbot, which is the
    notification. Rejection has to be told, and is told with a fresh 24-hour
    invite so the contact can act on it.
    """
    ensure_tables()
    now = _now()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT e.*, l.phone FROM dataset_edits e"
            " LEFT JOIN company_leads l ON l.id = e.lead_id WHERE e.id = ?", (edit_id,)
        ).fetchone()
        if row is None:
            raise LeadError("این ویرایش پیدا نشد.", status=404, code="unknown_edit")
        if row["status"] != "pending":
            raise LeadError("این ویرایش قبلاً بررسی شده است.", code="already_reviewed")
        if approve:
            conn.execute("UPDATE dataset SET text = ? WHERE id = ?",
                         (row["new_text"], row["dataset_id"]))
        conn.execute(
            "UPDATE dataset_edits SET status = ?, reviewed_at = ?, reviewed_by = ?"
            " WHERE id = ?",
            ("approved" if approve else "rejected", now.isoformat(),
             (reviewer or "")[:60], edit_id),
        )
        conn.commit()
    finally:
        conn.close()

    if approve:
        # The knowledge base changed, so the retrieval index is now stale.
        from app.routers.dataset import _trigger_reindex
        _trigger_reindex()
    _audit("edit_reviewed", edit_id, approved=approve, reviewer=reviewer)

    result = {"ok": True, "status": "approved" if approve else "rejected"}
    if approve or not row["lead_id"]:
        return result
    invite = create_invite(row["lead_id"], row["dataset_id"], base_url)
    sent, reason = _send_link(row["phone"] or "", invite["invite_url"], "reject")
    if not sent:
        # The contact does not know their text was refused. That is the admin's
        # problem to see, not something to swallow.
        _audit("reject_notice_failed", reason, lead_id=row["lead_id"])
    return {**result, "notified": sent, "notify_error": reason}


def revert_edit(edit_id: str, actor: str = "") -> dict:
    """Put back the text an approval replaced.

    `old_text` is the live answer as it was at the moment of submission, which
    is the whole reason it is kept. One click, no retyping, and the edit leaves
    the approved list so it cannot be reverted twice onto a newer text.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, dataset_id, old_text, status FROM dataset_edits WHERE id = ?",
            (edit_id,),
        ).fetchone()
        if row is None:
            raise LeadError("این ویرایش پیدا نشد.", status=404, code="unknown_edit")
        if row["status"] != "approved":
            raise LeadError("فقط یک ویرایش تأییدشده قابل برگرداندن است.",
                            code="not_revertable")
        conn.execute("UPDATE dataset SET text = ? WHERE id = ?",
                     (row["old_text"], row["dataset_id"]))
        conn.execute("UPDATE dataset_edits SET status = 'reverted' WHERE id = ?", (edit_id,))
        conn.commit()
    finally:
        conn.close()
    from app.routers.dataset import _trigger_reindex
    _trigger_reindex()
    from app.services import applog
    applog.audit("leads.edit_reverted", "متن تأییدشده به حالت قبل برگشت",
                 actor=actor, target=edit_id)
    _audit("edit_reverted", edit_id, actor=actor)
    return {"ok": True, "status": "reverted"}


# ── Reporting and the stuck list ─────────────────────────────────────────

def funnel() -> dict:
    """One number per status, plus the two counts that are NOT stages.

    Overrides and releases are exceptions an operator should watch, not steps a
    lead walks through, so they sit beside the funnel instead of inside it.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM company_leads GROUP BY status"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM company_leads").fetchone()["n"]
        overrides = conn.execute(
            "SELECT COUNT(*) AS n FROM company_leads WHERE duplicate_override_of IS NOT NULL"
        ).fetchone()["n"]
        released = conn.execute(
            "SELECT COUNT(*) AS n FROM company_leads WHERE released_at IS NOT NULL"
        ).fetchone()["n"]
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM dataset_edits WHERE status = 'pending'"
        ).fetchone()["n"]
    finally:
        conn.close()
    by_status = {r["status"]: r["n"] for r in rows}
    return {
        "total": total,
        "unverified": by_status.get("unverified", 0),
        "verified": by_status.get("verified", 0),
        "completed": by_status.get("completed", 0),
        "pending_review": pending,
        "overrides": overrides,
        "released": released,
    }


# Named columns, never `SELECT *`. These rows go to a browser, and starring the
# table would serve `phone_hash`, `challenge_id` and every column a later
# migration adds. The prior company is joined in by NAME, because "registered
# against another company" is unusable to an operator without saying which.
_LEAD_COLUMNS = (
    "l.id, l.dataset_id, l.company_name, l.visitor_id, l.first_name, l.last_name,"
    " l.position, l.phone, l.status, l.duplicate_override_of, l.duplicate_override_at,"
    " l.released_at, l.consent_script_version, l.created_at, l.verified_at,"
    " p.company_name AS duplicate_override_company, v.name AS visitor_name"
)
_LEAD_JOINS = (
    " FROM company_leads l"
    " LEFT JOIN company_leads p ON p.id = l.duplicate_override_of"
    " LEFT JOIN lead_visitors v ON v.id = l.visitor_id"
)


def _lead_rows(rows) -> list:
    out = []
    for r in rows:
        d = dict(r)
        d["phone"] = otp_service.mask_destination(d.get("phone") or "")
        out.append(d)
    return out


def list_leads(visitor_id: str = "", limit: int = 200) -> list:
    ensure_tables()
    conn = get_db_connection()
    try:
        if visitor_id:
            rows = conn.execute(
                f"SELECT {_LEAD_COLUMNS}{_LEAD_JOINS} WHERE l.visitor_id = ?"
                " ORDER BY l.created_at DESC LIMIT ?", (visitor_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_LEAD_COLUMNS}{_LEAD_JOINS}"
                " ORDER BY l.created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    finally:
        conn.close()
    return _lead_rows(rows)


def stuck_leads() -> list:
    """Companies that verified and then went quiet.

    Every registration sitting at `verified` is here, with no age threshold, so
    this list and the `verified` number on the funnel are always the same
    number. Two counts that disagree read as a bug. `waiting_hours` is computed
    here because the operator's clock is not the database's.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"SELECT {_LEAD_COLUMNS}{_LEAD_JOINS} WHERE l.status = 'verified'"
            " AND l.released_at IS NULL ORDER BY l.verified_at"
        ).fetchall()
    finally:
        conn.close()
    now = _now()
    out = _lead_rows(rows)
    for d in out:
        since = d.get("verified_at") or d.get("created_at")
        d["waiting_hours"] = round((now - to_naive_utc(since)).total_seconds() / 3600, 1)
    return out


def delete_company(dataset_id: str, actor: str = "") -> dict:
    """Remove a company from the leads feature entirely.

    The scenario: a company was added to the dataset by mistake, or pulled out
    of the exhibition. Every lead row, every live invite and every pending
    draft for it goes — the company disappears from the booth search and the
    contact form, and any link still sitting in a phone dies on its next tap.
    The `dataset` row itself is NOT touched: whether the company exists in the
    chatbot's knowledge base is the dataset page's business, not the leads
    page's.

    Deleting the leads (not the dataset row) is the reversible direction: an
    operator who deleted the wrong company re-adds a contact and everything is
    back, with no way to have lost the chatbot's answer.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        leads = conn.execute(
            "SELECT id FROM company_leads WHERE dataset_id = ?", (dataset_id,)
        ).fetchall()
        lead_ids = [r["id"] for r in leads]
        # The edit queue too: an unreviewed draft for a deleted company must
        # not sit in the reviewer's list as a ghost.
        conn.execute("DELETE FROM dataset_edits WHERE dataset_id = ?", (dataset_id,))
        for lead_id in lead_ids:
            conn.execute("DELETE FROM edit_invites WHERE lead_id = ?", (lead_id,))
        conn.execute("DELETE FROM company_leads WHERE dataset_id = ?", (dataset_id,))
        conn.commit()
    finally:
        conn.close()
    from app.services import applog
    applog.audit("leads.company_deleted", "شرکت از جذب سرنخ حذف شد",
                 actor=actor, target=dataset_id,
                 metadata={"leads_removed": len(lead_ids)})
    _audit("company_deleted", dataset_id, actor=actor, leads=len(lead_ids))
    return {"ok": True, "leads_removed": len(lead_ids)}


def reissue_invite(dataset_id: str, base_url: str = "", actor: str = "") -> dict:
    """Mint a fresh edit link for a company that already owns one.

    The scenario: the contact lost the link, or it expired before they opened
    it, and re-verifying their phone from the booth is pointless theatre — the
    operator already knows who they are. The newest live owner is re-used, its
    previous invite dies, and a new one is returned with its QR, shown once.

    Companies nobody owns are refused: that is the contact form's flow, and
    mixing the two would let a link be minted for a company whose contact was
    never verified by anyone.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        owner = conn.execute(
            f"SELECT l.id, l.company_name FROM company_leads l"
            f" WHERE l.dataset_id = ? AND {_live_owner()}"
            " ORDER BY l.verified_at DESC LIMIT 1", (dataset_id,),
        ).fetchone()
    finally:
        conn.close()
    if owner is None:
        raise LeadError("برای این شرکت مسئولی ثبت نشده است. اول «افزودن مسئول شرکت».",
                        status=404, code="no_owner")
    invite = create_invite(owner["id"], dataset_id, base_url)
    from app.services import applog
    applog.audit("leads.invite_reissued", "لینک ویرایش تازه برای شرکت صادر شد",
                 actor=actor, target=dataset_id)
    _audit("invite_reissued", dataset_id, lead_id=owner["id"], actor=actor)
    return {"lead_id": owner["id"], "company": owner["company_name"],
            "link": invite["invite_url"], "qr": qr_svg(invite["invite_url"]),
            "expires_at": invite["expires_at"]}


def release_lead(lead_id: str, actor: str = "", ip: str = "") -> dict:
    """Give a stuck company back to the visitors.

    The registration keeps its `verified` status for the history; `released_at`
    is what stops it owning the company. Its live invite dies with it, so the
    old link cannot land an edit on a company someone else is now registering.
    """
    ensure_tables()
    now = _now()
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "UPDATE company_leads SET released_at = ? WHERE id = ? AND status = 'verified'"
            " AND released_at IS NULL", (now.isoformat(), lead_id),
        )
        released = (cur.rowcount or 0) == 1
        if released:
            conn.execute("DELETE FROM edit_invites WHERE lead_id = ? AND used_at IS NULL",
                         (lead_id,))
        conn.commit()
    finally:
        conn.close()
    if not released:
        raise LeadError("این ثبت قابل آزادسازی نیست.", status=404, code="not_releasable")
    from app.services import applog
    applog.audit("leads.released", "ثبت آزاد شد و شرکت به فهرست برگشت",
                 actor=actor, target=lead_id, ip=ip)
    _audit("lead_released", lead_id, actor=actor)
    return {"ok": True}


# ── QR ───────────────────────────────────────────────────────────────────

def qr_svg(url: str) -> str:
    """The invite as an inline SVG.

    SVG, not PNG: it needs no imaging library, scales to whatever phone camera
    is pointed at it, and the visitor's own screen is the only display it has
    to survive.
    """
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
