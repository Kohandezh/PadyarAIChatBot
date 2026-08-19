"""CSRF protection for admin mutations.

DESIGN
------
The token is `HMAC-SHA256(session_token, app secret)`, hex-encoded. That gives
three properties without any new storage:

  * cryptographically secure — an attacker cannot compute it without the
    server secret;
  * bound to ONE session — a token minted for one admin is invalid for
    another, and it dies with the session;
  * stateless — no table, no expiry sweep, no cross-worker coordination.

WHY NOT JUST SameSite
---------------------
The cookie is already `SameSite=Lax`, which stops cross-site POSTs in modern
browsers. It is not sufficient on its own: Lax still permits top-level GET
navigations, older browsers ignore it, and a same-site subdomain or an
attacker-controlled page served from the same origin is unaffected. Defence in
depth is the point — the token is checked server-side on every mutation.

TRANSPORT
---------
Header `X-CSRF-Token`, because every admin mutation goes through `fetchAuth()`
in static/admin/js/utils.js — one choke point that attaches it automatically.
A form field `csrf_token` is also accepted for any non-JS form.

WHAT IS EXEMPT, AND WHY
-----------------------
`POST /admin/login` — there is no session yet, so there is no token to bind
to; it is protected by credentials plus the brute-force lockout instead.
Everything else that mutates state is protected.
"""
import hmac
import hashlib

from fastapi import HTTPException, Request

from app.config import ADMIN_COOKIE_NAME

HEADER = "X-CSRF-Token"
FORM_FIELD = "csrf_token"

# The login endpoint cannot carry a session-bound token by definition.
EXEMPT_PATHS = frozenset({"/admin/login"})
PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _secret() -> bytes:
    from app.auth.security import _get_hmac_key
    return _get_hmac_key().encode()


def token_for_session(session_token: str) -> str:
    if not session_token:
        return ""
    return hmac.new(_secret(), session_token.encode(), hashlib.sha256).hexdigest()


def token_for_request(request: Request) -> str:
    return token_for_session(request.cookies.get(ADMIN_COOKIE_NAME, ""))


async def enforce(request: Request) -> None:
    """Reject a state-changing admin request without a valid token.

    Raises 403 — deliberately distinct from the 401 an unauthenticated request
    gets, so an operator debugging a failure can tell "not logged in" from
    "token missing or wrong".
    """
    if request.method not in PROTECTED_METHODS:
        return
    if request.url.path in EXEMPT_PATHS:
        return

    session = request.cookies.get(ADMIN_COOKIE_NAME, "")
    if not session:
        # No session at all: authentication will reject it anyway.
        return

    expected = token_for_session(session)
    supplied = request.headers.get(HEADER, "")
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if "form" in content_type:
            try:
                form = await request.form()
                supplied = str(form.get(FORM_FIELD, ""))
            except Exception:  # noqa: BLE001 — unreadable body is not a token
                supplied = ""

    # compare_digest: a timing-safe comparison, since the value is a MAC.
    if not supplied or not hmac.compare_digest(supplied, expected):
        from app.services import applog
        applog.security("security.csrf.rejected",
                        "درخواست بدون توکن CSRF معتبر رد شد",
                        ip=request.client.host if request.client else "",
                        target=request.url.path, outcome="denied",
                        http_method=request.method,
                        user_agent=request.headers.get("user-agent", ""))
        raise HTTPException(
            status_code=403,
            detail="توکن امنیتی نامعتبر است. صفحه را تازه کنید و دوباره تلاش کنید.")
