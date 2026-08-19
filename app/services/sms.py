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

Adding a gateway means adding one function and one entry in PROVIDERS.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from app.config import logger
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
    Field("sms_asanak_template_id", "ASANAK_TEMPLATE_ID"),
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


def send_asanak_template(destination: str, code: str):
    """Send the verification code through an APPROVED Asanak template.

    This exists because free text does not arrive on this account's line.
    Measured 2026-08-17 on the same number, minutes apart:

        sendsms  (free text)      -> queued, Status 20 forever, credit unchanged
        template (id 1654)        -> Status 6 "Success", delivered 22:55:35,
                                     credit 1577 -> 1576

    A service line only carries content its operator has approved, so the code
    travels as a PARAMETER of a stored template rather than as a message body.
    The template's parameter is named `code`; changing the template in Asanak's
    panel without changing that name here breaks delivery silently.
    """
    template_id = setting("sms_asanak_template_id")
    payload = _credentials()
    payload.update({
        "template_id": int(template_id),
        "destination": asanak_destination(destination),
        "parameters": {"code": str(code)},
    })
    if setting("sms_asanak_send_to_blacklist").strip() == "0":
        payload["send_to_blacklist"] = 0

    url = setting("sms_asanak_template_url")
    started = time.perf_counter()
    try:
        data = _result(*_http_post_json(url, payload))
    except SmsError as e:
        applog.error("sms", "sms.send.failed", "ارسال قالب پیامک ناموفق بود",
                     provider="asanak", subcategory="template",
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
                provider="asanak", subcategory="template", outcome="queued",
                duration_ms=int((time.perf_counter() - started) * 1000),
                target=asanak_destination(destination),
                metadata={"template_id": template_id, "msgid": msgid,
                          "destination": applog.mask_phone(destination)})
    return msgid


def send_asanak(destination: str, message: str, code: str = None):
    """Send one SMS through the Asanak gateway. Returns Asanak's message id.

    When a template id is configured AND a code was supplied, the approved
    template is used — on a service line that is the only path that reaches a
    handset. Free text stays the fallback so an install on a promotional line,
    or one with no template yet, keeps working unchanged.
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

    _started = time.perf_counter()
    try:
        data = _call(setting("sms_asanak_url"), payload)
    except SmsError as e:
        applog.error("sms", "sms.send.failed", "ارسال پیامک متن آزاد ناموفق بود",
                     provider="asanak", subcategory="freetext",
                     duration_ms=int((time.perf_counter() - _started) * 1000),
                     error_code=str(getattr(e, "code", "") or ""),
                     error_type="SmsError", outcome="failed",
                     metadata={"destination": applog.mask_phone(destination),
                               "detail": e.detail})
        raise

    # Keep the id. "Accepted" only means Asanak queued it — a message can sit
    # undelivered for hours with a perfectly successful send response, and
    # without the id there is no way to ask `msgstatus` what became of it.
    # This log line is the only thread back to a message that never arrived.
    msgid = None
    if isinstance(data, list) and data:
        msgid = data[0]
    logger.info("[sms] asanak queued the message, msgid=%s", msgid)
    applog.info("sms", "sms.send.queued", "پیامک در صف ارسال قرار گرفت",
                provider="asanak", subcategory="freetext", outcome="queued",
                duration_ms=int((time.perf_counter() - _started) * 1000),
                metadata={"msgid": msgid,
                          "destination": applog.mask_phone(destination)})
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
