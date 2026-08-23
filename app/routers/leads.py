"""Exhibition lead capture: the visitor panel, the edit page, the admin queue.

Three audiences, three doors, and no shared credential between them:

  /v/{code}      a field visitor, identified by their own personal link
  /edit/{token}  a company contact, holding a one-time invite from the booth
  /secure-panel-inotex/leads  an administrator, on the existing admin session

Nothing here trusts anything the browser says about who it is. The visitor
cookie carries only an id, and `active` is re-read on every request, so
revoking a visitor takes effect on their next tap rather than at cookie expiry.
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

def current_visitor(request: Request) -> dict:
    visitor_id = request.cookies.get(leads_service.VISITOR_COOKIE, "")
    visitor = leads_service.visitor_by_id(visitor_id) if visitor_id else None
    if visitor is None:
        raise HTTPException(status_code=401, detail="دسترسی ندارید.")
    return visitor


@router.get("/v/{code}")
async def visitor_link(code: str, request: Request):
    """The visitor's personal link. Exchanges the code for a session cookie.

    The code leaves the URL immediately: a redirect means it is not sitting in
    the phone's history, in a screenshot, or in a referrer header.
    """
    visitor = leads_service.visitor_by_code(code)
    if visitor is None:
        return HTMLResponse(_brand(_page("denied.html")), status_code=403)
    response = RedirectResponse(url="/v", status_code=303)
    response.set_cookie(
        key=leads_service.VISITOR_COOKIE, value=visitor["id"], httponly=True,
        secure=COOKIE_SECURE, samesite="lax",
        max_age=leads_service.VISITOR_SESSION_TTL_SECONDS,
    )
    return response


@router.get("/v", response_class=HTMLResponse)
async def visitor_panel(request: Request):
    visitor_id = request.cookies.get(leads_service.VISITOR_COOKIE, "")
    visitor = leads_service.visitor_by_id(visitor_id) if visitor_id else None
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
    # NAT'd IP and two visitors must not be able to lock each other out.
    check_rate_limit(request, key=f"visitor:{visitor['id']}")
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


class VerifyBody(BaseModel):
    lead_id: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=12)


@router.post("/api/leads/verify")
async def verify(body: VerifyBody, request: Request,
                 visitor: dict = Depends(current_visitor)):
    check_rate_limit(request, key=f"visitor:{visitor['id']}")
    try:
        # The visitor's session goes in so the invite remembers who minted it.
        # What comes back is a QR image or a delivery report, never the token:
        # the person who captured the lead may not edit the company's answer.
        return leads_service.verify_contact(
            body.lead_id, body.code, _base_url(request),
            visitor_session=request.cookies.get(leads_service.VISITOR_COOKIE, ""),
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
    guessing run has to be visible in the log with an IP beside it.
    """
    check_rate_limit(request)
    try:
        leads_service.invite_view(token, ip=client_ip(request))
    except LeadError as e:
        return _dead_page(str(e), e.status)
    return HTMLResponse(_brand(_page("edit.html")))


@router.get("/api/leads/edit/{token}")
async def edit_state(token: str, request: Request):
    check_rate_limit(request)
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
    check_rate_limit(request)
    text = _only_text(payload)
    try:
        return leads_service.submit_edit(
            token, text,
            visitor_session=request.cookies.get(leads_service.VISITOR_COOKIE, ""),
            ip=client_ip(request),
        )
    except LeadError as e:
        raise _fail(e)


# ── Admin ────────────────────────────────────────────────────────────────

@router.get("/admin/api/leads/funnel", dependencies=[Depends(verify_admin)])
async def admin_funnel():
    return leads_service.funnel()


@router.get("/admin/api/leads", dependencies=[Depends(verify_admin)])
async def admin_leads(visitor_id: str = ""):
    return {"leads": leads_service.list_leads(visitor_id)}


@router.get("/admin/api/leads/stuck", dependencies=[Depends(verify_admin)])
async def admin_stuck():
    return {"stuck": leads_service.stuck_leads()}


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
            "sms_available": sms["available"], "sms_reason": sms["reason"]}


class SettingsBody(BaseModel):
    invite_channel: str = Field(default="", max_length=10)
    consent_script: str = Field(default="", max_length=4000)


@router.post("/admin/api/leads/settings", dependencies=[Depends(verify_admin)])
async def admin_save_settings(body: SettingsBody):
    try:
        if body.invite_channel:
            leads_service.set_invite_channel(body.invite_channel)
        if body.consent_script.strip():
            leads_service.set_consent_script(body.consent_script)
    except LeadError as e:
        raise _fail(e)
    return await admin_settings()


@router.get("/admin/api/leads/visitors", dependencies=[Depends(verify_admin)])
async def admin_visitors():
    """The visitor roster WITHOUT the codes.

    A personal link is shown once, when it is created or rotated. A list that
    re-displays every live code turns one look at this screen, or one backup,
    into every visitor's identity.
    """
    rows = leads_service.list_visitors()
    for r in rows:
        r.pop("code", None)
    return {"visitors": rows}


class VisitorBody(BaseModel):
    name: str = Field(default="", max_length=80)


@router.post("/admin/api/leads/visitors", dependencies=[Depends(verify_admin)])
async def admin_create_visitor(body: VisitorBody, request: Request):
    visitor = leads_service.create_visitor(body.name)
    link = f"{_base_url(request)}/v/{visitor['code']}"
    return {"id": visitor["id"], "name": visitor["name"], "link": link,
            "qr": leads_service.qr_svg(link)}


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
    """A new link for a lost phone. The old one stops working immediately."""
    code = leads_service.rotate_visitor_code(visitor_id)
    if code is None:
        raise HTTPException(status_code=404, detail="این همکار پیدا نشد.")
    link = f"{_base_url(request)}/v/{code}"
    return {"link": link, "qr": leads_service.qr_svg(link)}


@router.get("/admin/api/leads/edits", dependencies=[Depends(verify_admin)])
async def admin_edits(status: str = "pending"):
    return {"edits": leads_service.list_edits(status)}


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


@router.get("/secure-panel-inotex/leads", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Same session check and login redirect as every other admin page
    (see app/routers/public.py)."""
    from app.routers.public import _render, _require_admin

    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/leads.html", request=request, active_page="leads")
