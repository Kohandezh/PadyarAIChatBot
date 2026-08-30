"""OTP verification (SMS sign-up / sign-in step) — public API + /verify page.

PORTABLE MODULE. Nothing here is INOTEX-specific: the brand name, mark,
palette and companion avatar all come from settings (see `branding()` below),
so the same files drop into any Padyar-based installation — see
docs/engineering/OTP_MODULE.md and scripts/export-otp-module.py.

The page is an authentication step, not part of the chat skeleton: it is a
standalone route. It is not linked from the public chat navigation; flows that
need verification link to it.

WHO IS ASKING
-------------
Exactly one endpoint here mints an identity (`POST /api/auth/otp/verify`) and
exactly one thing carries it afterwards: the HttpOnly session cookie
`app/auth/visitor.py` owns. `challenge_id` is still a body field on verify and
resend, where it is correct — it is a server-minted, single-use capability and
no session exists yet — but it is no longer identity anywhere else. It used to
be: `/api/auth/profile` and `/api/visit-plan` took whatever challenge id the
body carried, which made a never-expiring bearer token out of a value that sat
in localStorage. Whoever held it could rewrite that person's profile and read
their name and masked number back.

Endpoints that consume or mint that cookie also run `validate_request_origin`,
because the moment a credential is ambient the browser will attach it to a POST
from any page the visitor happens to be on. `/request` and `/resend` are
deliberately left without it: they carry no ambient credential, so a forged
cross-site POST there gives an attacker nothing they could not do with curl,
and the per-destination hourly cap is the real bound on SMS spend.
"""
import json
import os
import shutil
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config import BASE_DIR, logger
from app.auth import security
from app.auth import visitor as visitor_auth
from app.auth.security import (check_rate_limits, client_ip,
                               validate_request_origin, verify_admin)
from app.services import conversations
from app.services import otp as otp_service

router = APIRouter()

# Per-install branding. Defaults are platform-neutral (PadYar); the install's
# own look comes from its admin settings rows, not from code. An install that
# never touches these sees the platform defaults.
_BRAND_DEFAULTS = {
    "otp_brand_name": "PadYar",
    "otp_brand_mark": (
        '<svg viewBox="0 0 64 64" width="30" height="30">'
        '<path d="M32 3 57 17.5v29L32 61 7 46.5v-29Z" fill="#1E2D52" stroke="#2D5CA7"'
        ' stroke-width="3" stroke-linejoin="round" />'
        '<path d="M32 17 45 24.5v15L32 47 19 39.5v-15Z" fill="#2D5CA7" />'
        '<path d="M32 17 45 24.5 32 32 19 24.5Z" fill="#5B8AD9" />'
        '<path d="M32 47 45 39.5v-6.2L32 40.8Z" fill="#FCB715" />'
        '</svg>'
    ),
    # Companion avatar: empty atlas URL = no companion at all, which is the
    # correct default for an install that has no character of its own.
    "otp_companion_atlas": "/static/otp/pet/inotex-pose-atlas-hd.webp",
    "otp_companion_cell": "512",
    # Palette — the CSS custom properties the stylesheet already consumes.
    "otp_color_primary": "#FCB715",
    "otp_color_primary_hover": "#FEBE27",
    "otp_color_blue": "#2D5CA7",
    "otp_color_navy": "#1E2D52",
    "otp_color_teal": "#04A584",
    "otp_color_background": "#000000",
    "otp_background_image": "/themes/inotex/static/bg-bricks.jpg",
}


def branding() -> dict:
    from app.db.queries import get_setting
    return {key: (get_setting(key, default) or default) for key, default in _BRAND_DEFAULTS.items()}


def _verified_registration(challenge_id: str) -> dict:
    """Everything a verified challenge holds, RAW phone included, or {}.

    `otp_service.profile_for()` cannot be used: it masks the number, which is
    the right rule for anything a browser sees and the wrong one here. The
    durable visitor row stores the real number because contacting these people
    after the exhibition is the point, and because the dedupe key is a hash of
    it — two spellings of one phone must not become two people.

    `used` is checked in the query, so an unverified or unknown challenge
    yields {} and nothing is ever promoted on the strength of a guessed id.
    """
    from app.db.connection import get_db_connection

    if not challenge_id:
        return {}
    try:
        otp_service.ensure_table()
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT first_name, last_name, destination, job, position,"
                " interests FROM otp_challenges WHERE id = ? AND used = TRUE",
                (challenge_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else {}
    except Exception as e:  # noqa: BLE001 — see _promote_to_visitor
        logger.error(f"[registration] challenge unreadable: {type(e).__name__}: {e}")
        return {}


def _promote_to_visitor(request: Request, challenge_id: str) -> str:
    """Turn a VERIFIED challenge into a durable visitor, and claim their chat.

    WHY THIS EXISTS. The registration answers lived on `otp_challenges`, a
    table keyed by a challenge and built to expire, plus a copy in the
    browser's localStorage. The exhibition's whole point is knowing who came
    and what they wanted, and both of those homes throw it away. This writes
    the person to `app.visitors`, which nothing expires.

    AND IT KEEPS THEIR CHAT. Somebody walks up, asks four questions, and only
    then registers. `padyar_conv` is the conversation those four questions are
    already in, so the new visitor is attached to it rather than starting a
    fresh one — the earlier messages stay exactly where they are and simply
    gain a name.

    Called from ONE place, the verify endpoint. It used to run on every profile
    save too, which meant a body-supplied challenge id could bind somebody
    else's visitor row to the attacker's conversation. Promotion happens once,
    where the code was actually proved.

    Never raises. A person who just proved their phone must be told they are
    registered, whatever the transcript store is doing.
    """
    record = _verified_registration(challenge_id)
    if not record:
        return ""
    conversation_id = (request.cookies.get("padyar_conv") or "")[:64]
    return conversations.register_visitor(conversation_id, {
        "first_name": record.get("first_name", ""),
        "last_name": record.get("last_name", ""),
        "phone": record.get("destination", ""),
        "job": record.get("job", ""),
        "position": record.get("position", ""),
        "interests": record.get("interests", ""),
    })


_BLANK_PROFILE = {"first_name": "", "last_name": "", "job": "", "position": "",
                  "interests": "", "destination_masked": ""}


def _visitor_profile(visitor_id: str) -> dict:
    """What the browser may see about a signed-in visitor. Phone MASKED.

    Same keys `otp_service.profile_for()` returns for a challenge, so the
    frontend reads one profile shape whether the visitor just verified or came
    back the next morning carrying nothing but a cookie.

    The raw number never leaves the server. `app.visitors` stores it because
    contacting these people after the exhibition is the point, not so that a
    page can print it — the masking rule is the same one profile_for() applies.
    """
    visitor_id = (visitor_id or "").strip()
    if not visitor_id:
        return dict(_BLANK_PROFILE)
    try:
        row = conversations.get_visitor(visitor_id)
    except Exception as e:  # noqa: BLE001 — a read fault is an empty profile
        logger.error("[registration] visitor unreadable: %s: %s", type(e).__name__, e)
        return dict(_BLANK_PROFILE)
    if not row:
        return dict(_BLANK_PROFILE)
    phone = row.get("phone") or ""
    return {
        "first_name": row.get("first_name") or "",
        "last_name": row.get("last_name") or "",
        "job": row.get("job") or "",
        "position": row.get("position") or "",
        "interests": row.get("interests") or "",
        "destination_masked": otp_service.mask_destination(phone) if phone else "",
    }


def _write_visitor_profile(visitor_id: str, job: str, position: str,
                           interests: str) -> bool:
    """Rewrite the three work fields of ONE visitor row. Returns False if none.

    BY ID AND NOTHING ELSE. The id comes from `require_visitor`, which reads it
    off the session the middleware resolved from the cookie, so the only row a
    request can ever write is its own. There is no phone, no challenge and no
    body field in this query to aim it somewhere else.

    Blanks are written, not skipped. `upsert_visitor` deliberately keeps an old
    value when a new registration leaves the field empty (a re-verification to
    fix a typo must not erase a name); this is the opposite case — a visitor
    clearing every interest is withdrawing consent and has to be obeyed.

    Name and phone are absent on purpose: the code proved those, and nothing
    reachable from a browser is allowed to change them.
    """
    from app.db.connection import get_db_connection
    try:
        conn = get_db_connection()
        try:
            # datetime('now') is INLINE, not a bound parameter: app/db/pg.py
            # rewrites it into the PostgreSQL form only when it can see the
            # literal. Same idiom as app/services/conversations.py.
            changed = conn.execute(
                "UPDATE visitors SET job = ?, position = ?, interests = ?,"
                " last_seen_at = datetime('now') WHERE id = ?",
                (job.strip()[:80], position.strip()[:80],
                 interests.strip()[:400], visitor_id)).rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return changed > 0
    except Exception as e:  # noqa: BLE001
        logger.error("[registration] profile write failed: %s: %s",
                     type(e).__name__, e)
        return False


def check_rate_limit(request: Request) -> None:
    """Two-tier limiter for this router's public endpoints.

    Tight bucket = the request's OTP identity at OTP_RATE_LIMIT; loose
    bucket = the per-IP backstop at OTP_IP_RATE_LIMIT. The identity is the
    canonicalized destination on /request, the challenge on /verify and
    /resend, and the SESSION'S visitor id once one exists. That last one is
    the point: keying a bucket on a body field let a caller mint a fresh, empty
    bucket per request just by varying the value, which left only the backstop
    counting. A visitor id is server-issued, so it cannot be rotated.

    Identity buckets exist so a booth's registration bursts do not
    collectively lock the hall out; the backstop exists so rotating identities
    cannot turn the endpoints into a free SMS relay. The service-level caps
    (attempts, resends, per-destination-hourly) remain the real bounds — these
    buckets just stop a booth lockout before the service is reached.

    Defined at module level under this router's historical name ON PURPOSE:
    the OTP test suites disable HTTP throttling by monkeypatching exactly this
    attribute (`otp_router.check_rate_limit = lambda request: None`) because
    dozens of requests from one client IP in seconds is their normal mode —
    the single-`request` call shape is part of that contract. The identity
    key arrives on request.state (each handler sets it from its already-parsed
    body; the raw stream is not re-readable from here). A request whose
    identity could not be canonicalized gets no tight bucket — only the
    backstop counts it.
    """
    identity = getattr(request.state, "otp_limit_identity", "")
    ip = client_ip(request) or "unknown"
    buckets = [(f"otpip:{ip}", security.OTP_IP_RATE_LIMIT)]
    if identity:
        buckets.insert(0, (identity, security.OTP_RATE_LIMIT))
    check_rate_limits(request, buckets)


class OtpRequestBody(BaseModel):
    destination: str = Field(..., min_length=5, max_length=32)
    first_name: str = Field("", max_length=60)
    last_name: str = Field("", max_length=60)
    job: str = Field("", max_length=80)
    position: str = Field("", max_length=80)
    interests: str = Field("", max_length=200)


class OtpVerifyBody(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=64)
    code: str = Field(..., min_length=1, max_length=16)


class OtpResendBody(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=64)


class VisitPlanBody(BaseModel):
    """The raw profile fields, or nothing at all. No identity field.

    `job`, `position` and `interests` are INPUTS to a recommendation, not
    claims about who is asking, so an anonymous visitor may still send them and
    get a useful plan — the planner has to work for somebody who walked up and
    never registered. What they can no longer do is name a stored profile: the
    `challenge_id` that used to sit here made the plan come back for whoever
    owned that id. A signed-in visitor's stored profile now wins, and it is
    read from the session cookie, never from this body.
    """
    job: str = Field("", max_length=80)
    position: str = Field("", max_length=80)
    interests: str = Field("", max_length=200)
    lang: str = Field("fa", max_length=8)


@router.post("/api/auth/otp/request")
async def otp_request(body: OtpRequestBody, request: Request):
    from app.services.maintenance import guard as _maintenance_guard
    _maintenance_guard()
    # Tight bucket on the CANONICALIZED destination (server-side
    # shape-validated) — never the raw body string, or "+98 912…" and
    # "0098912…" would mint separate buckets for one phone. A shape that
    # fails validation gets no tight bucket: the service is about to refuse
    # it anyway, and the per-IP backstop still counts the attempt.
    dest = otp_service.normalize_destination(body.destination)
    request.state.otp_limit_identity = f"otp:dest:{dest}" if dest else ""
    check_rate_limit(request)
    try:
        return otp_service.request_challenge(
            body.destination, body.first_name, body.last_name,
            body.job, body.position, body.interests,
        )
    except otp_service.OtpError as e:
        raise HTTPException(status_code=e.status, detail=e.public)


@router.post("/api/auth/otp/verify",
             dependencies=[Depends(validate_request_origin)])
async def otp_verify(body: OtpVerifyBody, request: Request, response: Response):
    """Check the code and, on success, HAND THIS BROWSER TO THE NEW VISITOR.

    This is the one place a browser is handed an identity. Origin is validated
    first because of login CSRF: an attacker knows their OWN challenge and
    code, and a forged POST from a page the visitor is looking at would drop
    the attacker's session cookie into the victim's browser. Everything the
    victim then said in the chat would be filed under the attacker's name.

    THE OLD SESSION DIES FIRST. This runs on an exhibition kiosk, so the
    browser normally arrives holding the PREVIOUS visitor's cookie. That
    session is revoked before a new one is minted. Person A is signed out of
    the screen person B is now standing at, and the kiosk stops collecting live
    session rows that nobody will ever come back for.

    THE RESPONSE ALWAYS WRITES THE COOKIE: set when there is a token, cleared
    when there is not. That is not tidiness, it is the fix for a real bug. The
    `resolve_visitor` middleware in app/main.py re-issues the cookie the
    REQUEST arrived with whenever the response did not write one itself, so a
    response that stayed silent handed person B person A's identity back, with
    a refreshed expiry. This docstring used to promise that a failed mint left
    the visitor "simply not signed in". It did not. It left them signed in as
    the last person who used the kiosk.

    A storage fault still never turns a good verification into an error. The
    visitor is told they verified and is anonymous, which the next verify
    fixes. Being wrongly signed OUT is recoverable; telling somebody who just
    proved their phone that it failed is not.
    """
    # Per-challenge bucket: challenge_id is a server-minted unguessable
    # capability, so one phone's retries cannot consume a neighbour's budget.
    # The service's own 5-attempts bound stays the real brute-force limit.
    request.state.otp_limit_identity = f"otp:chal:{body.challenge_id}"
    check_rate_limit(request)
    ok, message = otp_service.verify(body.challenge_id, body.code)
    if ok:
        # Whoever was signed in on this browser is signed out NOW, before the
        # new identity exists, so no path below can leave the old one alive.
        # revoke() ignores a token it does not know, so an absent or already
        # dead cookie costs one no-op.
        visitor_auth.revoke(
            request.cookies.get(visitor_auth.VISITOR_COOKIE_NAME, ""))
        # The code proved the phone, so this person is now known: write them
        # to the durable visitor table, hand them the conversation they have
        # been chatting in, and open a session for that visitor id.
        token = visitor_auth.mint(_promote_to_visitor(request, body.challenge_id))
        if token:
            visitor_auth.set_cookie(response, token)
        else:
            # Storage trouble: no session to give. DELETE the cookie rather
            # than say nothing. Saying nothing is what let the middleware put
            # the previous visitor's token back on this response, which is the
            # opposite of both what this endpoint means and what it promised.
            visitor_auth.clear_cookie(response)
        # The profile is returned only on success, and only the display name —
        # the phone number stays masked everywhere the browser can see it.
        profile = otp_service.profile_for(body.challenge_id)
        result = {"verified": True, "message": message, "profile": profile}
        # A cookie-less client (the pwa_api module's native/cross-origin
        # consumer) cannot pick the session token up from Set-Cookie, so it
        # asks for it in the body instead via this header. Cookie-based
        # clients never send it, so their response shape is byte-for-byte
        # unchanged. See docs/features/pwa-api/SPEC.md REQ-002.
        if request.headers.get("x-client", "").strip().lower() == "pwa":
            result["access_token"] = token
        return result
    # Generic public error — the reason detail stays in the audit log.
    raise HTTPException(status_code=400, detail=message)


@router.post("/api/auth/otp/resend")
async def otp_resend(body: OtpResendBody, request: Request):
    # Same per-challenge bucket as verify; the service's 45s cooldown and
    # max-3 resends remain the real caps on top.
    request.state.otp_limit_identity = f"otp:chal:{body.challenge_id}"
    check_rate_limit(request)
    try:
        return otp_service.resend(body.challenge_id)
    except otp_service.OtpError as e:
        raise HTTPException(status_code=e.status, detail=e.public)


class ProfileUpdateBody(BaseModel):
    """The three editable fields, and nothing that says who is editing.

    There is no `challenge_id` here any more, and no visitor id either. The
    body of a request cannot name the row it writes: identity comes from the
    session cookie, through `require_visitor`.

    All three are required: the 3 onboarding questions (job, position,
    interests) are mandatory now, not just optional plan input.
    """
    job: str = Field(..., min_length=1, max_length=80)
    position: str = Field(..., min_length=1, max_length=80)
    interests: str = Field(..., min_length=1, max_length=400)


@router.get("/api/registration/options")
async def registration_options(lang: str = "fa"):
    """The job list, interest list and checkboxes the form renders.

    Served from the taxonomy file, never hardcoded in the form, so replacing
    that file changes what visitors can pick without touching the frontend.
    """
    from app.services import taxonomy
    return taxonomy.form_options("en" if lang.lower().startswith("en") else "fa")


@router.post("/api/auth/profile",
             dependencies=[Depends(validate_request_origin)])
async def update_profile(body: ProfileUpdateBody, request: Request,
                         visitor_id: str = Depends(visitor_auth.require_visitor)):
    """Let a signed-in visitor correct their work profile and re-plan.

    A visitor may only ever write their OWN row, and cannot say which row that
    is. `require_visitor` returns the id the middleware resolved from the
    session cookie; anonymous gets a 401 carrying the registration_required
    marker, which is what opens the signup card in the browser.

    Only the descriptive fields move. Name and phone are what the code proved
    and are not editable from a browser at all.
    """
    # Bucket on the SESSION's visitor id. The old key was built from a body
    # field, so varying it handed the caller a fresh empty bucket every request
    # and only the per-IP backstop ever counted them.
    request.state.otp_limit_identity = f"otp:visitor:{visitor_id}"
    check_rate_limit(request)
    if not _write_visitor_profile(visitor_id, body.job, body.position,
                                  body.interests):
        # The session resolved but its person is gone (a deleted visitor row,
        # or a storage fault). Nothing was written, so say so rather than
        # reporting a save that did not happen.
        raise HTTPException(status_code=403, detail="این نشست معتبر نیست.")
    return {"updated": True, "profile": _visitor_profile(visitor_id)}


@router.post("/api/visit-plan",
             dependencies=[Depends(validate_request_origin)])
async def visit_plan_endpoint(body: VisitPlanBody, request: Request):
    """Which official INOTEX sections match this visitor's work and interests.

    Open to anonymous callers on purpose — see VisitPlanBody. When a session
    exists the STORED profile wins over the body, exactly as the challenge id
    used to make it win: the server already knows what this person typed at
    registration, and no browser can edit that copy.
    """
    visitor_id = getattr(request.state, "visitor_id", "") or ""
    # Identity bucket only when there is a session: the planner is useful
    # without one, and an anonymous call is limited by IP alone.
    request.state.otp_limit_identity = (
        f"otp:visitor:{visitor_id}" if visitor_id else "")
    check_rate_limit(request)
    from app.services import visit_plan as planner

    profile = {"job": body.job, "position": body.position, "interests": body.interests}
    if visitor_id:
        stored = _visitor_profile(visitor_id)
        if any(stored.get(k) for k in ("job", "position", "interests")):
            profile = {k: stored.get(k, "") for k in ("job", "position", "interests")}

    lang = "en" if body.lang.lower().startswith("en") else "fa"
    return planner.recommend(profile, lang)


@router.get("/api/auth/session")
async def visitor_session(request: Request):
    """Am I signed in, and as whom. Read from the COOKIE only.

    This is what replaces localStorage as the answer to that question. The
    browser used to decide for itself by reading a key it had written, which
    meant typing one value into devtools made it "signed in" and the server
    never disagreed. Now the browser asks and the server answers from the
    session it resolved.

    The phone stays masked, matching every other profile the browser sees.

    no-store because an exhibition kiosk is shared: a back button that redraws
    the previous visitor's name from cache would be a privacy leak with no
    request behind it.
    """
    visitor_id = getattr(request.state, "visitor_id", "") or ""
    return JSONResponse(
        {"signed_in": bool(visitor_id),
         "profile": _visitor_profile(visitor_id) if visitor_id else {}},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/auth/logout",
             dependencies=[Depends(validate_request_origin)])
async def visitor_logout(request: Request, response: Response):
    """End this browser's session: delete the row, then delete the cookie.

    Safe to call when already anonymous — revoke() ignores a token that never
    existed, and clearing an absent cookie is a no-op. A sign-out must never
    fail, or a shared kiosk keeps the last visitor signed in.

    Deleting the ROW is what makes this real. A signed token could only be
    asked to expire; a row stops resolving the same second it is gone.
    """
    visitor_auth.revoke(request.cookies.get(visitor_auth.VISITOR_COOKIE_NAME, ""))
    visitor_auth.clear_cookie(response)
    return {"signed_in": False}


@router.get("/api/auth/registration-status")
async def registration_status():
    """Whether the Smart Visit entry point should be offered at all.

    Public and deliberately thin: it reveals only the on/off switch and the
    configured code length — never the provider account or whether the
    gateway credentials exist.
    """
    from app.db.queries import get_setting
    return {
        "enabled": get_setting("registration_enabled", "false") == "true",
        "otp_length": otp_service.OTP_LENGTH,
    }


@router.get("/api/auth/otp/status/{challenge_id}")
async def otp_status(challenge_id: str):
    """Timer reconciliation after a page refresh (unguessable id)."""
    try:
        return otp_service.get_status(challenge_id)
    except otp_service.OtpError as e:
        raise HTTPException(status_code=e.status, detail=e.public)


@router.get("/verify", response_class=HTMLResponse)
async def otp_page():
    """Serve the verification page with this install's branding injected.

    Uses the project's existing placeholder-replacement pattern for raw HTML
    pages (same approach as the public chat UI) rather than a template engine,
    so the module stays copy-deployable into installs that render their public
    pages differently.
    """
    path = os.path.join(BASE_DIR, "templates", "otp", "verify.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    b = branding()
    # html.escape on everything injected into HTML/CSS/JS: the branding values
    # are admin-editable settings, and an unescaped quote or angle bracket
    # becomes stored XSS against every visitor on this page. The leads module
    # already escapes its injections; this matches it. `attr` mode for the
    # CSS/JS contexts so both quote styles are covered.
    import html as _html
    esc = lambda v: _html.escape(str(v), quote=True)
    companion = b["otp_companion_atlas"].strip()
    brand_css = (
        "<style>:root{"
        f"--inotex-primary:{esc(b['otp_color_primary'])};"
        f"--inotex-yellow-light:{esc(b['otp_color_primary_hover'])};"
        f"--inotex-blue:{esc(b['otp_color_blue'])};"
        f"--inotex-navy:{esc(b['otp_color_navy'])};"
        f"--inotex-teal:{esc(b['otp_color_teal'])};"
        f"--inotex-background:{esc(b['otp_color_background'])};"
        "}"
        f"body{{background-image:url('{esc(b['otp_background_image'])}');}}"
        + ("" if companion else ".pet-slot{display:none;}")
        + "</style>"
    )
    config = (
        "<script>window.OTP_CONFIG="
        f'{{"companionAtlas":"{esc(companion)}","companionCell":{b["otp_companion_cell"]}}};'
        "</script>"
    )

    html = html.replace("<!-- BRAND_CSS -->", brand_css)
    html = html.replace("<!-- BRAND_CONFIG -->", config)
    html = html.replace("<!-- BRAND_MARK -->", esc(b["otp_brand_mark"]))
    html = html.replace("<!-- BRAND_NAME -->", esc(b["otp_brand_name"]))
    return HTMLResponse(html)


# ── Admin: editing the taxonomy ──────────────────────────────────────────
# `data/visit-taxonomy.json` decides what the registration form offers and
# what the visit planner may recommend. This module owns that file, so the
# admin screen that edits it lives here, behind the same admin session as
# every other /admin/api endpoint.

# Persian names for the lists and fields, so a refusal reads like a sentence
# a receptionist can act on rather than a schema error.
_LIST_LABELS = {
    "jobs": "شغل‌ها",
    "positions": "سمت‌ها",
    "interests": "علاقه‌مندی‌ها",
    "flags": "گزینه‌های تیک‌دار",
    "sections": "بخش‌های نمایشگاه",
}
_FIELD_LABELS = {"id": "شناسه", "fa": "عنوان فارسی", "en": "عنوان انگلیسی"}


def _rows_the_loader_would_drop(doc: dict) -> list:
    """Persian reasons for rows the loader would silently skip.

    The loader is deliberately forgiving: a row with an empty label is dropped
    and the rest of the list still ships. That is right for a file drop, but
    wrong for a person: an admin who typed a job title and pressed save must
    not be told "saved" while the row was thrown away. So the save refuses and
    names the row instead.
    """
    problems = []
    for key, label in _LIST_LABELS.items():
        rows = doc.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list):
            problems.append(f"«{label}» باید یک فهرست باشد.")
            continue
        required = ("id", "fa", "en") if key == "sections" else ("id", "fa")
        seen = set()
        for number, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                problems.append(f"«{label}» ردیف {number}: ساختار درستی ندارد.")
                continue
            for field in required:
                if not str(row.get(field, "")).strip():
                    problems.append(
                        f"«{label}» ردیف {number}: {_FIELD_LABELS[field]} خالی است."
                    )
            row_id = str(row.get("id", "")).strip()
            if row_id and row_id in seen:
                problems.append(f"«{label}» ردیف {number}: شناسهٔ «{row_id}» تکراری است.")
            seen.add(row_id)
            keywords = row.get("keywords")
            if key == "sections" and not (isinstance(keywords, list) and keywords):
                problems.append(f"«{label}» ردیف {number}: کلیدواژه‌ها خالی است.")
    return problems


class TaxonomySaveBody(BaseModel):
    # The whole file as text — the friendly editor and the raw editor both
    # send this, so there is exactly one save path and one validation path.
    text: str = Field(..., min_length=2, max_length=500_000)


@router.get("/admin/api/taxonomy", dependencies=[Depends(verify_admin)])
async def get_taxonomy():
    """The file as it is on disk, plus what the running app is actually using."""
    from app.services import taxonomy

    path = taxonomy.TAXONOMY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""

    data, parse_error = None, ""
    if text.strip():
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else None
            if data is None:
                parse_error = "ساختار فایل باید یک شیء JSON باشد."
        except ValueError as e:
            parse_error = str(e)

    live = taxonomy.document()
    return {
        "text": text,
        "data": data,
        "parse_error": parse_error,
        "file_name": os.path.basename(path),
        # What visitors see right now. Differs from the file only when the file
        # is broken and the loader is still serving the last good version.
        "live_version": live["version"],
        "live_counts": {k: len(live.get(k, [])) for k in
                        ("jobs", "positions", "interests", "flags", "sections")},
        "using_fallback": live["version"] == "builtin-minimum",
    }


@router.post("/admin/api/taxonomy", dependencies=[Depends(verify_admin)])
async def save_taxonomy(body: TaxonomySaveBody):
    """Validate first, back up, then replace the file in one atomic step.

    Nothing is written unless the loader would accept the result, so a save can
    never be the reason registration falls back to the built-in minimum.
    """
    from app.services import taxonomy

    try:
        parsed = json.loads(body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"متن واردشده JSON معتبر نیست: {e}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="ساختار فایل باید یک شیء JSON باشد.")

    problems = _rows_the_loader_would_drop(parsed)
    if problems:
        shown = problems[:5]
        if len(problems) > 5:
            shown.append(f"و {len(problems) - 5} مورد دیگر.")
        raise HTTPException(status_code=400, detail="ذخیره نشد — " + " ".join(shown))

    # The product's own validator has the final say: if it would refuse this
    # document, the file must not change.
    if taxonomy._validate(parsed) is None:
        raise HTTPException(
            status_code=400,
            detail="ذخیره نشد — فایل حداقل به یک «بخش نمایشگاه» سالم "
                   "(با شناسه، عنوان فارسی، عنوان انگلیسی و کلیدواژه) نیاز دارد.",
        )

    path = taxonomy.TAXONOMY_PATH
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)

    # Backup before overwrite — same timestamped-sibling pattern as
    # scripts/reset-content-to-defaults.py, so the previous file is one
    # rename away if an edit turns out to be wrong.
    backup_name = ""
    root, ext = os.path.splitext(path)
    if os.path.exists(path):
        backup = f"{root}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        shutil.copy2(path, backup)
        backup_name = os.path.basename(backup)

    # Re-serialise rather than writing the request body verbatim. The friendly
    # editor posts compact JSON, which would collapse the whole taxonomy onto
    # one line — unreadable to hand-edit and useless in a diff. Pretty-printing
    # the already-validated document also gives the raw editor a canonical
    # format, so two admins editing the same file produce comparable output.
    text = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".visit-taxonomy-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Atomic on POSIX: a crash leaves either the old file or the new one,
        # never a truncated taxonomy.
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    # Keep the 10 most recent backups and drop the rest. An operator tuning the
    # form during an event saves many times a day, and nothing else ever cleans
    # data/ up. The timestamp in the name sorts oldest-first as plain text, so
    # sorting by name is sorting by age.
    #
    # Housekeeping must never cost us the save: the new taxonomy is already on
    # disk and live, so a file we cannot delete is logged and forgotten.
    try:
        prefix = os.path.basename(root) + ".backup."
        old = sorted(n for n in os.listdir(folder)
                     if n.startswith(prefix) and n.endswith(ext))
        for name in old[:-10]:
            os.remove(os.path.join(folder, name))
    except Exception as e:
        logger.warning("[taxonomy] could not prune old backups: %s", e)

    # The loader watches the mtime, so the new file is live on the next read.
    live = taxonomy.document()
    logger.info(
        "[taxonomy] saved from admin panel — v%s, %d sections (backup: %s)",
        live["version"], len(live["sections"]), backup_name or "none",
    )
    return {
        "status": "saved",
        "backup": backup_name,
        "live_version": live["version"],
        "live_counts": {k: len(live.get(k, [])) for k in
                        ("jobs", "positions", "interests", "flags", "sections")},
    }


@router.get("/secure-panel-inotex/settings/taxonomy", response_class=HTMLResponse)
async def admin_taxonomy_page(request: Request):
    """The admin screen. Same session check and login redirect as the other
    admin pages (see app/routers/public.py)."""
    from app.routers.public import _render, _require_admin

    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render(
        "admin/settings_taxonomy.html", request=request, active_page="settings_taxonomy"
    )
