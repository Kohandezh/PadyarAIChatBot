"""SMS delivery providers (Asanak) and where their credentials live.

The credentials NEVER reach the browser. They are read here, server-side, and
no endpoint returns them; the audit log records only the masked destination —
never the code, the username or the password.

Storage
-------
`save_settings()` writes every field to BOTH stores, in one call:

  * the `settings` table — unchanged reading precedence, so nothing else in
    the app has to know this changed;
  * the project's `.env` — what the customer asked for: the values live in the
    environment file and survive a database reset or a headless redeploy.

The two real secrets (password, API key) are ENCRYPTED in both places, with an
`enc:` prefix (see app/services/secure_store.py). Username, sender number and
URLs are stored readable on purpose — an operator needs to be able to check
them. Values written before this existed are plaintext and keep working: the
`enc:` prefix is what marks a value as encrypted, and everything else is
returned untouched.

Reading precedence is unchanged: settings table -> environment -> default.

Adding a field the customer's Asanak account turns out to need: add ONE row to
ASANAK_FIELDS, one input to templates/admin/settings_sms.html, and one line to
the request model. The save, .env and encryption paths need no change at all.

Protocol notes — verified 2026-08-16 against https://asanak.com/api-docs/sms
-----------------------------------------------------------------------------
  * Documented host is `sms.asanak.ir` (send / msgstatus / getcredit). The
    default below now points there; an install already working against
    `panel.asanak.com` keeps working because the URL is a normal setting and a
    stored value always wins over this default.
  * Authentication is `username` + `password` in the request BODY. There is no
    API-key/token scheme in the public documentation. The `api_key` field is
    still stored (an account may need it for another Asanak product) but the
    send path does not use it — do not build a key-based auth path on a guess.
  * `send_to_blacklist=0` stops delivery to blacklisted numbers; Asanak's own
    default is 1.
  * `trim` is NOT in the documentation. It is kept because the customer asked
    for it and it may be an undocumented-but-real parameter; it is only sent
    when explicitly switched off.
  * The docs show `application/json` or `multipart/form-data`. This module
    posts `application/x-www-form-urlencoded` (as Asanak's own Python sample
    does). Undocumented, but it is the shape that is in production today, so
    it is left alone until the customer confirms otherwise.
  * A success is `meta.status == 200`; `data` is the list of message ids, one
    per (comma-separated) destination.

Measured against the live account, 2026-08-17
-----------------------------------------------------------------------------
  * `destination` MUST begin 091/093 (or 9891/9893). `+989121234567` — the
    form this app stores — is refused with 406 / 1010, `Invalids:
    ["0Invalid"]`. See `asanak_destination()`.
  * `source` is validated before `destination`, so a deliberately invalid
    destination is a free way to test a sender line without queuing anything.
    Only the exact stored 18-digit line is active on this account; every
    truncation of it returns 1002.
  * A 200 means QUEUED, nothing more. Eleven real messages were accepted with
    ids across four endpoint variants (v2rest form, v2rest JSON, v1rest on
    both hosts) and every one sat at msgstatus `Status: 20` with
    `DeliverTime: 0000-00-00`, while `getcredit` never moved off 1577 — the
    account was never charged, so the messages never reached the carrier.
    Status 20 is NOT in Asanak's published table (-1,1,2,4,5,6,7,8,9,10,11,
    12,13); it is an internal hold. That is an account-side matter for Asanak
    support, not something this code can fix — do not "fix" it by retrying.
  * `POST /webservice/v2rest/template` answers 1001 "Template is not
    valid/active" for arbitrary ids. If Asanak says the line is service-only,
    OTP has to go through an approved template rather than `sendsms`.

Free text is the message path; templates are for the code (settled 2026-08-24)
-----------------------------------------------------------------------------
Two facts, in the order they were learned:

  * MEASURED 2026-08-17 on this account: free text was accepted by `sendsms`
    and sat at Status 20 forever, credit untouched, while template id 1654
    delivered (Status 6) and was charged. On that evidence this module grew a
    "service line" model where only approved templates could carry anything
    and links were refused outright.
  * SETTLED 2026-08-24 by the product owner, after talking to Asanak support:
    `sendsms` free text is a normal send path and DOES carry links; a template
    is needed only for the verification code. The August hold was an
    account-side matter, not a law of the line.

So the service/promotional switch is gone. What each message does now:

  verification code   approved template, parameter `code`, when a template id
                      is configured. MEASURED WORKING (id 1654, Status 6,
                      credit 1577 -> 1576). Still the only path that uses a
                      template. Without a template id the code goes as free
                      text, unchanged.
  invite link         free text. The body comes from the `sms_invite_text`
                      setting (admin-editable) with `{{magic_link}}` marking
                      where the link goes.
  rejection notice    free text. The body comes from `sms_reject_text`, same
                      placeholder. The reviewer's REASON does not travel by
                      SMS: it belongs on the contact's own page, which has
                      room for it and which the contact is signing in to
                      anyway. `send_reject_notice` takes no `reason` argument
                      on purpose.

Asanak error 1014 ("the sender line may not send links") can still come back
from the gateway on a line without link permission. `is_link_refusal` still
recognises it, so the caller's QR fallback (SPEC REQ-057) still fires for it.

Making a lost message visible (the reason `last_freetext_delivery` exists)
-----------------------------------------------------------------------------
A 200 from `sendsms` means QUEUED and nothing more. A message that sits at
Status 20 forever looks exactly like a delivered one to the caller, and that
is how eleven messages were lost in August without anyone noticing until the
credit was checked by hand. The August hold is exactly why this check stays
even though free text is now the normal path: a gateway can accept a message
and drop it, whatever the support line says.

So every free-text send records its msgid, and `last_freetext_delivery()` asks
`msgstatus` about it ONCE, after a grace period, and writes the verdict down.
The admin SMS settings page calls it and shows what it says. That is the whole
mechanism: no poller, no queue, no background task, one extra gateway call per
free-text message and only when an operator is looking.

It is deliberately NOT called from any send path. An OTP visitor must never
wait on a diagnostic, and the booth must never wait on one either. The cost is
that a message lost mid-event is seen when the operator next opens that page
rather than at the moment it is lost. That is the trade this file makes: a
delay before an operator sees it, never a silent success.

Only status 6 (delivered) and status 20 (the internal hold) are interpreted,
because those two are the only ones measured on this account. Any other code
is reported to the operator as a raw number with the honest statement that
this app does not know what it means. Asanak's published table is
-1,1,2,4,5,6,7,8,9,10,11,12,13; 20 is not in it.

NOT VERIFIED, and stated rather than assumed
-----------------------------------------------------------------------------
  * Whether a Persian SMS is billed per 70-character part — the free-text
    bodies here are several parts each. That cost is deliberate: a contact
    reading this weeks after a booth conversation has to be told what it is
    about.
  * An invite link is a one-time credential. It travels in the SMS body and,
    on a dev install, into the gitignored outbox. It NEVER reaches an applog
    row or a stdout line. Only the masked destination and the caller's
    reference do.

The daily budget
-----------------------------------------------------------------------------
`sms_daily_budget` is a SPEND cap, not a rate limit. `OTP_DEST_HOURLY_LIMIT`
is per destination and therefore bounds nothing about the bill: walking the
09xx range, or a loop that retries, bills the account without limit. The
budget counts every message this module sends, on whichever path, because they
all come off the same Asanak credit. Default 0 = no cap, so an install
upgrading into this code does not start refusing messages against a number
nobody chose.

Adding a gateway means adding one function and one entry in PROVIDERS.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import BASE_DIR, logger
from app.services import applog
from app.services.secure_store import protect, reveal, write_env_values

# Asanak's documented v2rest endpoints. Overridable per install for a private
# gateway, a staging host, or an account still on the older panel host.
ASANAK_DEFAULT_URL = "https://sms.asanak.ir/webservice/v2rest/sendsms"
ASANAK_STATUS_URL = "https://sms.asanak.ir/webservice/v2rest/msgstatus"
ASANAK_CREDIT_URL = "https://sms.asanak.ir/webservice/v2rest/getcredit"
ASANAK_TEMPLATE_URL = "https://sms.asanak.ir/webservice/v2rest/template"
# Asanak's own sample uses timeout=5; that is tight for a mobile gateway under
# load, and a spurious timeout costs the visitor a code. Doubled, still short
# enough that a hung gateway does not hold the request open.
TIMEOUT_SECONDS = 10

# What a visitor is allowed to see. Gateway specifics (wrong password, no
# credit) are operator business and travel in SmsError.detail instead.
_VISITOR_MESSAGE = "ارسال پیامک ناموفق بود. کمی بعد دوباره تلاش کنید."

# Where a dev install sees the link it would have texted. Deliberately the
# same file the OTP dev outbox uses, so a developer has ONE place to look.
# The path is rebuilt here rather than imported from app.services.otp: that
# module imports this one, and importing back would be circular.
_DEV_OUTBOX = os.path.join(BASE_DIR, "data", "otp-dev-outbox.log")


@dataclass(frozen=True)
class Field:
    """One gateway setting: where it is stored, and whether it is a secret."""
    key: str               # settings-table key
    env: str               # environment variable name in .env
    secret: bool = False   # encrypted at rest, never returned to the browser
    default: str = ""


# The single source of truth for the gateway's fields. The admin form, the
# .env writer, the encryption decision and the read path all read this table.
ASANAK_FIELDS = (
    Field("sms_provider", "OTP_DELIVERY", default="dev"),
    Field("sms_asanak_username", "ASANAK_USERNAME"),
    Field("sms_asanak_password", "ASANAK_PASSWORD", secret=True),
    Field("sms_asanak_api_key", "ASANAK_API_KEY", secret=True),
    Field("sms_asanak_source", "ASANAK_SOURCE"),
    # The verification code's approved template. Parameter: `code`. This is the
    # ONLY template this module sends: a template is what the account needs for
    # the code, and everything else travels as free text (settled with Asanak
    # support 2026-08-24 — see the header).
    Field("sms_asanak_template_id", "ASANAK_TEMPLATE_ID"),
    # The invite and rejection NOTICE bodies are admin-editable settings
    # (`sms_invite_text`, `sms_reject_text`) but deliberately NOT gateway
    # fields: they are message prose, not credentials, so they live in the
    # settings table only and never in .env. See `_message_body`.
    # Global daily spend cap, counted across every message this module sends.
    # 0 = no cap; see the header for why that is the default.
    Field("sms_daily_budget", "SMS_DAILY_BUDGET", default="0"),
    Field("sms_asanak_url", "ASANAK_URL", default=ASANAK_DEFAULT_URL),
    Field("sms_asanak_status_url", "ASANAK_STATUS_URL", default=ASANAK_STATUS_URL),
    Field("sms_asanak_credit_url", "ASANAK_CREDIT_URL", default=ASANAK_CREDIT_URL),
    Field("sms_asanak_template_url", "ASANAK_TEMPLATE_URL", default=ASANAK_TEMPLATE_URL),
    Field("sms_asanak_trim", "ASANAK_TRIM", default="true"),
    Field("sms_asanak_send_to_blacklist", "ASANAK_SEND_TO_BLACKLIST", default="1"),
    Field("otp_sms_host", "OTP_SMS_HOST"),
)
FIELDS_BY_KEY = {f.key: f for f in ASANAK_FIELDS}

# meta.status -> what the operator should read. Straight from Asanak's
# published error list; 1008 is ambiguous (validation vs. auth) and is
# disambiguated by the HTTP status below.
ASANAK_ERRORS = {
    1001: "قالب پیامک تأیید نشده یا فعال نیست. شناسهٔ قالب را در پنل آسانک "
          "بررسی کنید و همان شناسه را در تنظیمات پیامک وارد کنید.",
    1002: "شماره فرستنده فعال نمی‌باشد.",
    1004: "خطای داخلی سرور سامانه پیامک.",
    1005: "اعتبار پنل نمایندگی کافی نیست.",
    1006: "اعتبار حساب پیامکی برای ارسال کافی نیست.",
    1008: "خطای اعتبارسنجی پارامترهای ورودی.",
    1009: "محدودیت ارسال روزانه وب‌سرویس به پایان رسیده است.",
    1010: "لیست شماره‌های مقصد صحیح و معتبر نمی‌باشد.",
    1013: "بازه زمانی غیرمجاز برای ارسال پیامک تبلیغاتی.",
    1014: "شماره فرستنده مجاز به ارسال لینک نمی‌باشد. متن کد تأیید نشانی سایت "
          "را همراه دارد — «دامنه سایت برای تکمیل خودکار کد» را خالی بگذارید "
          "یا از آسانک اجازه ارسال لینک بگیرید.",
    1015: "رمز وب‌سرویس منقضی شده است. در پنل آسانک رمز تازه بسازید و همین‌جا وارد کنید.",
    429: "تعداد درخواست‌ها بیش از حد مجاز است. کمی بعد دوباره تلاش کنید.",
}
_AUTH_MESSAGE = "نام کاربری یا رمز عبور وب‌سرویس درست نیست."

# `SmsError.code` is normally Asanak's numeric meta.status. This is the one
# non-numeric value it takes: the refusal came from THIS module, before any
# request was made, because the day's budget is spent. A string cannot collide
# with an Asanak code, so a caller can compare without guessing.
BUDGET_EXHAUSTED = "budget_exhausted"

# "The sender line may not send links" (Asanak 1014). The one refusal a caller
# must be able to act on differently: the invite still has to reach the
# contact, so the booth falls back to the QR channel instead of failing the
# registration (SPEC REQ-057). 1001 is deliberately NOT in here: an unapproved
# template is a template problem an operator fixes in the panel, not a missing
# permission.
LINK_REFUSAL_CODES = (1014,)

# ── Was the last free-text message actually delivered? ──────────────────
# One settings row holds the last free-text send and, once asked, the verdict:
#   "<msgid>|<iso sent_at>|<subcategory>|<verdict>"
# An empty verdict means "not asked yet". Writing the verdict back into the
# same row is what makes this a one-shot check instead of a poller.
_LAST_FREETEXT_KEY = "sms_last_freetext"
# How long a queued message is allowed to look pending before its status is
# worth asking about. Below this, a Status 20 says nothing: a message queued
# thirty seconds ago is supposed to be pending.
_DELIVERY_GRACE_SECONDS = 180
# The only two msgstatus codes measured on this account (2026-08-17). Every
# other code is reported as a raw number, not guessed at.
_STATUS_DELIVERED = 6
_STATUS_HELD = 20


class SmsError(Exception):
    """Delivery failed.

    `str(e)` is the generic sentence a VISITOR may see. `e.detail` is the
    operator-facing reason (the gateway's own error, translated) and is shown
    only inside the admin panel; `e.code` is Asanak's numeric meta.status.
    """

    def __init__(self, message: str = _VISITOR_MESSAGE, detail: str = "", code=None):
        super().__init__(message)
        self.detail = detail or message
        self.code = code


def setting(key: str) -> str:
    """Effective value of one gateway setting: table -> env -> default.

    Both stores may hold an encrypted secret; both are decrypted here (the
    settings table transparently, via get_setting).
    """
    from app.db.queries import get_setting
    field = FIELDS_BY_KEY.get(key)
    value = (get_setting(key, "") or "").strip()
    if value:
        return value
    if field is None:
        return ""
    if field.env:
        value = reveal(os.getenv(field.env, "")).strip()
    return value or field.default


def save_settings(values: dict) -> bool:
    """Persist {settings key: raw value} to the settings table AND to .env.

    Secrets are encrypted before either store sees them. An empty secret means
    "keep the stored one" — that is what lets an operator change the sender
    number without re-typing the password. Returns True when the environment
    file was updated as well; False means only the database has the new values
    (e.g. a read-only deployment), which the admin panel then reports.
    """
    from app.db.queries import set_setting
    env_updates = {}
    for key, raw in values.items():
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            continue  # not a gateway field — not ours to store
        raw = (raw or "").strip()
        if field.secret and not raw:
            continue
        stored = protect(raw) if field.secret else raw
        set_setting(key, stored)
        if field.env:
            env_updates[field.env] = stored
    return write_env_values(env_updates)


def asanak_configured() -> bool:
    return bool(setting("sms_asanak_username")
                and setting("sms_asanak_password")
                and setting("sms_asanak_source"))


def is_link_refusal(error: Exception) -> bool:
    """True when the gateway refused because the line may not send links.

    This is the one failure a caller treats differently: the contact still
    needs the link, so the booth shows the QR instead (SPEC REQ-057). Every
    other failure is a plain failure and must be reported as one.
    """
    return getattr(error, "code", None) in LINK_REFUSAL_CODES


# ── Daily spend budget ──────────────────────────────────────────────────
# The counter is a settings row, not a gateway field: it is runtime state, so
# it must never be written into .env. Its value is "YYYY-MM-DD:count", which
# makes the daily reset free: a stale date simply reads as zero. The day
# boundary is UTC, like every other timestamp in this app.
_BUDGET_COUNTER_KEY = "sms_daily_count"


def _utcnow() -> datetime:
    """Naive UTC, the form every timestamp in this app is stored in.
    Built from an aware value because datetime.utcnow() is deprecated."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def daily_budget() -> int:
    """Messages allowed per day across every send path. 0 = no cap."""
    try:
        return max(0, int((setting("sms_daily_budget") or "0").strip()))
    except ValueError:
        # A typo in the setting must not become "unlimited spend" by accident.
        logger.error("[sms] sms_daily_budget is not a number, treating it as 0")
        return 0


def _budget_today():
    from app.db.queries import get_setting
    today = _utcnow().date().isoformat()
    day, _, used = (get_setting(_BUDGET_COUNTER_KEY, "") or "").partition(":")
    if day != today:
        return today, 0
    try:
        return today, int(used)
    except ValueError:
        return today, 0


def sent_today() -> int:
    """Messages already spent against today's budget (operator diagnostics)."""
    return _budget_today()[1]


def _spend_budget(kind: str) -> None:
    """Book one message against today's budget, or refuse before sending.

    Counted BEFORE the request, not after. A request the gateway rejects costs
    nothing at Asanak, so this over-counts on a bad day, which stops sending
    early rather than late. For a spend cap that is the safe direction, and a
    crash between the count and the send cannot leave a message unbilled.
    """
    budget = daily_budget()
    if not budget:
        return
    today, used = _budget_today()
    if used >= budget:
        # Loud on purpose. A budget that stops sending quietly is worse than
        # no budget: the operator finds out from the visitors.
        applog.critical("sms", "sms.budget.exhausted",
                        "بودجهٔ روزانهٔ پیامک تمام شد و ارسال متوقف شده است",
                        provider="asanak", subcategory=kind, outcome="denied",
                        metadata={"budget": budget, "sent_today": used})
        logger.error("[sms] daily budget of %s messages is spent, refusing %s", budget, kind)
        raise SmsError(
            detail="بودجهٔ روزانهٔ پیامک (%s پیامک) تمام شده است و تا فردا "
                   "پیامکی فرستاده نمی‌شود. برای ادامه، سقف روزانه را در "
                   "تنظیمات پیامک بالا ببرید." % budget,
            code=BUDGET_EXHAUSTED,
        )

    from app.db.queries import set_setting
    set_setting(_BUDGET_COUNTER_KEY, "%s:%d" % (today, used + 1))
    # One warning while there is still time to act, not only at the wall.
    # The equality makes it fire exactly once per day.
    if used + 1 == (budget * 4) // 5:
        applog.warning("sms", "sms.budget.low",
                       "۸۰ درصد بودجهٔ روزانهٔ پیامک مصرف شده است",
                       provider="asanak", subcategory=kind, outcome="ok",
                       metadata={"budget": budget, "sent_today": used + 1})


def _error_text(http_status: int, code) -> str:
    """The operator-facing reason for a gateway refusal."""
    if http_status == 401:
        return _AUTH_MESSAGE if code in (1008, None) else ASANAK_ERRORS.get(code, _AUTH_MESSAGE)
    known = ASANAK_ERRORS.get(code)
    if known:
        return known
    if code is not None:
        return "سامانه پیامک درخواست را نپذیرفت (کد %s)." % code
    return _VISITOR_MESSAGE


def _http_post(url: str, payload: dict):
    """POST a form-encoded body. Returns (http status, response text).

    On a transport failure only the exception TYPE is logged: the request body
    carries the account password. The response body of a 4xx/5xx is read too —
    Asanak puts its own error code in there.
    """
    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001 — an unreadable error body is still an error
            body = ""
        return e.code, body
    except Exception as e:  # noqa: BLE001 — network failures are expected
        logger.error("[sms] asanak request failed: %s", type(e).__name__)
        raise SmsError(detail="ارتباط با سامانه پیامک برقرار نشد.")


def _result(http_status: int, body: str):
    """Asanak's `data` on success; SmsError with the mapped reason otherwise."""
    document = None
    if body.startswith("{"):
        try:
            document = json.loads(body)
        except ValueError:
            document = None

    if isinstance(document, dict):
        meta = document.get("meta") or {}
        try:
            code = int(meta.get("status"))
        except (TypeError, ValueError):
            code = None
        if http_status == 200 and code == 200:
            return document.get("data")
        logger.error("[sms] asanak refused the request: http=%s code=%s", http_status, code)
        raise SmsError(detail=_error_text(http_status, code), code=code)

    # Legacy plaintext answer (the older panel host replies with a bare
    # number: positive = message id). Kept so an install pointing at the old
    # endpoint does not break on upgrade.
    if body.lstrip("-").isdigit():
        number = int(body)
        if http_status == 200 and number > 0:
            return [number]
        logger.error("[sms] asanak rejected the message, code=%s", number)
        raise SmsError(detail=_error_text(http_status, number), code=number)

    # A 403 whose body is a CDN error page — not Asanak's own JSON — means the
    # gateway's WAF refused this machine before the request ever reached the
    # API. Telling the operator to "try again later" would be wrong: an IP or
    # geographic block does not clear on its own, and the credentials are not
    # the problem. Say what actually has to happen.
    if http_status == 403 and "<html" in body.lower():
        logger.error("[sms] asanak refused this host at the CDN (403 access denied)")
        raise SmsError(
            detail="سامانهٔ آسانک دسترسی این سرور را مسدود کرده است (۴۰۳). "
                   "این خطای نام کاربری یا رمز نیست — باید IP این سرور در پنل "
                   "آسانک مجاز شود، یا برنامه از سروری اجرا شود که آسانک "
                   "دسترسی‌اش را می‌پذیرد.",
            code=403,
        )

    logger.error("[sms] asanak sent an unreadable response, http=%s", http_status)
    raise SmsError(detail=_error_text(http_status, None))


def _call(url: str, payload: dict):
    return _result(*_http_post(url, payload))


def _credentials() -> dict:
    return {
        "username": setting("sms_asanak_username"),
        "password": setting("sms_asanak_password"),
    }


def asanak_status(msgid: str):
    """Delivery status of a previously sent message (operator diagnostics)."""
    payload = _credentials()
    payload["msgid"] = msgid
    return _call(setting("sms_asanak_status_url"), payload)


def _status_code(data):
    """The numeric status inside a msgstatus answer, or None.

    The exact shape of `data` was NOT re-verified while writing this. What is
    recorded from 2026-08-17 is that the answer carried `Status: 20` alongside
    `DeliverTime: 0000-00-00`. So this accepts a dict, a one-element list of
    dicts, or a bare number, and looks for a `status` key case-insensitively.
    Anything it cannot read returns None, which the caller reports as unknown
    rather than as delivered.
    """
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).strip().lower() == "status":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None
    try:
        return int(data)
    except (TypeError, ValueError):
        return None


def _remember_freetext(msgid, subcategory: str) -> None:
    """Note the free-text message whose delivery is still unproven.

    Never raises. A settings write that fails must not turn a message the
    gateway accepted into an error the caller sees.
    """
    if not msgid:
        return
    try:
        from app.db.queries import set_setting
        set_setting(_LAST_FREETEXT_KEY,
                    "%s|%s|%s|" % (msgid, _utcnow().isoformat(), subcategory))
    except Exception:  # noqa: BLE001 (diagnostics must never break a send)
        logger.error("[sms] could not record the last free-text message id")


def _verdict_text(verdict: str, msgid: str) -> str:
    """The operator-facing sentence for a stored verdict."""
    if verdict == "delivered":
        return "آخرین پیامک متن‌آزاد به گوشی رسید."
    if verdict == "held":
        return ("آخرین پیامک متن‌آزاد (شناسه %s) را آسانک پذیرفت ولی به گوشی "
                "نرساند و در وضعیت ۲۰ مانده است. این یعنی خط شما متن آزاد "
                "نمی‌فرستد. «نوع خط» را روی «خدماتی» بگذارید تا پیام‌ها از "
                "قالب تأییدشده فرستاده شوند، و برای بقیه با پشتیبانی آسانک "
                "تماس بگیرید." % msgid)
    if verdict == "unreadable":
        return ("وضعیت آخرین پیامک متن‌آزاد (شناسه %s) از آسانک خوانده نشد. "
                "رسیدن پیام را خودتان بررسی کنید." % msgid)
    if verdict.startswith("code:"):
        return ("وضعیت آخرین پیامک متن‌آزاد (شناسه %s) از آسانک عدد %s برگشت. "
                "این برنامه معنی این عدد را نمی‌داند؛ همین عدد را از پشتیبانی "
                "آسانک بپرسید." % (msgid, verdict[5:]))
    return ""


def last_freetext_delivery() -> dict:
    """Did the last free-text message actually arrive? Asked once, then kept.

    This is the whole answer to "a 200 means queued, nothing more". Every
    free-text send leaves its msgid behind; this asks `msgstatus` about it one
    time, after `_DELIVERY_GRACE_SECONDS`, records the verdict in the same
    settings row and never asks again until the next free-text send.

    Called from the admin SMS settings page and from nowhere else. No visitor
    and no booth ever waits on a gateway roundtrip for a diagnostic.

    Returns {"state", "msgid", "message"}. `state` is one of none, waiting,
    delivered, held, unreadable, code, unknown.
    """
    from app.db.queries import get_setting, set_setting
    raw = (get_setting(_LAST_FREETEXT_KEY, "") or "").strip()
    if not raw:
        return {"state": "none", "msgid": "", "message": ""}

    parts = (raw.split("|") + ["", "", "", ""])[:4]
    msgid, sent_at, subcategory, verdict = parts
    if verdict:
        state = "code" if verdict.startswith("code:") else verdict
        return {"state": state, "msgid": msgid, "message": _verdict_text(verdict, msgid)}

    try:
        age = (_utcnow() - datetime.fromisoformat(sent_at)).total_seconds()
    except ValueError:
        age = _DELIVERY_GRACE_SECONDS  # unreadable timestamp: ask now
    if age < _DELIVERY_GRACE_SECONDS:
        return {"state": "waiting", "msgid": msgid,
                "message": "آخرین پیامک متن‌آزاد تازه فرستاده شده است. چند دقیقه "
                           "بعد این صفحه را دوباره باز کنید تا معلوم شود به گوشی "
                           "رسیده یا نه."}

    try:
        code = _status_code(asanak_status(msgid))
    except SmsError as e:
        # The gateway could not be asked. Leave the verdict empty so the next
        # page load asks again. An unanswered question is not an answer.
        return {"state": "unknown", "msgid": msgid, "message": e.detail}

    if code == _STATUS_DELIVERED:
        verdict = "delivered"
    elif code == _STATUS_HELD:
        verdict = "held"
    elif code is None:
        verdict = "unreadable"
    else:
        verdict = "code:%d" % code

    message = _verdict_text(verdict, msgid)
    try:
        set_setting(_LAST_FREETEXT_KEY, "%s|%s|%s|%s" % (msgid, sent_at, subcategory, verdict))
    except Exception:  # noqa: BLE001 (a verdict we cannot store is still worth reporting)
        logger.error("[sms] could not store the delivery verdict for msgid=%s", msgid)

    if verdict == "delivered":
        applog.info("sms", "sms.delivery.confirmed", "پیامک متن‌آزاد به گوشی رسید",
                    provider="asanak", subcategory=subcategory, outcome="ok",
                    metadata={"msgid": msgid, "status": code})
    elif verdict == "held":
        # Loud. This is the failure that looks like a success, and an operator
        # reading the log later must find it without knowing to look.
        applog.critical("sms", "sms.delivery.lost",
                        "پیامک متن‌آزاد پذیرفته شد ولی به گوشی نرسید",
                        provider="asanak", subcategory=subcategory, outcome="failed",
                        metadata={"msgid": msgid, "status": code})
        logger.error("[sms] msgid=%s was accepted and never delivered (status 20)", msgid)
    else:
        applog.warning("sms", "sms.delivery.unknown",
                       "وضعیت تحویل پیامک متن‌آزاد مشخص نشد",
                       provider="asanak", subcategory=subcategory, outcome="failed",
                       metadata={"msgid": msgid, "status": code})

    return {"state": "code" if verdict.startswith("code:") else verdict,
            "msgid": msgid, "message": message}


def asanak_credit() -> int:
    """Remaining credit, in messages.

    A read-only call: it proves the username and password are right WITHOUT
    sending a real SMS, which is what an operator wants before an event.
    """
    data = _call(setting("sms_asanak_credit_url"), _credentials())
    applog.info("sms", "sms.credit.checked", "اعتبار پیامک بررسی شد",
                provider="asanak", outcome="ok",
                metadata={"credit": (data or {}).get("credit") if isinstance(data, dict) else None})
    if isinstance(data, dict):
        try:
            return int(data.get("credit"))
        except (TypeError, ValueError):
            pass
    raise SmsError(detail="پاسخ سامانه پیامک قابل خواندن نبود.")


def asanak_destination(destination: str) -> str:
    """The app's canonical `+98…` form in the shape Asanak actually accepts.

    Measured 2026-08-17 against the live gateway: `+989122723024` is refused
    with HTTP 406 / meta.status 1010 ("Destination list is not valid",
    Invalids: ["0Invalid"]), while the same number as `09122723024` or
    `989122723024` is accepted. The documentation agrees — a destination must
    begin `091`/`093` or `9891`/`9893`, never `+`.

    The app stores E.164 with the `+` on purpose (it is the visitor's identity
    in `otp_challenges`), so the `+` is stripped HERE, at the gateway edge,
    rather than by weakening `normalize_destination`.
    """
    digits = (destination or "").strip().lstrip("+")
    if digits.startswith("98") and len(digits) > 10:
        digits = "0" + digits[2:]
    return digits


def _http_post_json(url: str, document: dict):
    """POST a JSON body. Same contract as _http_post; the template endpoint is
    the one method Asanak documents as application/json only."""
    data = json.dumps(document).encode()
    request = urllib.request.Request(
        url, data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001 — an unreadable error body is still an error
            body = ""
        return e.code, body
    except Exception as e:  # noqa: BLE001 — network failures are expected
        logger.error("[sms] asanak template request failed: %s", type(e).__name__)
        raise SmsError(detail="ارتباط با سامانه پیامک برقرار نشد.")


def _send_template(template_id: str, destination: str, parameters: dict,
                   subcategory: str, missing_detail: str):
    """POST one approved template and return Asanak's message id.

    Both template paths go through here (the verification code and the
    rejection notice) so the budget, the payload shape and the logging live in
    one place. `parameters` are matched by NAME against the template stored in
    Asanak's panel; renaming one there without renaming it here breaks delivery
    silently. An empty dict is a valid argument: the rejection template has no
    slots at all, which is what keeps prose out of an approved text.

    Nothing from `parameters` reaches a log row. The verification code is a
    credential, and only the template id and the masked destination are safe
    to keep.
    """
    template_id = (template_id or "").strip()
    if not template_id.isdigit():
        # An unconfigured template is an operator error, not a gateway error.
        # Say which setting is empty rather than letting int() raise.
        raise SmsError(detail=missing_detail)

    _spend_budget(subcategory)

    payload = _credentials()
    payload.update({
        "template_id": int(template_id),
        "destination": asanak_destination(destination),
        "parameters": parameters,
    })
    if setting("sms_asanak_send_to_blacklist").strip() == "0":
        payload["send_to_blacklist"] = 0

    url = setting("sms_asanak_template_url")
    started = time.perf_counter()
    try:
        data = _result(*_http_post_json(url, payload))
    except SmsError as e:
        applog.error("sms", "sms.send.failed", "ارسال قالب پیامک ناموفق بود",
                     provider="asanak", subcategory=subcategory,
                     duration_ms=int((time.perf_counter() - started) * 1000),
                     error_code=str(getattr(e, "code", "") or ""),
                     error_type="SmsError", outcome="failed",
                     target=asanak_destination(destination),
                     metadata={"template_id": template_id,
                               "destination": applog.mask_phone(destination),
                               "detail": e.detail})
        raise
    msgid = data[0] if isinstance(data, list) and data else None
    logger.info("[sms] asanak queued template %s, msgid=%s", template_id, msgid)
    # "queued", never "delivered" — the gateway returns 200 the moment it
    # accepts a message, and one that is accepted can still never arrive.
    applog.info("sms", "sms.send.queued", "پیامک قالبی در صف ارسال قرار گرفت",
                provider="asanak", subcategory=subcategory, outcome="queued",
                duration_ms=int((time.perf_counter() - started) * 1000),
                target=asanak_destination(destination),
                metadata={"template_id": template_id, "msgid": msgid,
                          "destination": applog.mask_phone(destination)})
    return msgid


def send_asanak_template(destination: str, code: str):
    """Send the verification code through an APPROVED Asanak template.

    Measured 2026-08-17 on this account, minutes apart:

        sendsms  (free text)      -> queued, Status 20 forever, credit unchanged
        template (id 1654)        -> Status 6 "Success", delivered 22:55:35,
                                     credit 1577 -> 1576

    and settled with Asanak support 2026-08-24: the code is the one message
    that needs an approved template; everything else goes as free text. The
    template's parameter is named `code`.
    """
    return _send_template(
        setting("sms_asanak_template_id"), destination, {"code": str(code)},
        subcategory="template",
        missing_detail="شناسهٔ قالب کد تأیید تنظیم نشده است. آن را در تنظیمات "
                       "پیامک وارد کنید.")


def send_asanak(destination: str, message: str, code: str = None,
                subcategory: str = "freetext"):
    """Send one SMS through the Asanak gateway. Returns Asanak's message id.

    When a template id is configured AND a code was supplied, the approved
    template is used — the code is the one message the account needs a
    template for (settled with Asanak support 2026-08-24). Everything else,
    and any install without a template yet, sends free text.

    `subcategory` only labels the log rows, so an operator can tell an invite
    apart from a verification code in the same list.
    """
    if setting("sms_asanak_template_id").strip() and code:
        return send_asanak_template(destination, code)

    payload = _credentials()
    source = setting("sms_asanak_source")

    if not (payload["username"] and payload["password"] and source):
        raise SmsError("سرویس پیامک پیکربندی نشده است.")

    payload.update({
        "source": source,
        "destination": asanak_destination(destination),
        "message": message,
    })
    # Both are only sent when they differ from the gateway's own default, so a
    # plain install posts exactly the documented parameter set.
    if setting("sms_asanak_trim").lower() in ("false", "0", "no"):
        payload["trim"] = "false"
    if setting("sms_asanak_send_to_blacklist").strip() == "0":
        payload["send_to_blacklist"] = "0"

    _spend_budget(subcategory)

    _started = time.perf_counter()
    try:
        data = _call(setting("sms_asanak_url"), payload)
    except SmsError as e:
        applog.error("sms", "sms.send.failed", "ارسال پیامک متن آزاد ناموفق بود",
                     provider="asanak", subcategory=subcategory,
                     duration_ms=int((time.perf_counter() - _started) * 1000),
                     error_code=str(getattr(e, "code", "") or ""),
                     error_type="SmsError", outcome="failed",
                     metadata={"destination": applog.mask_phone(destination),
                               "detail": e.detail})
        raise

    # Keep the id. "Accepted" only means Asanak queued it — a message can sit
    # undelivered for hours with a perfectly successful send response, and
    # without the id there is no way to ask `msgstatus` what became of it.
    msgid = None
    if isinstance(data, list) and data:
        msgid = data[0]
    logger.info("[sms] asanak queued the message, msgid=%s", msgid)
    applog.info("sms", "sms.send.queued", "پیامک در صف ارسال قرار گرفت",
                provider="asanak", subcategory=subcategory, outcome="queued",
                duration_ms=int((time.perf_counter() - _started) * 1000),
                metadata={"msgid": msgid,
                          "destination": applog.mask_phone(destination)})
    # The id is the thread back to a message that never arrived. Stored, not
    # only logged, because `last_freetext_delivery()` has to be able to find it
    # without anyone reading a log file. See the header.
    _remember_freetext(msgid, subcategory)
    return msgid


# ── The invite and the rejection notice ─────────────────────────────────
# Both travel as free text; the only template this module sends is the
# verification code (see the header). The bodies are the operator's own words,
# stored in `sms_invite_text` / `sms_reject_text`, with `{{magic_link}}`
# marking where the one-time link goes. Empty setting = the built-in default.

# The shared 1014 text tells an OTP operator to blank the autofill host, which
# is right there and wrong here: on this path the link IS the message. Same
# gateway code, different thing to do about it.
_LINK_NOT_PERMITTED = (
    "شماره فرستنده مجاز به ارسال لینک نمی‌باشد (خطای ۱۰۱۴ آسانک). این خطای "
    "تنظیمات نیست و با عوض کردن هیچ فیلدی درست نمی‌شود: باید از آسانک برای این "
    "خط اجازهٔ ارسال لینک گرفته شود. تا آن وقت کانال تحویل دعوت‌نامه را روی QR "
    "بگذارید.")
_NOT_CONFIGURED = (
    "سرویس پیامک پیکربندی نشده است. نام کاربری، رمز عبور و شماره فرستنده را در "
    "تنظیمات پیامک وارد کنید.")
# Where the link goes inside an operator-written body. One token, deliberately
# not {format} syntax: an operator's text may contain a brace of its own, and
# str.format would then raise on a message the operator did not think was code.
MAGIC_LINK_TOKEN = "{{magic_link}}"


def _brand() -> str:
    """The name the contact will recognise. Same setting the OTP body uses."""
    from app.db.queries import get_setting
    return (get_setting("otp_brand_name", "") or "INOTEX").strip()


def _default_invite_text() -> str:
    """The invite, written for someone who last thought about this weeks ago.

    It says who is writing, why they have this number, what the link does and
    how long it lasts.
    """
    return (
        "سلام. این پیام از چت‌بات %s است.\n"
        "جهت تأیید اطلاعات خود در چت‌بات نمایشگاه به لینک زیر مراجع کنید:\n"
        "%s\n"
        "لینک تا ۲۴ ساعت باز است و فقط یک بار کار می‌کند. آن را برای کسی نفرستید."
        % (_brand(), MAGIC_LINK_TOKEN))


def _default_reject_text() -> str:
    """The rejection notice. The reviewer's reason is NOT in here.

    It lives on the contact's own page, which is where the link goes and where
    there is room to read it.
    """
    return (
        "سلام. این پیام از چت‌بات %s است.\n"
        "متنی که برای معرفی شرکت خودتان فرستادید تأیید نشد.\n"
        "برای دیدن دلیل و فرستادن متن اصلاح‌شده به لینک زیر مراجع کنید:\n"
        "%s\n"
        "لینک تا ۲۴ ساعت باز است. آن را برای کسی نفرستید."
        % (_brand(), MAGIC_LINK_TOKEN))


def default_message_text(kind: str) -> str:
    """The built-in body for one kind, with the token still in place.

    The admin panel shows this beside the empty box, so an operator deciding
    whether to type their own words starts from what the default actually
    says rather than from a guess.
    """
    return _default_reject_text() if kind == "reject" else _default_invite_text()


def message_body(kind: str, link: str) -> str:
    """Compose one link message from the operator's stored words.

    `kind` is "invite" or "reject". The stored setting wins; empty means the
    built-in default. `{{magic_link}}` is replaced with `link`.

    A body WITHOUT the token is not an error: a link dropped from an
    operator-written SMS is a contact who cannot ever reach their page, so the
    link is appended on its own line rather than lost. The admin panel refuses
    to save such a body in the first place; this is the belt under that brace,
    because a setting can also be written by hand.
    Read through `setting()` — the settings-table read comes first there, so
    a value an operator saved is found even though this key has no Field
    entry, and the test suite's setting stub covers it too.
    """
    if kind == "reject":
        text = setting("sms_reject_text")
        if not text:
            text = _default_reject_text()
    else:
        text = setting("sms_invite_text")
        if not text:
            text = _default_invite_text()
    if MAGIC_LINK_TOKEN not in text:
        logger.warning("[sms] %s text has no %s; appending the link",
                       kind, MAGIC_LINK_TOKEN)
        text = text + "\n" + link
    return text.replace(MAGIC_LINK_TOKEN, link)


def _dev_outbox(destination: str, purpose: str, link: str) -> None:
    """Write what would have been texted, on a dev install only.

    The link is the credential, so it is written in full here and nowhere
    else: this file is gitignored and exists so a developer can finish the
    flow without a gateway. A production install never reaches this path,
    because `sms_provider` is `asanak` there.
    """
    os.makedirs(os.path.dirname(_DEV_OUTBOX), exist_ok=True)
    with open(_DEV_OUTBOX, "a", encoding="utf-8") as f:
        f.write("%s %s %s=%s\n" % (_utcnow().isoformat(),
                                   applog.mask_phone(destination), purpose, link))


def _active_provider() -> str:
    from app.db.queries import get_setting
    return (get_setting("sms_provider", "")
            or os.getenv("OTP_DELIVERY", "dev")).strip().lower()


def _dev_link(destination: str, link: str, subcategory: str, reference: str):
    _dev_outbox(destination, subcategory, link)
    applog.info("sms", "sms.link.queued", "لینک در صندوق آزمایشی نوشته شد",
                provider="dev", subcategory=subcategory, outcome="queued",
                metadata={"destination": applog.mask_phone(destination),
                          "reference": reference})
    return None


def _link_queued(msgid, destination: str, subcategory: str, reference: str) -> None:
    """One log row per link that left, keyed by the caller's own id.

    Without it "did this contact ever get the SMS" is unanswerable a week
    later. The link itself is never in the row.
    """
    if reference:
        applog.info("sms", "sms.link.queued", "پیامک برای مخاطب فرستاده شد",
                    provider="asanak", subcategory=subcategory, outcome="queued",
                    metadata={"msgid": msgid, "reference": reference,
                              "destination": applog.mask_phone(destination)})


def send_invite_link(destination: str, link: str, reference: str = ""):
    """Text a company contact the link that lets them edit their own answer.

    Free text, body from `sms_invite_text` with `{{magic_link}}` replaced by
    `link`. A 1014 from the gateway (a line without link permission) is
    re-raised with the operator-facing sentence, and `is_link_refusal(e)` is
    True for it — the case the booth answers with a QR code (SPEC REQ-057).

    `e.detail` is the operator-facing sentence for the admin panel; `str(e)` is
    all a visitor may see.
    """
    if _active_provider() == "dev":
        return _dev_link(destination, link, "invite", reference)
    if not asanak_configured():
        raise SmsError(detail=_NOT_CONFIGURED)

    try:
        msgid = send_asanak(destination, message_body("invite", link),
                            subcategory="invite")
    except SmsError as e:
        if is_link_refusal(e):
            raise SmsError(str(e), detail=_LINK_NOT_PERMITTED, code=e.code) from e
        raise
    _link_queued(msgid, destination, "invite", reference)
    return msgid


def send_reject_notice(destination: str, link: str, reference: str = ""):
    """Tell a contact their proposed text was not approved.

    Free text, body from `sms_reject_text` with `{{magic_link}}` replaced by
    `link`.

    There is no `reason` argument and there will not be one. The reviewer's
    reason belongs on `/my`, which has room for it and which the contact is
    signing in to anyway.

    The rejection itself stands whether or not this succeeds (SPEC REQ-067):
    the caller records that the contact was not reached, it does not undo the
    review.
    """
    if _active_provider() == "dev":
        return _dev_link(destination, link, "reject", reference)
    if not asanak_configured():
        raise SmsError(detail=_NOT_CONFIGURED)

    try:
        msgid = send_asanak(destination, message_body("reject", link),
                            subcategory="reject")
    except SmsError as e:
        if is_link_refusal(e):
            raise SmsError(str(e), detail=_LINK_NOT_PERMITTED, code=e.code) from e
        raise
    _link_queued(msgid, destination, "reject", reference)
    return msgid


PROVIDERS = {
    "asanak": {
        "send": send_asanak,
        "configured": asanak_configured,
        "credit": asanak_credit,
    },
}


def send(provider: str, destination: str, message: str, code: str = None):
    """Deliver one message. `code` is the verification code on its own, which a
    template-based gateway needs as a parameter rather than as body text."""
    entry = PROVIDERS.get(provider)
    if entry is None:
        raise SmsError("سرویس ارسال پیامک در دسترس نیست.")
    return entry["send"](destination, message, code)


def credit(provider: str) -> int:
    entry = PROVIDERS.get(provider)
    if entry is None or "credit" not in entry:
        raise SmsError("سرویس ارسال پیامک در دسترس نیست.")
    return entry["credit"]()


def is_configured(provider: str) -> bool:
    entry = PROVIDERS.get(provider)
    return bool(entry and entry["configured"]())
