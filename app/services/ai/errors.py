"""Normalized AI failures.

Every provider failure becomes one of the classes below before it leaves the
provider layer. Application code never sees an `openai.APIError`, an
`anthropic.APIStatusError`, or a raw `httpx` exception.

WHY THE ROUTING FLAGS LIVE HERE
-------------------------------
Whether to retry and whether to fail over are properties OF THE FAILURE, so
they are recorded here once rather than re-derived by string-matching at each
decision point. The predecessor (`app/services/openai.py:_llm_error_code`)
inferred them by scanning `str(exc)` for needles like `"429"` and `"api key"`.
That is why it could misfire: any request id containing `401` read as an auth
failure. Adapters now classify from HTTP status plus the parsed provider body,
and hand back one of these.

THE TWO FLAGS ARE NOT THE SAME QUESTION
---------------------------------------
`retryable`         — is it worth asking THIS provider again?
`failover_eligible` — is it worth asking a DIFFERENT provider?

They come apart in both directions, which is exactly why one boolean would be
wrong:

  * `authentication_failed` — retrying the same provider with the same broken
    key is pointless, but another provider will happily answer. Not retryable,
    IS failover-eligible.
  * `context_limit_exceeded` — the next provider will reject the same oversized
    prompt. Neither.
  * `rate_limited` — both. Back off here, and meanwhile ask someone else.

THE ONES THAT MUST NOT FAIL OVER
--------------------------------
`invalid_request`, `structured_output_failed` and `content_rejected` are our
bug or our content, not the provider's health. Cycling providers on them would
turn one visible error into nine invisible ones and burn quota doing it. The
phase brief calls this out and it is enforced here in data, not in prose.

`content_rejected` deserves its own note: Gemini signals a safety block with
**HTTP 200 and a well-formed body containing no text**. It is a successful
call whose content was refused. Every other provider would refuse the same
content, so failing over is both useless and expensive.

NAME COMPATIBILITY
------------------
These names deliberately overlap the codes the old `_llm_error_code` already
wrote into `observability.app_logs.error_code`: `rate_limited`,
`quota_exceeded`, `timeout`, `context_window_exceeded`, `model_not_found`,
`connection_failed`. Operators have filters and saved queries against those
strings. Where a name had to change, `LEGACY_ALIASES` records the mapping so
old log rows stay interpretable.
"""
from dataclasses import dataclass, field

# ── The taxonomy ────────────────────────────────────────────────────────
# name -> (retryable, failover_eligible, operator-facing Persian summary)

AUTHENTICATION_FAILED = "authentication_failed"
PERMISSION_DENIED = "permission_denied"
RATE_LIMITED = "rate_limited"
QUOTA_EXCEEDED = "quota_exceeded"
TIMEOUT = "timeout"
CONNECTION_FAILED = "connection_failed"
PROVIDER_UNAVAILABLE = "provider_unavailable"
SERVER_ERROR = "server_error"
MODEL_NOT_FOUND = "model_not_found"
MODEL_UNAVAILABLE = "model_unavailable"
CONTEXT_LIMIT_EXCEEDED = "context_limit_exceeded"
INVALID_REQUEST = "invalid_request"
INVALID_RESPONSE = "invalid_response"
STRUCTURED_OUTPUT_FAILED = "structured_output_failed"
CONTENT_REJECTED = "content_rejected"
ALL_ROUTES_FAILED = "all_routes_failed"
UNKNOWN = "unknown"

_SPEC = {
    #                          retryable  failover  Persian
    AUTHENTICATION_FAILED:    (False,     True,     "اعتبارنامهٔ سرویس‌دهنده پذیرفته نشد."),
    PERMISSION_DENIED:        (False,     True,     "این حساب اجازهٔ دسترسی به این مدل را ندارد."),
    RATE_LIMITED:             (True,      True,     "سرویس‌دهنده محدودیت نرخ اعمال کرد."),
    QUOTA_EXCEEDED:           (False,     True,     "اعتبار یا سهمیهٔ حساب تمام شده است."),
    TIMEOUT:                  (True,      True,     "سرویس‌دهنده در زمان مجاز پاسخ نداد."),
    CONNECTION_FAILED:        (True,      True,     "اتصال به سرویس‌دهنده برقرار نشد."),
    PROVIDER_UNAVAILABLE:     (True,      True,     "سرویس‌دهنده موقتاً در دسترس نیست."),
    SERVER_ERROR:             (True,      True,     "خطای داخلی سرویس‌دهنده."),
    MODEL_NOT_FOUND:          (False,     True,     "این مدل روی این سرویس‌دهنده وجود ندارد."),
    MODEL_UNAVAILABLE:        (True,      True,     "این مدل موقتاً در دسترس نیست."),
    CONTEXT_LIMIT_EXCEEDED:   (False,     False,    "طول درخواست از ظرفیت مدل بیشتر است."),
    INVALID_REQUEST:          (False,     False,    "درخواست نامعتبر بود — این ایراد از سمت ماست."),
    INVALID_RESPONSE:         (True,      True,     "پاسخ سرویس‌دهنده قابل خواندن نبود."),
    STRUCTURED_OUTPUT_FAILED: (False,     False,    "مدل خروجی ساخت‌یافتهٔ معتبر تولید نکرد."),
    CONTENT_REJECTED:         (False,     False,    "محتوا توسط فیلتر ایمنی سرویس‌دهنده رد شد."),
    ALL_ROUTES_FAILED:        (False,     False,    "هیچ سرویس‌دهندهٔ فعالی پاسخ نداد."),
    UNKNOWN:                  (False,     True,     "خطای ناشناخته از سرویس‌دهنده."),
}

CLASSES = tuple(_SPEC)

# Old code -> new code, so log rows written before this module stay readable.
LEGACY_ALIASES = {
    "invalid_api_key": AUTHENTICATION_FAILED,
    "forbidden": PERMISSION_DENIED,
    "context_window_exceeded": CONTEXT_LIMIT_EXCEEDED,
    "provider_internal_error": SERVER_ERROR,
    "malformed_response": INVALID_RESPONSE,
}


def _scrub(text: str) -> str:
    """Strip credential shapes out of provider text.

    Imported lazily and defensively: this runs inside `AIError.__post_init__`,
    so it executes on every failure path including startup and error handling.
    If it could raise, constructing an error would raise — turning a handled
    provider failure into an unhandled crash. On any problem it fails CLOSED,
    returning a placeholder rather than the raw text: losing a diagnostic
    string is recoverable, leaking a live API key is not.
    """
    if not text:
        return ""
    try:
        from app.services import applog
        return applog.scrub_text(text)
    except Exception:  # noqa: BLE001
        return "[unscrubbable provider detail withheld]"


def canonical(code: str) -> str:
    """Map any historical or current code onto a current one."""
    code = (code or "").strip() or UNKNOWN
    code = LEGACY_ALIASES.get(code, code)
    return code if code in _SPEC else UNKNOWN


def is_retryable(code: str) -> bool:
    return _SPEC[canonical(code)][0]


def is_failover_eligible(code: str) -> bool:
    return _SPEC[canonical(code)][1]


def message_fa(code: str) -> str:
    return _SPEC[canonical(code)][2]


@dataclass
class AIError(Exception):
    """A normalized provider failure.

    Raised out of the wrapper so `app/routers/chat.py` can keep catching a
    single exception type, as it does today, without learning any vendor
    vocabulary.

    `provider_detail` holds the provider's own words for diagnosis. It is
    REDACTED before it is stored or displayed — providers have been known to
    echo the Authorization header back inside an error message, and that must
    never reach a log table or an admin page.
    """

    code: str = UNKNOWN
    provider_type: str = ""
    provider_instance_id: str = ""
    model: str = ""
    status_code: int = 0
    provider_detail: str = ""
    provider_request_id: str = ""
    attempts: int = 0
    correlation_id: str = ""
    request_id: str = ""
    # Populated on ALL_ROUTES_FAILED: what each attempted target did.
    route_failures: list = field(default_factory=list)

    def __post_init__(self):
        self.code = canonical(self.code)
        # Scrub HERE, not only in redacted_detail(). This string becomes
        # `str(exc)` and the traceback line, which reach places that never
        # call the redacting accessor: an uncaught 500 page, a `logger.error`
        # with %s, a pytest failure dump, a crash reporter. Providers have
        # been observed echoing the Authorization header back inside an error
        # body, so the unscrubbed form is a live credential one stack trace
        # away from disclosure.
        Exception.__init__(self, f"{self.code}: {_scrub(self.provider_detail)[:200]}")

    @property
    def retryable(self) -> bool:
        return is_retryable(self.code)

    @property
    def failover_eligible(self) -> bool:
        return is_failover_eligible(self.code)

    @property
    def message_fa(self) -> str:
        return message_fa(self.code)

    def redacted_detail(self) -> str:
        """Provider text with anything credential-shaped stripped out."""
        from app.services import applog
        return applog.scrub_text(self.provider_detail or "")[:400]

    def as_log_fields(self) -> dict:
        """The safe subset for `applog`. Never includes raw provider text."""
        return {
            "error_code": self.code,
            "provider": self.provider_type,
            "model": self.model,
            "metadata": {
                "provider_instance_id": self.provider_instance_id,
                "status_code": self.status_code,
                "provider_request_id": self.provider_request_id[:80],
                "attempts": self.attempts,
                "retryable": self.retryable,
                "failover_eligible": self.failover_eligible,
                "provider_error": self.redacted_detail(),
            },
        }


def from_status(status: int, detail: str = "", **kw) -> AIError:
    """Fallback classification from an HTTP status alone.

    Adapters SHOULD classify from the parsed provider body first — a 429 can
    mean "slow down" (retry here) or "you are out of credit" (do not), and only
    the body distinguishes them. This exists for the cases where the body is
    absent or unparseable, and for transports that never got a body at all.
    """
    if status in (401,):
        code = AUTHENTICATION_FAILED
    elif status in (403,):
        code = PERMISSION_DENIED
    elif status in (404,):
        code = MODEL_NOT_FOUND
    elif status in (408, 504):
        code = TIMEOUT
    elif status in (413,):
        code = CONTEXT_LIMIT_EXCEEDED
    elif status in (429,):
        code = RATE_LIMITED
    elif status in (400, 422):
        code = INVALID_REQUEST
    elif status in (402,):
        code = QUOTA_EXCEEDED
    # 529 is Anthropic's `overloaded_error`; it has no OpenAI equivalent and is
    # squarely a "try again shortly" condition.
    elif status in (500, 502, 503, 529) or status >= 500:
        code = PROVIDER_UNAVAILABLE if status in (503, 529) else SERVER_ERROR
    else:
        code = UNKNOWN
    return AIError(code=code, status_code=status, provider_detail=detail, **kw)
