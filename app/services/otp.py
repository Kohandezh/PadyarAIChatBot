"""OTP verification service — server-side, single-use, abuse-resistant.

Security contract (see docs/engineering/SECURITY_MODEL.md):
- Codes are generated with the ``secrets`` CSPRNG.
- The raw code is NEVER stored and NEVER logged: the database keeps only a
  keyed HMAC-SHA256 of ``challenge_id:code`` under the app's secret key
  (an unkeyed hash of a 6-digit code is brute-forceable in milliseconds).
- Verification is constant-time (``hmac.compare_digest``).
- Expiry, attempt limits, resend cooldown/limits and per-destination rate
  limits are all enforced HERE, server-side — the UI timer is presentation.
- A challenge is bound to an unguessable ``challenge_id``; the client never
  proves anything by sending a phone number alone.
- Success consumes the challenge (single-use); resend invalidates the
  previous code by replacing the HMAC and expiry atomically.

Delivery is behind a provider seam. Without a configured SMS/email provider
the "dev" provider appends to a local, gitignored outbox file so a developer
can complete the flow — production installs must configure a real provider
(OTP_DELIVERY=dev is refused when COOKIE_SECURE=true, the project's
production marker). The code is never returned by any API response.
"""
import hmac
import os
import re
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple

from app.config import BASE_DIR, logger
from app.db.connection import get_db_connection
from app.db.timeutil import to_naive_utc

# --- Configuration (env-overridable) ---
OTP_LENGTH = int(os.getenv("OTP_LENGTH", "6"))
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "120"))
OTP_RESEND_COOLDOWN = int(os.getenv("OTP_RESEND_COOLDOWN", "45"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
OTP_MAX_RESENDS = int(os.getenv("OTP_MAX_RESENDS", "3"))
# Max new challenges per destination per hour (anti SMS-pumping).
OTP_DEST_HOURLY_LIMIT = int(os.getenv("OTP_DEST_HOURLY_LIMIT", "5"))
OTP_DELIVERY = os.getenv("OTP_DELIVERY", "dev")

_DEV_OUTBOX = os.path.join(BASE_DIR, "data", "otp-dev-outbox.log")

# Persian/Arabic-Indic digits → ASCII, mirrored by the frontend normalizer.
_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(value: str) -> str:
    """Map Persian and Arabic-Indic digits onto ASCII digits."""
    return (value or "").translate(_DIGIT_MAP)


def normalize_destination(raw: str) -> Optional[str]:
    """Canonicalize a phone destination; None when it can't be one.

    Accepts Persian/ASCII digits with optional +, spaces and dashes; the
    canonical form is digits with an optional leading +. 10–15 digits per
    E.164. This validates SHAPE only — real deliverability is the SMS
    provider's concern.
    """
    cleaned = normalize_digits(raw or "").strip()
    cleaned = re.sub(r"[ \-()]", "", cleaned)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    body = cleaned[1:] if cleaned.startswith("+") else cleaned
    if not body.isdigit() or not (10 <= len(body) <= 15):
        return None
    return cleaned


def mask_destination(destination: str) -> str:
    """+98 912 *** 4821-style masking — never show the full number back."""
    digits = destination.lstrip("+")
    keep_tail = 4
    if len(digits) <= keep_tail:
        return "*" * len(digits)
    prefix = digits[:2] if destination.startswith("+") else digits[:1]
    masked_len = len(digits) - len(prefix) - keep_tail
    return ("+" if destination.startswith("+") else "") + prefix + "*" * max(masked_len, 2) + digits[-keep_tail:]


def _hmac_key() -> bytes:
    from app.auth.security import _get_hmac_key
    return _get_hmac_key().encode()


def _code_hmac(challenge_id: str, code: str) -> str:
    return hmac.new(_hmac_key(), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.utcnow()


def _generate_code() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


def _audit(event: str, challenge_id: str = "", destination: str = "", detail: str = ""):
    """Structured audit line — masked destination, never the code.

    Writes to BOTH sinks: stdout (unchanged, so nothing that parsed these lines
    breaks) and the durable log store. Routing it here rather than at each call
    site means every existing and future _audit() call is captured for free.
    """
    logger.info(
        "[otp] event=%s challenge=%s destination=%s %s",
        event, challenge_id[:8] + "…" if challenge_id else "-",
        mask_destination(destination) if destination else "-",
        detail,
    )
    from app.services import applog
    failed = "fail" in event or "invalid" in event or "expired" in event
    exceeded = "limit" in event or "exceed" in event
    applog.record(
        "otp", f"otp.{event}",
        level="warning" if (failed or exceeded) else "info",
        message=detail or event,
        outcome="failed" if failed else ("denied" if exceeded else "ok"),
        actor_type="visitor",
        target=mask_destination(destination) if destination else "",
        metadata={"challenge": challenge_id[:8] if challenge_id else "",
                  "detail": detail})


class OtpError(Exception):
    """Public-safe error: `public` is generic by design (no enumeration)."""

    def __init__(self, public: str, status: int = 400):
        self.public = public
        self.status = status
        super().__init__(public)


def _message_for(code: str) -> str:
    """The SMS body.

    The last line is the WebOTP contract: `@<host> #<code>`. Chrome on Android
    only offers to autofill a code when the message ends with exactly that,
    so the format is functional, not decorative — changing it silently turns
    autofill off.
    """
    from app.db.queries import get_setting
    brand = (get_setting("otp_brand_name", "") or "PadYar").strip()
    host = (get_setting("otp_sms_host", "") or os.getenv("OTP_SMS_HOST", "")).strip()
    body = f"{brand}\nکد تأیید شما: {code}\nاین کد را در اختیار کسی قرار ندهید."
    if host:
        body += f"\n@{host} #{code}"
    return body


def _deliver(destination: str, code: str) -> None:
    """Provider seam: dev outbox, or a configured SMS gateway.

    The gateway credentials are read server-side inside app.services.sms and
    never leave it — no endpoint returns them and nothing here logs them.
    """
    from app.db.queries import get_setting
    provider = (get_setting("sms_provider", "") or OTP_DELIVERY or "dev").strip().lower()

    if provider != "dev":
        from app.services import sms
        if not sms.is_configured(provider):
            _audit("delivery_failed", destination=destination,
                   detail=f"provider={provider} not configured")
            raise OtpError("سرویس ارسال کد پیکربندی نشده است.", status=503)
        try:
            # The code travels twice on purpose: as the free-text body, and on
            # its own for a gateway that can only send an approved template.
            sms.send(provider, destination, _message_for(code), code=code)
        except sms.SmsError as e:
            _audit("delivery_failed", destination=destination, detail=f"provider={provider}")
            raise OtpError(str(e), status=503)
        _audit("delivery_requested", destination=destination, detail=f"provider={provider}")
        return

    from app.config import COOKIE_SECURE
    if COOKIE_SECURE:
        # Production marker set but no real provider configured — refuse
        # loudly instead of silently writing codes to disk.
        _audit("delivery_failed", destination=destination, detail="dev provider refused in production")
        raise OtpError("سرویس ارسال کد در دسترس نیست.", status=503)
    os.makedirs(os.path.dirname(_DEV_OUTBOX), exist_ok=True)
    with open(_DEV_OUTBOX, "a", encoding="utf-8") as f:
        f.write(f"{_now().isoformat()} {mask_destination(destination)} code={code}\n")
    _audit("delivery_requested", destination=destination, detail="provider=dev(outbox)")


def ensure_table() -> None:
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS otp_challenges (
            id TEXT PRIMARY KEY,
            destination TEXT NOT NULL,
            code_hmac TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            resends INTEGER DEFAULT 0,
            used INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            last_sent_at TEXT NOT NULL,
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            job TEXT DEFAULT '',
            position TEXT DEFAULT '',
            interests TEXT DEFAULT ''
        )
    """)
    # Installs created before the profile step get the columns added in place;
    # empty values are valid, so this is safe to run on every boot.
    for column in ("first_name", "last_name", "job", "position", "interests"):
        try:
            conn.execute(f"ALTER TABLE otp_challenges ADD COLUMN {column} TEXT DEFAULT ''")
        except Exception:  # noqa: BLE001 — "already present", whichever backend says it
            # This caught only sqlite3.OperationalError until PostgreSQL became
            # the production backend. psycopg raises psycopg.errors.DuplicateColumn,
            # which sailed straight through — and because ensure_table() is the
            # first statement of every OTP entry point (request, resend, verify,
            # profile read, profile update), the ENTIRE registration surface
            # returned 500 on PostgreSQL while passing on SQLite in CI.
            # Catching the base class is correct here: the only thing this ALTER
            # can legitimately hit is "column exists", and a genuinely broken
            # connection will fail again on the very next statement anyway.
            conn.rollback()  # PostgreSQL aborts the txn; without this the
                             # following statements fail with InFailedSqlTransaction.
    conn.commit()
    conn.close()


def request_challenge(raw_destination: str, first_name: str = "", last_name: str = "",
                      job: str = "", position: str = "", interests: str = "") -> dict:
    """Create a challenge, deliver a code, return public challenge state."""
    destination = normalize_destination(raw_destination)
    if destination is None:
        raise OtpError("شماره واردشده معتبر نیست.")

    ensure_table()
    conn = get_db_connection()
    try:
        hour_ago = (_now() - timedelta(hours=1)).isoformat()
        recent = conn.execute(
            "SELECT COUNT(*) FROM otp_challenges WHERE destination = ? AND created_at > ?",
            (destination, hour_ago),
        ).fetchone()[0]
        if recent >= OTP_DEST_HOURLY_LIMIT:
            _audit("rate_limit_triggered", destination=destination)
            raise OtpError("تعداد درخواست‌ها بیش از حد مجاز است. کمی بعد دوباره تلاش کنید.", status=429)

        challenge_id = secrets.token_urlsafe(24)
        code = _generate_code()
        now = _now()
        expires = now + timedelta(seconds=OTP_TTL_SECONDS)
        conn.execute(
            "INSERT INTO otp_challenges (id, destination, code_hmac, expires_at,"
            " created_at, last_sent_at, first_name, last_name, job, position, interests)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (challenge_id, destination, _code_hmac(challenge_id, code),
             expires.isoformat(), now.isoformat(), now.isoformat(),
             (first_name or "").strip()[:60], (last_name or "").strip()[:60],
             (job or "").strip()[:80], (position or "").strip()[:80],
             (interests or "").strip()[:200]),
        )
        conn.commit()
    finally:
        conn.close()

    _audit("request_created", challenge_id, destination)
    _deliver(destination, code)
    return _public_state(challenge_id, destination, expires)


def _public_state(challenge_id: str, destination: str, expires: datetime,
                  resend_at: Optional[datetime] = None) -> dict:
    now = _now()
    return {
        "challenge_id": challenge_id,
        "destination_masked": mask_destination(destination),
        "server_now": now.isoformat(),
        "expires_at": expires.isoformat(),
        "expires_in": max(0, int((expires - now).total_seconds())),
        "resend_in": max(0, int(((resend_at or (now + timedelta(seconds=OTP_RESEND_COOLDOWN)))
                                 - now).total_seconds())),
        "otp_length": OTP_LENGTH,
    }


def _load(conn, challenge_id: str):
    return conn.execute(
        "SELECT * FROM otp_challenges WHERE id = ?", (challenge_id,)
    ).fetchone()


def get_status(challenge_id: str) -> dict:
    """Timer reconciliation after refresh — no secrets, unguessable id."""
    ensure_table()
    conn = get_db_connection()
    try:
        row = _load(conn, challenge_id)
    finally:
        conn.close()
    if row is None or row["used"]:
        raise OtpError("کد منقضی شده است. دوباره درخواست دهید.", status=404)
    expires = to_naive_utc(row["expires_at"])
    resend_at = to_naive_utc(row["last_sent_at"]) + timedelta(seconds=OTP_RESEND_COOLDOWN)
    return _public_state(challenge_id, row["destination"], expires, resend_at)


def verify(challenge_id: str, raw_code: str) -> Tuple[bool, str]:
    """Constant-time verification. Returns (ok, public_message)."""
    code = normalize_digits(raw_code or "").strip()
    if not re.fullmatch(rf"\d{{{OTP_LENGTH}}}", code):
        return False, "کد واردشده صحیح نیست."

    ensure_table()
    conn = get_db_connection()
    try:
        row = _load(conn, challenge_id)
        if row is None:
            _audit("verification_failed", challenge_id, detail="unknown challenge")
            return False, "کد واردشده صحیح نیست."
        if row["used"]:
            _audit("verification_failed", challenge_id, row["destination"], "already used (replay)")
            return False, "کد منقضی شده است. دوباره درخواست دهید."
        if to_naive_utc(row["expires_at"]) < _now():
            _audit("code_expired", challenge_id, row["destination"])
            return False, "کد منقضی شده است. دوباره درخواست دهید."
        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            _audit("rate_limit_triggered", challenge_id, row["destination"], "attempt limit")
            return False, "تعداد تلاش‌های مجاز به پایان رسیده است."

        expected = row["code_hmac"]
        provided = _code_hmac(challenge_id, code)
        ok = hmac.compare_digest(expected, provided)

        if ok:
            # `TRUE`, not `1`: `used` is a real BOOLEAN in PostgreSQL, which
            # rejects an integer here (DatatypeMismatch). SQLite accepts TRUE
            # too, so this is portable. Getting this wrong meant the code was
            # validated and then the consuming UPDATE 500'd — leaving the
            # challenge unconsumed and the OTP replayable.
            conn.execute("UPDATE otp_challenges SET used = TRUE WHERE id = ?", (challenge_id,))
            conn.commit()
            _audit("verification_succeeded", challenge_id, row["destination"])
            return True, "کد با موفقیت تأیید شد."
        conn.execute("UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = ?", (challenge_id,))
        conn.commit()
        _audit("verification_failed", challenge_id, row["destination"],
               f"attempt {row['attempts'] + 1}/{OTP_MAX_ATTEMPTS}")
        return False, "کد واردشده صحیح نیست."
    finally:
        conn.close()


def profile_for(challenge_id: str) -> dict:
    """Display profile of a verified challenge (no phone number).

    Called only after `verify` returned True, so this cannot be used to read
    a profile by guessing ids — an unverified challenge yields empty strings
    exactly like an unknown one.
    """
    ensure_table()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT first_name, last_name, job, position, interests, used, destination"
            " FROM otp_challenges WHERE id = ?",
            (challenge_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["used"]:
        return {"first_name": "", "last_name": "", "destination_masked": ""}
    return {
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
        "job": row["job"] or "",
        "position": row["position"] or "",
        "interests": row["interests"] or "",
        "destination_masked": mask_destination(row["destination"]),
    }


def update_profile(challenge_id: str, job: str, position: str, interests: str) -> bool:
    """Rewrite the work profile of an ALREADY VERIFIED challenge.

    Returns False for an unknown or unverified challenge — the `used`
    condition is in the UPDATE itself, so an unverified row cannot be written
    even under a race. Name and destination are untouched: they were what the
    code proved, and nothing here is allowed to change them.
    """
    ensure_table()
    conn = get_db_connection()
    try:
        cur = conn.execute(
            # `TRUE`, not `1`, for the same reason as the UPDATE in
            # `verify()`. PostgreSQL has no boolean = integer operator, so
            # `used = 1` raised UndefinedFunction and turned every profile
            # save into a 500. SQLite has accepted TRUE since 3.23, so this
            # is portable.
            "UPDATE otp_challenges SET job = ?, position = ?, interests = ?"
            " WHERE id = ? AND used = TRUE",
            (job.strip(), position.strip(), interests.strip(), challenge_id),
        )
        conn.commit()
        changed = cur.rowcount > 0
    finally:
        conn.close()
    _audit("profile_updated" if changed else "profile_update_refused",
           challenge_id=challenge_id)
    return changed


def resend(challenge_id: str) -> dict:
    """Replace the code (invalidating the old one) after the cooldown."""
    ensure_table()
    conn = get_db_connection()
    try:
        row = _load(conn, challenge_id)
        if row is None or row["used"]:
            raise OtpError("کد منقضی شده است. دوباره درخواست دهید.", status=404)
        if row["resends"] >= OTP_MAX_RESENDS:
            _audit("rate_limit_triggered", challenge_id, row["destination"], "resend limit")
            raise OtpError("تعداد ارسال مجدد به پایان رسیده است.", status=429)
        cooldown_until = to_naive_utc(row["last_sent_at"]) + timedelta(seconds=OTP_RESEND_COOLDOWN)
        if _now() < cooldown_until:
            raise OtpError("هنوز امکان ارسال مجدد نیست.", status=429)

        code = _generate_code()
        now = _now()
        expires = now + timedelta(seconds=OTP_TTL_SECONDS)
        # New HMAC + expiry + zeroed attempts in one statement: the previous
        # code stops verifying the instant this commits.
        conn.execute(
            "UPDATE otp_challenges SET code_hmac = ?, expires_at = ?,"
            " attempts = 0, resends = resends + 1, last_sent_at = ? WHERE id = ?",
            (_code_hmac(challenge_id, code), expires.isoformat(), now.isoformat(), challenge_id),
        )
        conn.commit()
        destination = row["destination"]
    finally:
        conn.close()

    _audit("resend_requested", challenge_id, destination)
    _deliver(destination, code)
    return _public_state(challenge_id, destination, expires)
