"""Exhibition lead capture: the visitor panel, the edit page, the admin queue.

Four audiences, four doors, and no shared credential between them:

  /v/{code}      a field visitor, identified by their own personal link
  /edit/{token}  a company contact, holding a one-time invite from the booth
  /login, /my    the same contact later, on an account and a session of their
                 own, because the invite was one-time and the company is not
  /secure-panel-inotex/leads  an administrator, on the existing admin session

Nothing here trusts anything the browser says about who it is. The visitor
cookie carries an opaque session token, and every request re-reads the session's
expiry, the visitor's `active` flag and their code's expiry, so revoking a
visitor takes effect on their next tap and a cookie kept forever buys nothing.
The invite token in the URL is the contact's whole credential: the row it hashes
to names the single company it may touch, so no company id is ever taken from a
request body, and the token stops existing the moment an edit is accepted.

A refusal carries `detail`, the sentence the person may read. The duplicate
phone warning carries one thing more, `"duplicate": true`, because a company
someone already owns is also a `409` and the two need different screens: one is
final, the other is a question the visitor answers by sending the form again.
"""
import html
import os

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.config import BASE_DIR, COOKIE_SECURE
from app.auth.security import check_rate_limit, client_ip, verify_admin
# One audit helper for every route in the app that hands a batch of data out,
# so there is a single event name to grep for. See app/routers/admin.py.
from app.routers.admin import audit_export
from app.services import identity as identity_service
from app.services import leads as leads_service
from app.services.leads import LeadError

router = APIRouter()


def _fail(e: LeadError):
    return HTTPException(status_code=e.status, detail=str(e))


def _page(name: str) -> str:
    with open(os.path.join(BASE_DIR, "templates", "leads", name), encoding="utf-8") as f:
        return f.read()


def _brand(page: str) -> str:
    """Inject the install's own name, colours and consent script.

    Same placeholder pattern the chat UI and the /verify page already use, so
    these pages need no template engine and stay copy-deployable.

    Every injected value is escaped. These are operator-editable settings, and
    a `</style><script>` pasted into a colour field would otherwise run in the
    contact's browser.
    """
    from app.db.queries import get_setting
    out = page.replace("<!-- APP_NAME -->",
                       html.escape(get_setting("whitelabel_app_name", "پادیار ویدیو چت")))
    out = out.replace(
        "<!-- BRAND_CSS -->",
        "<style>:root{"
        f"--brand-primary:{html.escape(get_setting('whitelabel_primary_color', '#2D5CA7'))};"
        f"--brand-accent:{html.escape(get_setting('whitelabel_accent_color', '#FCB715'))};"
        "}</style>",
    )
    return out.replace("<!-- CONSENT_SCRIPT -->",
                       html.escape(leads_service.consent_script()["text"]))


def _base_url(request: Request) -> str:
    """Where the contact's phone will be told to go.

    The public host is a setting first, because behind Cloudflare Tunnel the
    request's own URL can be the internal origin, and a QR pointing at
    127.0.0.1 is a QR that works only on the laptop that drew it.
    """
    from app.db.queries import get_setting
    configured = (get_setting("leads_public_base_url", "") or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def _dead_page(message: str, status: int) -> HTMLResponse:
    return HTMLResponse(
        _brand(_page("expired.html")).replace("<!-- REASON -->", html.escape(message)),
        status_code=status,
    )


# ── Visitor session ──────────────────────────────────────────────────────

def _session_token(request: Request) -> str:
    return request.cookies.get(leads_service.VISITOR_COOKIE, "")


def current_visitor(request: Request) -> dict:
    visitor = leads_service.visitor_by_session(_session_token(request))
    if visitor is None:
        raise HTTPException(status_code=401, detail="دسترسی ندارید.")
    return visitor


@router.get("/v/{code}")
async def visitor_link(code: str, request: Request):
    """The visitor's personal link. Exchanges the code for a session cookie.

    The code leaves the URL immediately: a redirect means it is not sitting in
    the phone's history, in a screenshot, or in a referrer header. What the
    cookie then carries is a session token, never the visitor's id.

    A dead code and an unknown code get the same page: which of the two it was
    is not the holder's business.
    """
    session = leads_service.start_session(code)
    if session is None:
        return HTMLResponse(_brand(_page("denied.html")), status_code=403)
    response = RedirectResponse(url="/v", status_code=303)
    response.set_cookie(
        key=leads_service.VISITOR_COOKIE, value=session["token"], httponly=True,
        secure=COOKIE_SECURE, samesite="lax",
        # The browser holds the pointer for as long as the code lives. How long
        # the SESSION lives is decided on the server, on every request.
        max_age=session["cookie_max_age"],
    )
    return response


@router.get("/v", response_class=HTMLResponse)
async def visitor_panel(request: Request):
    visitor = leads_service.visitor_by_session(_session_token(request))
    if visitor is None:
        return HTMLResponse(_brand(_page("denied.html")), status_code=403)
    page = _brand(_page("panel.html"))
    return HTMLResponse(page.replace("<!-- VISITOR_NAME -->",
                                     html.escape(visitor["name"] or "همکار")))


@router.get("/api/leads/companies")
async def companies(q: str = "", visitor: dict = Depends(current_visitor)):
    return {"companies": leads_service.search_companies(q)}


class RegisterBody(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    first_name: str = Field(default="", max_length=60)
    last_name: str = Field(default="", max_length=60)
    position: str = Field(default="", max_length=80)
    phone: str = Field(min_length=8, max_length=20)
    # Set only by the second attempt, after the visitor has read the duplicate
    # warning and decided to go on. It is written down on the lead.
    override_duplicate: bool = False


@router.post("/api/leads/register")
async def register(body: RegisterBody, request: Request,
                   visitor: dict = Depends(current_visitor)):
    # Keyed on the visitor, not the address: a whole exhibition hall shares one
    # NAT'd IP and two visitors must not be able to lock each other out. The
    # ceiling is the lead module's own, so raising it never touches /chat.
    check_rate_limit(request, key=f"visitor:{visitor['id']}",
                     limit=leads_service.RATE_LIMIT_PER_VISITOR)
    try:
        return leads_service.register_contact(
            visitor["id"], body.dataset_id, body.first_name, body.last_name,
            body.position, body.phone, override_duplicate=body.override_duplicate,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except LeadError as e:
        if e.code == "duplicate_phone":
            # The extra key is the whole point: an owned company is a 409 too,
            # and only this one can be answered by sending the form again.
            return JSONResponse(status_code=e.status,
                                content={"detail": str(e), "duplicate": True})
        raise _fail(e)
    except Exception as e:  # OTP delivery refusals reach the visitor verbatim
        raise HTTPException(status_code=getattr(e, "status", 400), detail=str(e))


class NewCompanyBody(BaseModel):
    """The normal registration, plus the four fields of the dataset editor.

    Only `title` and `phone` are required. The three content fields may be
    blank, because the contact is about to be handed a link and write the
    answer themselves.
    """
    title: str = Field(min_length=1, max_length=120)
    title_en: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=4000)
    text_en: str = Field(default="", max_length=4000)
    first_name: str = Field(default="", max_length=60)
    last_name: str = Field(default="", max_length=60)
    position: str = Field(default="", max_length=80)
    phone: str = Field(min_length=8, max_length=20)
    override_duplicate: bool = False


@router.post("/api/leads/new-company")
async def new_company(body: NewCompanyBody, request: Request,
                      visitor: dict = Depends(current_visitor)):
    """Create the company AND register its contact, or create nothing.

    The only route on the feature that takes a company name as text. What keeps
    it from being a way around choosing from the list is the refusal inside:
    a name the knowledge base already holds is sent back to the search box.
    """
    check_rate_limit(request, key=f"visitor:{visitor['id']}",
                     limit=leads_service.RATE_LIMIT_PER_VISITOR)
    try:
        return leads_service.register_new_company(
            visitor["id"], body.title, body.first_name, body.last_name,
            body.position, body.phone, text=body.text, title_en=body.title_en,
            text_en=body.text_en, override_duplicate=body.override_duplicate,
            ip=client_ip(request), user_agent=request.headers.get("user-agent", ""),
        )
    except LeadError as e:
        # `code` rides along so the panel can act instead of only complaining:
        # a duplicate number is answered by sending the form again, a name that
        # is already taken by going back to the search box. `duplicate` keeps
        # the shape /api/leads/register already returns.
        return JSONResponse(status_code=e.status,
                            content={"detail": str(e), "code": e.code,
                                     "duplicate": e.code == "duplicate_phone"})
    except Exception as e:  # OTP delivery refusals reach the visitor verbatim
        raise HTTPException(status_code=getattr(e, "status", 400), detail=str(e))


class VerifyBody(BaseModel):
    lead_id: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=12)


@router.post("/api/leads/verify")
async def verify(body: VerifyBody, request: Request,
                 visitor: dict = Depends(current_visitor)):
    check_rate_limit(request, key=f"visitor:{visitor['id']}",
                     limit=leads_service.RATE_LIMIT_PER_VISITOR)
    try:
        # The visitor's session goes in so the invite remembers who minted it.
        # What comes back is a QR image or a delivery report, never the token:
        # the person who captured the lead may not edit the company's answer.
        return leads_service.verify_contact(
            body.lead_id, body.code, _base_url(request),
            visitor_session=_session_token(request),
        )
    except LeadError as e:
        raise _fail(e)


@router.get("/api/leads/mine")
async def mine(visitor: dict = Depends(current_visitor)):
    return {"leads": leads_service.list_leads(visitor["id"])}


# ── The company contact ──────────────────────────────────────────────────

@router.get("/edit/{token}", response_class=HTMLResponse)
async def edit_page(token: str, request: Request):
    """Open the edit page. This does NOT burn the invite.

    Rate limited and audited with the caller's address: this is the one route
    on the whole feature that an unauthenticated stranger can hammer, and a
    guessing run has to be visible in the log with an IP beside it. The IP
    ceiling is the generous one, because the contact has no cookie to key on
    and half the phones in the hall leave from the same address.
    """
    check_rate_limit(request, limit=leads_service.RATE_LIMIT_PER_IP)
    try:
        leads_service.invite_view(token, ip=client_ip(request))
    except LeadError as e:
        return _dead_page(str(e), e.status)
    return HTMLResponse(_brand(_page("edit.html")))


@router.get("/api/leads/edit/{token}")
async def edit_state(token: str, request: Request):
    check_rate_limit(request, limit=leads_service.RATE_LIMIT_PER_IP)
    try:
        view = leads_service.invite_view(token, ip=client_ip(request))
    except LeadError as e:
        raise _fail(e)
    # `متن پاسخ`, plus the company name as a read-only heading so the contact
    # knows whose text this is. `id`, `video_url` and the English columns are
    # not in this response, because they are not part of this conversation.
    return {"company": view["company"], "text": view["text"],
            "pending": view["pending"], "expires_at": view["expires_at"],
            "consent_script": leads_service.consent_script()["text"]}


def _only_text(payload: dict) -> str:
    """Exactly one field, `text`. Anything else is a `400`.

    Ignoring an unexpected field is not the same as refusing it: silence is how
    `dataset_id` or `status` gets wired through by someone who assumed the
    endpoint was already checking.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="درخواست معتبر نیست.")
    extra = sorted(k for k in payload if k != "text")
    if extra:
        raise HTTPException(status_code=400,
                            detail="فقط متن پاسخ قابل ارسال است: " + "، ".join(extra))
    value = payload.get("text")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="متن پاسخ را وارد کنید.")
    return value


@router.post("/api/leads/edit/{token}")
async def save_edit(token: str, request: Request, payload: dict = Body(default={})):
    check_rate_limit(request, limit=leads_service.RATE_LIMIT_PER_IP)
    text = _only_text(payload)
    try:
        return leads_service.submit_edit(
            token, text,
            visitor_session=_session_token(request),
            ip=client_ip(request),
        )
    except LeadError as e:
        raise _fail(e)


# ── The contact, signed in ───────────────────────────────────────────────
#
# A fourth door, and the only one that outlives its invite:
#
#   /login  a company contact, proving a phone number they already gave once
#   /my     the companies that phone number is allowed to speak for
#
# Two rules run through every route below. The credential is a session cookie
# whose expiry the server re-reads on each request, never a challenge id and
# never anything the browser asserts. And the company being edited is derived
# from the ownership record every single time, so nothing in a request body or
# a query string can point this at a company the caller does not own
# (SEC-004, SEC-005).

def _user_token(request: Request) -> str:
    return request.cookies.get(identity_service.USER_COOKIE, "")


def current_user(request: Request) -> dict:
    """403, not 401. Both are a login to the page, and SEC-001 says 403."""
    user = identity_service.user_by_session(_user_token(request))
    if user is None:
        raise HTTPException(status_code=403, detail="وارد نشده‌اید.")
    return user


class LoginRequestBody(BaseModel):
    phone: str = Field(min_length=8, max_length=20)


@router.post("/api/auth/login/request")
async def login_request(body: LoginRequestBody, request: Request):
    """Send a code. No password exists to be created, forgotten or reset.

    The answer is the same whether or not this number has an account: a page
    that says "no account with that number" is a page that lists which of the
    exhibition's contacts we hold. The account is created on the way back, when
    the code has actually been read.
    """
    check_rate_limit(request, limit=leads_service.RATE_LIMIT_PER_IP)
    from app.services import otp as otp_service
    if otp_service.normalize_destination(body.phone) is None:
        return JSONResponse(status_code=400,
                            content={"detail": "شماره واردشده معتبر نیست.",
                                     "code": "bad_phone"})
    try:
        # Straight through the OTP service, so this path is under the same
        # per-number hourly ceiling, cooldown and attempt limit as every other
        # code this install sends (REQ-036).
        return otp_service.request_challenge(body.phone)
    except otp_service.OtpError as e:
        code = {429: "hourly", 503: "sms_down"}.get(e.status, "error")
        return JSONResponse(status_code=e.status,
                            content={"detail": e.public, "code": code})


class LoginVerifyBody(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=12)


@router.post("/api/auth/login/verify")
async def login_verify(body: LoginVerifyBody, request: Request):
    """Check the code, find or create the account, hand out a session."""
    check_rate_limit(request, limit=leads_service.RATE_LIMIT_PER_IP)
    from app.services import otp as otp_service
    ok, message = otp_service.verify(body.challenge_id, body.code)
    if not ok:
        return JSONResponse(status_code=400,
                            content={"detail": message, "code": "bad_code"})
    destination = identity_service.verified_destination(body.challenge_id)
    if not destination:
        return JSONResponse(status_code=400,
                            content={"detail": "کد منقضی شده است. دوباره درخواست دهید.",
                                     "code": "expired"})

    user = identity_service.find_or_create_user(destination, source="login")
    if user["status"] != "active":
        # A blocked account cannot sign back in. Blocking deletes the sessions;
        # this is what stops a new one being minted a second later.
        return JSONResponse(status_code=403,
                            content={"detail": "این حساب غیرفعال است.",
                                     "code": "blocked"})
    session = identity_service.start_session(user["id"])
    response = JSONResponse({"ok": True, "redirect": "/my"})
    response.set_cookie(
        key=identity_service.USER_COOKIE, value=session["token"], httponly=True,
        secure=COOKIE_SECURE, samesite="lax", max_age=session["max_age"],
    )
    return response


@router.post("/api/auth/logout")
async def logout(request: Request):
    """One button, and the cookie stops working on the server too."""
    identity_service.end_session(_user_token(request))
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=identity_service.USER_COOKIE)
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if identity_service.user_by_session(_user_token(request)):
        return RedirectResponse(url="/my", status_code=303)
    return HTMLResponse(_brand(_page("login.html")))


@router.get("/my", response_class=HTMLResponse)
async def my_page(request: Request):
    """No session is not an error to explain, it is a login."""
    if identity_service.user_by_session(_user_token(request)) is None:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(_brand(_page("my.html")))


@router.get("/api/my/companies")
async def my_companies(user: dict = Depends(current_user)):
    """Only this account's live ownerships, and there may be several.

    `pending_grants` are the companies a booth attached to an account that
    already existed. They open nothing until this person accepts one from this
    session, which is the whole of SEC-013.
    """
    return {"companies": leads_service.owner_companies(user["id"]),
            "pending_grants": [{"id": g["id"], "title": g["title"]}
                               for g in identity_service.pending_grants(user["id"])],
            "user": {"first_name": user["first_name"], "last_name": user["last_name"]}}


@router.post("/api/my/companies/{ownership_id}/accept")
async def my_accept(ownership_id: str, user: dict = Depends(current_user)):
    if not identity_service.accept_grant(user["id"], ownership_id):
        raise HTTPException(status_code=403, detail=leads_service.NO_ACCESS_MESSAGE)
    return {"ok": True}


# Two shapes for one route. The bare path is what the spec named and is enough
# for the ordinary case of one company; the path parameter names WHICH
# ownership when a person runs several. Neither carries a company id: an
# ownership id belongs to exactly one account, and the company is read off the
# record it names.
@router.get("/api/my/edit")
@router.get("/api/my/edit/{ownership_id}")
async def my_edit_state(ownership_id: str = "", user: dict = Depends(current_user)):
    try:
        return leads_service.owner_edit_view(user["id"], ownership_id)
    except LeadError as e:
        raise _fail(e)


def _owner_payload(payload: dict) -> str:
    """`text`, and nothing else. A `dataset_id` in the body is a 400.

    Ignoring an unexpected field is not the same as refusing it: silence is how
    somebody later assumes the endpoint was already reading it.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="درخواست معتبر نیست.")
    extra = sorted(k for k in payload if k != "text")
    if extra:
        raise HTTPException(status_code=400,
                            detail="فقط متن پاسخ قابل ارسال است: " + "، ".join(extra))
    value = payload.get("text")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="متن پاسخ را وارد کنید.")
    return value


@router.post("/api/my/edit")
@router.post("/api/my/edit/{ownership_id}")
async def my_edit_save(request: Request, ownership_id: str = "",
                       payload: dict = Body(default={}),
                       user: dict = Depends(current_user)):
    check_rate_limit(request, key=f"user:{user['id']}",
                     limit=leads_service.RATE_LIMIT_PER_VISITOR)
    text = _owner_payload(payload)
    try:
        return leads_service.submit_owner_edit(user["id"], ownership_id, text)
    except LeadError as e:
        raise _fail(e)


# ── Admin ────────────────────────────────────────────────────────────────

@router.get("/admin/api/leads/funnel")
async def admin_funnel(request: Request, admin: str = Depends(verify_admin)):
    """Counts, not rows. It still writes the export line, because the counts
    are the map: they say how many companies are worth coming back for."""
    audit_export(request, admin, "lead_funnel")
    return leads_service.funnel()


@router.get("/admin/api/leads")
async def admin_leads(request: Request, visitor_id: str = "",
                      admin: str = Depends(verify_admin)):
    # `with_signals` carries `ip`, `user_agent` and the two cluster flags. Only
    # this route asks for them: the visitor's own list must not tell a visitor
    # which of their patterns an operator is looking at.
    rows = leads_service.list_leads(visitor_id, with_signals=True)
    # The contact list of the whole exhibition: every company captured, who
    # captured it, and from which address and device. A browser tab is an
    # export as surely as a CSV is, so it leaves the same row behind.
    audit_export(request, admin, "company_leads", rows=len(rows),
                 visitor_id=visitor_id or "all")
    return {"leads": rows,
            "fast_capture_seconds": leads_service.MIN_SECONDS_BETWEEN_CAPTURES}


@router.get("/admin/api/leads/stuck")
async def admin_stuck(request: Request, admin: str = Depends(verify_admin)):
    rows = leads_service.stuck_leads()
    audit_export(request, admin, "stuck_leads", rows=len(rows))
    return {"stuck": rows}


@router.post("/admin/api/leads/{lead_id}/release")
async def admin_release(lead_id: str, request: Request,
                        admin: str = Depends(verify_admin)):
    try:
        return leads_service.release_lead(lead_id, actor=admin, ip=client_ip(request))
    except LeadError as e:
        raise _fail(e)


@router.get("/admin/api/leads/settings", dependencies=[Depends(verify_admin)])
async def admin_settings():
    consent = leads_service.consent_script()
    sms = leads_service.sms_capability()
    # `sms_available` is answered here, on the screen where the channel is
    # chosen. Finding out from a log that nothing was delivered is finding out
    # too late.
    return {"invite_channel": leads_service.invite_channel(),
            "channels": list(leads_service.INVITE_CHANNELS),
            "consent_script": consent["text"], "consent_version": consent["version"],
            "sms_available": sms["available"], "sms_reason": sms["reason"],
            # The domains a proposed answer may link to without the review card
            # nudging the reviewer. Editable here so permitting the
            # exhibition's own site is a setting, not a deploy.
            "allowed_link_domains": ", ".join(leads_service.allowed_link_domains())}


class SettingsBody(BaseModel):
    invite_channel: str = Field(default="", max_length=10)
    consent_script: str = Field(default="", max_length=4000)
    # `None` means "left alone". An empty string is a real value here: it is
    # how an operator clears the list back to allowing nothing.
    allowed_link_domains: str | None = Field(default=None, max_length=2000)


@router.post("/admin/api/leads/settings", dependencies=[Depends(verify_admin)])
async def admin_save_settings(body: SettingsBody):
    try:
        if body.invite_channel:
            leads_service.set_invite_channel(body.invite_channel)
        if body.consent_script.strip():
            leads_service.set_consent_script(body.consent_script)
        if body.allowed_link_domains is not None:
            leads_service.set_allowed_link_domains(body.allowed_link_domains)
    except LeadError as e:
        raise _fail(e)
    return await admin_settings()


@router.get("/admin/api/leads/visitors")
async def admin_visitors(request: Request, admin: str = Depends(verify_admin)):
    """The visitor roster. There is no code in it to leave out any more.

    A personal link is shown once, when it is created or rotated, and the
    database keeps only its HMAC. `needs_link` is what this screen says
    instead: a code that has run out, or a row that never had one, is handed a
    new link rather than shown an old one.
    """
    rows = leads_service.list_visitors()
    audit_export(request, admin, "lead_visitors", rows=len(rows))
    return {"visitors": rows}


class VisitorBody(BaseModel):
    name: str = Field(default="", max_length=80)


@router.post("/admin/api/leads/visitors", dependencies=[Depends(verify_admin)])
async def admin_create_visitor(body: VisitorBody, request: Request):
    """The one response that carries a raw code, together with the rotate
    action below. Nothing can show it again."""
    visitor = leads_service.create_visitor(body.name)
    link = f"{_base_url(request)}/v/{visitor['code']}"
    return {"id": visitor["id"], "name": visitor["name"], "link": link,
            "expires_at": visitor["expires_at"], "qr": leads_service.qr_svg(link)}


class VisitorActiveBody(BaseModel):
    active: bool


@router.post("/admin/api/leads/visitors/{visitor_id}/active",
             dependencies=[Depends(verify_admin)])
async def admin_visitor_active(visitor_id: str, body: VisitorActiveBody):
    if not leads_service.set_visitor_active(visitor_id, body.active):
        raise HTTPException(status_code=404, detail="این همکار پیدا نشد.")
    return {"ok": True}


@router.post("/admin/api/leads/visitors/{visitor_id}/rotate",
             dependencies=[Depends(verify_admin)])
async def admin_rotate_visitor(visitor_id: str, request: Request):
    """A new link for a lost phone. The old one and every session opened with
    it stop working immediately."""
    rotated = leads_service.rotate_visitor_code(visitor_id)
    if rotated is None:
        raise HTTPException(status_code=404, detail="این همکار پیدا نشد.")
    link = f"{_base_url(request)}/v/{rotated['code']}"
    return {"link": link, "expires_at": rotated["expires_at"],
            "qr": leads_service.qr_svg(link)}


@router.get("/admin/api/leads/edits")
async def admin_edits(request: Request, status: str = "pending",
                      admin: str = Depends(verify_admin)):
    rows = leads_service.list_edits(status)
    # Every row carries the contact's name, role and masked number alongside
    # the text they wrote. `risky` rides with it: the tokens a reviewer has to
    # read before approving, extracted here because only the server knows the
    # allowed-domain list.
    audit_export(request, admin, "dataset_edits", rows=len(rows), status=status)
    return {"edits": rows}


class ReviewBody(BaseModel):
    approve: bool


@router.post("/admin/api/leads/edits/{edit_id}")
async def admin_review_edit(edit_id: str, body: ReviewBody, request: Request,
                            admin: str = Depends(verify_admin)):
    try:
        # The reviewer is the admin USERNAME. This used to store a slice of the
        # session cookie, which named nobody and put a piece of a live
        # credential in a table the panel prints.
        return leads_service.review_edit(edit_id, body.approve, reviewer=admin,
                                         base_url=_base_url(request))
    except LeadError as e:
        raise _fail(e)


@router.post("/admin/api/leads/edits/{edit_id}/revert")
async def admin_revert_edit(edit_id: str, admin: str = Depends(verify_admin)):
    try:
        return leads_service.revert_edit(edit_id, actor=admin)
    except LeadError as e:
        raise _fail(e)


# ── Admin: accounts and ownership ────────────────────────────────────────

@router.get("/admin/api/leads/users")
async def admin_users(request: Request, admin: str = Depends(verify_admin)):
    rows = identity_service.list_users()
    audit_export(request, admin, "lead_users", rows=len(rows))
    return {"users": rows}


class BlockBody(BaseModel):
    blocked: bool


@router.post("/admin/api/leads/users/{user_id}/block",
             dependencies=[Depends(verify_admin)])
async def admin_block_user(user_id: str, body: BlockBody):
    """Blocking deletes this account's sessions, so it takes effect on the next
    request rather than at the next expiry. Its history stays."""
    if not identity_service.block_user(user_id, body.blocked):
        raise HTTPException(status_code=404, detail="این کاربر پیدا نشد.")
    return {"ok": True}


class OwnerBody(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    user_id: str = Field(min_length=1, max_length=120)


@router.post("/admin/api/leads/owners")
async def admin_grant_owner(body: OwnerBody, admin: str = Depends(verify_admin)):
    """The one place a company id is taken from a request body, and it is the
    admin's own. SEC-004 is about the contact's edit path, where the id would
    be a way to reach a company the caller does not own; here the caller is the
    person who decides ownership in the first place.

    Granted `active`: an admin doing this IS the confirmation SEC-012 asks for.
    """
    grant = identity_service.grant_ownership(body.dataset_id, body.user_id,
                                             granted_by=admin, status="active")
    if not grant["ok"]:
        detail = ("این شرکت مالک دارد. اول مالکیت فعلی را لغو کنید."
                  if grant["reason"] == "taken"
                  else "این کاربر از قبل روی این شرکت مالکیت دارد.")
        raise HTTPException(status_code=409, detail=detail)
    return {"ok": True, "id": grant["id"]}


@router.post("/admin/api/leads/owners/{ownership_id}/revoke")
async def admin_revoke_owner(ownership_id: str, admin: str = Depends(verify_admin)):
    if not identity_service.revoke_grant(ownership_id, actor=admin):
        raise HTTPException(status_code=404, detail="این مالکیت پیدا نشد.")
    return {"ok": True}


@router.get("/secure-panel-inotex/leads", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Same session check and login redirect as every other admin page
    (see app/routers/public.py)."""
    from app.routers.public import _render, _require_admin

    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/leads.html", request=request, active_page="leads")
