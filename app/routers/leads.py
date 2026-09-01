"""Exhibition lead capture: the visitor panel, the edit page, the admin queue.

Three audiences, three doors, and no shared credential between them:

  /v/{code}      a field visitor, identified by their own personal link
  /edit/{token}  a company contact, holding a one-time invite from the booth
  /secure-panel-inotex/leads  an administrator, on the existing admin session

Nothing here trusts anything the browser says about who it is. The visitor
cookie carries the visitor's CODE, the same secret their personal link is
made of, and both `active` and the code itself are re-read on every request.
So revoking a visitor, and rotating their link, each take effect on the next
tap rather than at cookie expiry. It used to carry `lead_visitors.id`, the
row's primary key, which never changes: a lost phone survived a rotation for
the rest of its 12 hour session. A row id the client hands back is not a
credential.

That change costs one thing, once. The day it ships, every staff member with
a /v panel already open is signed out, because their cookie holds an id and
nothing answers to an id any more. They open their own personal link again
and carry on. Tell the operator before the deploy, not after: mid-exhibition
this looks like the panel breaking for the whole booth at the same minute.

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

from fastapi import (APIRouter, BackgroundTasks, Body, Depends, HTTPException,
                     Request)
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

    Values come from the shared branding service (one source of truth for
    the 5 whitelabel_* keys) but the escaping stays HERE: this is raw HTML
    string-replacement, not a Jinja env, so every value is escaped at the
    injection point — a `</style><script>` pasted into a colour field would
    otherwise run in the contact's browser.
    """
    from app.services.branding import get_branding
    b = get_branding()
    out = page.replace("<!-- APP_NAME -->",
                       html.escape(b["whitelabel_app_name"]))
    out = out.replace(
        "<!-- BRAND_CSS -->",
        "<style>:root{"
        f"--brand-primary:{html.escape(b['whitelabel_primary_color'])};"
        f"--brand-accent:{html.escape(b['whitelabel_accent_color'])};"
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
    """Who is holding this phone, re-read from the database every request.

    The cookie is the visitor's code, never their row id. Rotating the code
    is then the same thing as ending every live session on that visitor, with
    no session table and no extra column: the old cookie names a code that no
    longer exists in `lead_visitors`.
    """
    code = request.cookies.get(leads_service.VISITOR_COOKIE, "")
    visitor = leads_service.visitor_by_code(code) if code else None
    if visitor is None:
        raise HTTPException(status_code=401, detail="دسترسی ندارید.")
    return visitor


@router.get("/v/{code}")
async def visitor_link(code: str, request: Request):
    """The visitor's personal link. Exchanges the code for a session cookie.

    The code leaves the URL immediately: a redirect means it is not sitting in
    the phone's history, in a screenshot, or in a referrer header. It moves
    into an HttpOnly cookie, so it is still out of reach of page scripts.
    """
    visitor = leads_service.visitor_by_code(code)
    if visitor is None:
        return HTMLResponse(_brand(_page("denied.html")), status_code=403)
    response = RedirectResponse(url="/v", status_code=303)
    response.set_cookie(
        # The code, not visitor["id"]. The id never changes, so a cookie
        # holding it outlived "give this staff member a new link" by up to
        # 12 hours, on the very phone the rotation was answering.
        key=leads_service.VISITOR_COOKIE, value=code, httponly=True,
        secure=COOKIE_SECURE, samesite="lax",
        max_age=leads_service.VISITOR_SESSION_TTL_SECONDS,
    )
    return response


@router.get("/v", response_class=HTMLResponse)
async def visitor_panel(request: Request):
    code = request.cookies.get(leads_service.VISITOR_COOKIE, "")
    visitor = leads_service.visitor_by_code(code) if code else None
    if visitor is None:
        return HTMLResponse(_brand(_page("denied.html")), status_code=403)
    page = _brand(_page("panel.html"))
    return HTMLResponse(page.replace("<!-- VISITOR_NAME -->",
                                     html.escape(visitor["name"] or "همکار")))


@router.get("/api/leads/companies")
async def companies(q: str = "", visitor: dict = Depends(current_visitor)):
    return {"companies": leads_service.search_companies(q)}


class ProposeCompanyBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)


@router.post("/api/leads/companies")
async def propose_company(body: ProposeCompanyBody, request: Request,
                          visitor: dict = Depends(current_visitor)):
    """The visitor's booth is not in the list — add it.

    Same rate-limit key as register(): keyed on the visitor, not the address,
    so one exhibition hall sharing a NAT'd IP cannot lock each other out.
    """
    check_rate_limit(request, key=f"visitor:{visitor['id']}")
    try:
        return leads_service.propose_company(visitor["id"], body.title, body.text)
    except LeadError as e:
        raise _fail(e)


class RegisterBody(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    position: str = Field(min_length=1, max_length=80)
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
        # The visitor's ID goes in so the invite remembers who minted it.
        # What comes back is a QR image or a delivery report, never the token:
        # the person who captured the lead may not edit the company's answer.
        #
        # The id and not the cookie, even though the cookie identifies the
        # same person. The cookie now holds the visitor's live code, and this
        # value is written to edit_invites.issued_by_session in the clear;
        # a table read, an export or a backup would hand out a working /v link.
        return leads_service.verify_contact(
            body.lead_id, body.code, _base_url(request),
            visitor_session=visitor["id"],
        )
    except LeadError as e:
        raise _fail(e)


@router.get("/api/leads/mine")
async def mine(visitor: dict = Depends(current_visitor)):
    return {"leads": leads_service.list_leads(visitor["id"])}


class NoteBody(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2000)
    warmth: str = Field(default="medium", max_length=10)
    contact_name: str = Field(default="", max_length=80)
    contact_position: str = Field(default="", max_length=80)
    contact_phone: str = Field(default="", max_length=24)


@router.post("/api/leads/notes")
async def create_note(body: NoteBody, request: Request,
                      visitor: dict = Depends(current_visitor)):
    """One visit note — what the agent observed, without the OTP pipeline.

    Same rate-limit key as register(): the visitor, not the NAT'd hall
    address. See app/services/leads.py:create_note for why the optional
    contact block is note-grade and never a lead."""
    check_rate_limit(request, key=f"visitor:{visitor['id']}")
    try:
        return leads_service.create_note(
            visitor["id"], body.dataset_id, body.note, warmth=body.warmth,
            contact_name=body.contact_name,
            contact_position=body.contact_position,
            contact_phone=body.contact_phone,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except LeadError as e:
        raise _fail(e)


# ── The company contact ──────────────────────────────────────────────────

@router.get("/edit/{token}", response_class=HTMLResponse)
async def edit_page(token: str, request: Request):
    """The link's landing page — a gate, not the form.

    GET never burns anything: messengers (Telegram, WhatsApp) prefetch URLs
    server-side before the human taps, and a link that died on GET was a link
    the contact never had. So this serves a start screen whose only button
    POSTs; the burn and the data both arrive with that press (see begin).

    A browser already holding a live edit-session cookie (the same contact,
    refreshing) is taken straight past the gate to the form. Rate limited and
    audited with the caller's address: this is the one route on the whole
    feature an unauthenticated stranger can hammer, and a guessing run has to
    be visible in the log with an IP beside it.
    """
    check_rate_limit(request)
    secret = request.cookies.get(leads_service.EDIT_SESSION_COOKIE, "")
    if secret:
        try:
            leads_service.session_view(secret, ip=client_ip(request))
            return HTMLResponse(_brand(_page("edit.html")))
        except LeadError:
            pass  # submitted or expired: fall through and judge the token
    if not leads_service.invite_alive(token, ip=client_ip(request)):
        return _dead_page(leads_service.DEAD_INVITE_MESSAGE, 410)
    return HTMLResponse(_brand(_page("begin.html")))


@router.post("/api/leads/edit/{token}/begin")
async def begin_edit(token: str, request: Request, payload: dict = Body(default={})):
    """The button press that spends the one-time link.

    The burn happens here and only here. A browser carrying a /v visitor
    cookie is a booth phone: refused WITHOUT burning, so neither the person
    who captured the lead nor a phone whose link was rotated can spend the
    company's one opening. The same params and the same refusal rule the old
    submit-time guard used, one step earlier.
    """
    check_rate_limit(request)
    visitor_code = request.cookies.get(leads_service.VISITOR_COOKIE, "")
    try:
        opened = leads_service.open_invite(
            token,
            visitor_session=(leads_service.visitor_id_for_session(visitor_code)
                             if visitor_code else ""),
            from_booth_phone=bool(visitor_code),
            ip=client_ip(request),
        )
    except LeadError as e:
        raise _fail(e)
    response = JSONResponse({
        "ok": True, "company": opened["company"],
        "expires_at": opened["expires_at"], "expires_in": opened["expires_in"],
    })
    response.set_cookie(
        key=leads_service.EDIT_SESSION_COOKIE, value=opened["session_secret"],
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
        max_age=leads_service.EDIT_SESSION_TTL_SECONDS,
    )
    return response


@router.get("/api/leads/edit/state")
async def edit_state(request: Request):
    """What the open page shows, held by the session cookie — never by the
    URL, which died with the invite. `fields` is the whole editable profile,
    `context` the read-only booth/hall block. `id`, `video_url` and the
    English columns are not in this response, because they are not part of
    this conversation."""
    check_rate_limit(request)
    secret = request.cookies.get(leads_service.EDIT_SESSION_COOKIE, "")
    try:
        view = leads_service.session_view(secret, ip=client_ip(request))
    except LeadError as e:
        raise _fail(e)
    return {"company": view["company"], "fields": view["fields"],
            "context": view["context"], "text": view["text"],
            "pending": view["pending"], "expires_at": view["expires_at"],
            "consent_script": leads_service.consent_script()["text"]}


def _edit_payload(payload: dict):
    """Either {"confirm": true} or {"fields": {editable fields}} — nothing else.

    Ignoring an unexpected field is not the same as refusing it: silence is
    how `dataset_id` or `status` gets wired through by someone who assumed the
    endpoint was already checking. Returns (confirm, fields).
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="درخواست معتبر نیست.")
    extra = sorted(k for k in payload if k not in ("confirm", "fields"))
    if extra:
        raise HTTPException(status_code=400,
                            detail="این درخواست فیلدشناخته ندارد: " + "، ".join(extra))
    confirm = payload.get("confirm", False)
    fields = payload.get("fields")
    if confirm not in (True, False):
        raise HTTPException(status_code=400, detail="درخواست معتبر نیست.")
    if confirm:
        return True, None
    if not isinstance(fields, dict):
        raise HTTPException(status_code=400, detail="اطلاعات فرم ارسال نشده است.")
    unknown = sorted(k for k in fields if k not in leads_service.EDITABLE_FIELDS)
    if unknown:
        raise HTTPException(status_code=400,
                            detail="این موارد قابل ارسال نیست: " + "، ".join(unknown))
    if not all(isinstance(v, str) for v in fields.values()):
        raise HTTPException(status_code=400, detail="مقدار واردشده معتبر نیست.")
    return False, fields


@router.post("/api/leads/edit/submit")
async def save_edit(request: Request, payload: dict = Body(default={})):
    """Submit through the open page's session cookie. The link in the URL bar
    is already dead; this is the only door left, and it closes with the
    submit."""
    check_rate_limit(request)
    confirm, fields = _edit_payload(payload)
    secret = request.cookies.get(leads_service.EDIT_SESSION_COOKIE, "")
    try:
        return leads_service.submit_edit_session(
            secret, fields or {}, ip=client_ip(request), confirm=confirm)
    except LeadError as e:
        raise _fail(e)


# ── Admin ────────────────────────────────────────────────────────────────

class ContactBody(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    first_name: str = Field(default="", max_length=60)
    last_name: str = Field(default="", max_length=60)
    position: str = Field(default="", max_length=80)
    phone: str = Field(min_length=8, max_length=20)
    override_duplicate: bool = False


@router.get("/admin/api/leads/companies", dependencies=[Depends(verify_admin)])
async def admin_companies(q: str = ""):
    """Company search for the admin's contact form. Same list the booth sees:
    companies someone already owns are not offered twice."""
    return {"companies": leads_service.search_companies(q)}


@router.post("/admin/api/leads/contacts")
async def admin_add_contact(body: ContactBody, request: Request,
                            admin: str = Depends(verify_admin)):
    try:
        return leads_service.admin_add_contact(
            body.dataset_id, body.first_name, body.last_name, body.position,
            body.phone, base_url=_base_url(request),
            override_duplicate=body.override_duplicate,
            actor=admin, ip=client_ip(request),
        )
    except LeadError as e:
        if e.code == "duplicate_phone":
            return JSONResponse(status_code=e.status,
                                content={"detail": str(e), "duplicate": True})
        raise _fail(e)


@router.post("/admin/api/leads/contacts/{dataset_id}/reissue-invite")
async def admin_reissue_invite(dataset_id: str, request: Request,
                               admin: str = Depends(verify_admin)):
    """A fresh one-time link for a company that already owns one. The previous
    invite dies; the new link + QR is shown once, to be handed over by the
    operator."""
    try:
        return leads_service.reissue_invite(dataset_id, base_url=_base_url(request),
                                            actor=admin)
    except LeadError as e:
        raise _fail(e)


@router.delete("/admin/api/leads/companies/{dataset_id}")
async def admin_delete_company(dataset_id: str, admin: str = Depends(verify_admin)):
    """Drop a company from the leads feature: leads, invites, pending drafts.
    The dataset row itself is the dataset page's business."""
    return leads_service.delete_company(dataset_id, actor=admin)


# ── Company profiles ─────────────────────────────────────────────────────
# What the organizer already knows about each exhibitor — see
# app/services/company_profiles.py for why this is a table beside `dataset`
# and `company_leads`, not a column of either.

@router.get("/admin/api/company-profiles", dependencies=[Depends(verify_admin)])
async def admin_company_profiles(q: str = "", warmth: str = "", limit: int = 25, offset: int = 0):
    from app.services import company_profiles
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    rows = company_profiles.list_companies(q, limit=limit, offset=offset, warmth=warmth)
    total = company_profiles.count_companies(q, warmth=warmth)
    return {"companies": rows, "total": total, "has_more": offset + len(rows) < total}


@router.get("/admin/api/company-profiles/export", dependencies=[Depends(verify_admin)])
async def admin_companies_export():
    """The organizer's follow-up sheet: every company with its CURRENT
    eagerness and contact block — the specific data marketing asked to be
    able to slice by. Organizer-only (nothing here feeds any chat path)."""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from app.services import company_profiles

    def safe(value) -> str:
        s = "" if value is None else str(value)
        return "'" + s if s.startswith(("=", "+", "-", "@", "\t", "\r")) else s

    warmth_fa = {"low": "سرد", "medium": "معمولی", "high": "داغ"}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["شرکت", "علاقه‌مندی", "حوزهٔ فعالیت", "شماره غرفه", "سالن",
                     "مسئول ثبت‌شده", "سمت", "تلفن شرکت", "ایمیل", "وب‌سایت", "استان"])
    for c in company_profiles.list_companies(limit=500):
        writer.writerow([
            safe(c.get("title")), warmth_fa.get(c.get("marketing_warmth"), ""),
            safe(c.get("activity_field")), safe(c.get("booth_number")),
            safe(c.get("hall")), safe(c.get("contact_name")),
            safe(c.get("contact_position")), safe(c.get("company_phone")),
            safe(c.get("email")), safe(c.get("website")), safe(c.get("province")),
        ])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="companies.csv"'},
    )


# ── Activity-field autofill ──────────────────────────────────────────────
# The companies-page button that fills empty حوزهٔ فعالیت rows from each
# company's own intro text. See app/services/company_autofill.py for the
# contract: the model suggests, the code validates, only empty fields change.
# Declared BEFORE the /{dataset_id} routes: FastAPI matches in order, and a
# literal path must not fall into the wildcard.

@router.get("/admin/api/company-profiles/autofill",
            dependencies=[Depends(verify_admin)])
async def admin_autofill_preview():
    """What the button shows: how many companies can be filled right now."""
    from app.services import company_autofill
    return company_autofill.pending()


@router.post("/admin/api/company-profiles/autofill")
async def admin_autofill_run(payload: dict = Body(default={}),
                             admin: str = Depends(verify_admin)):
    """Fill one batch (≤ 10 fills, ≤ 40 scans) of empty fields; the UI
    loops, passing the returned cursor forward so a stretch of no-yield
    companies is asked once per pass, not once per batch."""
    from app.services import company_autofill
    cursor = payload.get("cursor") if isinstance(payload, dict) else None
    if not (isinstance(cursor, (list, tuple)) and len(cursor) == 2
            and all(isinstance(v, str) for v in cursor)):
        cursor = None
    try:
        return await company_autofill.run(actor=admin, cursor=cursor)
    except company_autofill.AutofillUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/admin/api/company-profiles/{dataset_id}",
            dependencies=[Depends(verify_admin)])
async def admin_company_profile(dataset_id: str):
    from app.services import company_profiles
    return {
        "profile": company_profiles.get_profile(dataset_id),
        "video_url": company_profiles.get_video(dataset_id),
        "content": company_profiles.get_public_content(dataset_id),
        "priority_boost": company_profiles.get_priority_boost(dataset_id),
    }


@router.put("/admin/api/company-profiles/{dataset_id}")
async def admin_save_company_profile(dataset_id: str, payload: dict = Body(default={}),
                                     admin: str = Depends(verify_admin)):
    from app.services import company_profiles
    try:
        return {"profile": company_profiles.upsert_profile(dataset_id, payload)}
    except company_profiles.ProfileError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


class CompanyVideoBody(BaseModel):
    video_url: str = Field(default="", max_length=500)


@router.put("/admin/api/company-profiles/{dataset_id}/video",
            dependencies=[Depends(verify_admin)])
async def admin_set_company_video(dataset_id: str, body: CompanyVideoBody):
    """Set a company's intro video, the same way a dataset entry's video is
    set — see app/services/company_profiles.py:set_video for why this is
    separate from the profile form."""
    from app.services import company_profiles
    try:
        return {"video_url": company_profiles.set_video(dataset_id, body.video_url)}
    except company_profiles.ProfileError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.put("/admin/api/company-profiles/{dataset_id}/content",
            dependencies=[Depends(verify_admin)])
async def admin_set_company_content(dataset_id: str, payload: dict = Body(default={})):
    """Set a company's public content — the title/title_en/text/text_en the
    dataset editor sets for a normal dataset row; see
    app/services/company_profiles.py:set_public_content for why this is
    separate from the profile form and from the video endpoint above."""
    from app.services import company_profiles
    try:
        return {"content": company_profiles.set_public_content(dataset_id, payload)}
    except company_profiles.ProfileError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


class CompanyPriorityBoostBody(BaseModel):
    priority_boost: bool = False


@router.put("/admin/api/company-profiles/{dataset_id}/priority-boost",
            dependencies=[Depends(verify_admin)])
async def admin_set_company_priority_boost(dataset_id: str, body: CompanyPriorityBoostBody):
    """Pin a company ahead of the alphabetical company-list order — a sort
    flag, not organizer knowledge, so like video/content above it is its own
    endpoint rather than a PROFILE_FIELDS key; see
    app/services/company_profiles.py:set_priority_boost for why."""
    from app.services import company_profiles
    try:
        return {"priority_boost":
                company_profiles.set_priority_boost(dataset_id, body.priority_boost)}
    except company_profiles.ProfileError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


def _trigger_reindex():
    """Rebuild this worker's indexes after a company delete and publish a new
    version stamp so every other worker rebuilds too (same contract as
    app/routers/dataset.py:_trigger_reindex — see search.reindex_and_publish).

    A delete is what removes a company from the chatbot's answers: without
    this, companies_lookup and the intent head would keep serving a company
    whose row is gone."""
    import asyncio
    from app.services.search import reindex_and_publish
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, reindex_and_publish)
    except RuntimeError:
        reindex_and_publish()


@router.delete("/admin/api/company-profiles/{dataset_id}",
               dependencies=[Depends(verify_admin)])
async def admin_delete_company(dataset_id: str):
    """Delete one company and its whole footprint (curated question anchors,
    capture history) — see company_profiles.delete_companies."""
    from app.services import company_profiles
    deleted = company_profiles.delete_companies([dataset_id])
    if not deleted:
        raise HTTPException(status_code=404, detail="این شرکت در دانش‌نامه نیست.")
    _trigger_reindex()
    return {"status": "deleted", "deleted": deleted}


@router.post("/admin/api/company-profiles/bulk-delete",
             dependencies=[Depends(verify_admin)])
async def admin_bulk_delete_companies(payload: dict):
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="هیچ موردی برای حذف انتخاب نشده است.")
    if not all(isinstance(i, str) for i in ids):
        raise HTTPException(status_code=400, detail="لیست شناسه‌ها نامعتبر است.")
    from app.services import company_profiles
    deleted = company_profiles.delete_companies(ids)
    _trigger_reindex()
    return {"status": "deleted", "deleted": deleted}


@router.get("/admin/api/leads/funnel", dependencies=[Depends(verify_admin)])
async def admin_funnel():
    return leads_service.funnel()


@router.get("/admin/api/leads", dependencies=[Depends(verify_admin)])
async def admin_leads(visitor_id: str = "", limit: int = 25, offset: int = 0):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    rows = leads_service.list_leads(visitor_id, limit=limit, offset=offset)
    total = leads_service.count_leads(visitor_id)
    return {"leads": rows, "total": total, "has_more": offset + len(rows) < total}


@router.get("/admin/api/leads/stuck", dependencies=[Depends(verify_admin)])
async def admin_stuck(limit: int = 25, offset: int = 0):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    rows = leads_service.stuck_leads(limit=limit, offset=offset)
    total = leads_service.count_stuck_leads()
    return {"stuck": rows, "total": total, "has_more": offset + len(rows) < total}


def _note_csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection in exported cells — mirrors
    app/routers/admin.py:_csv_safe; agent-typed notes are exactly the kind of
    visitor-authored text that ends up opened in Excel."""
    s = "" if value is None else str(value)
    if s.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + s
    return s


@router.get("/admin/api/leads/notes", dependencies=[Depends(verify_admin)])
async def admin_notes(dataset_id: str = "", q: str = "", limit: int = 200):
    """The visit-notes feed; `dataset_id` narrows it to one company's
    timeline, `q` searches company/note/contact/agent text."""
    return {"notes": leads_service.list_notes(dataset_id=dataset_id, q=q,
                                               limit=limit)}


@router.get("/admin/api/leads/notes/export", dependencies=[Depends(verify_admin)])
async def admin_notes_export():
    import csv
    import io

    from fastapi.responses import StreamingResponse

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["شرکت", "همکار غرفه", "میزان علاقه‌مندی", "یادداشت",
                     "نام مسئول", "سمت", "شماره تماس", "زمان"])
    for n in leads_service.list_notes(limit=500):
        writer.writerow([
            _note_csv_safe(n.get("company_name")), _note_csv_safe(n.get("visitor_name")),
            {"low": "سرد", "medium": "معمولی", "high": "داغ"}.get(n.get("warmth"), ""),
            _note_csv_safe(n.get("note")), _note_csv_safe(n.get("contact_name")),
            _note_csv_safe(n.get("contact_position")), _note_csv_safe(n.get("contact_phone")),
            _note_csv_safe(n.get("created_at")),
        ])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="marketing-notes.csv"'},
    )


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
            "sms_invite_text": sms["text"]}


class SettingsBody(BaseModel):
    invite_channel: str = Field(default="", max_length=10)
    consent_script: str = Field(default="", max_length=4000)
    sms_invite_text: str = Field(default="", max_length=1000)


@router.post("/admin/api/leads/settings", dependencies=[Depends(verify_admin)])
async def admin_save_settings(body: SettingsBody):
    try:
        if body.invite_channel:
            leads_service.set_invite_channel(body.invite_channel)
        if body.consent_script.strip():
            leads_service.set_consent_script(body.consent_script)
        if body.sms_invite_text.strip():
            if "{magic_link}" not in body.sms_invite_text:
                raise HTTPException(
                    status_code=400,
                    detail="متن پیامکِ لینک دعوت باید عبارت {magic_link} را دقیقاً یک بار "
                           "داشته باشد — همان‌جا لینک واقعی جایگزین می‌شود.")
            from app.services import sms as sms_service
            sms_service.save_settings({"sms_asanak_invite_text": body.sms_invite_text})
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


@router.post("/admin/api/leads/visitors/{visitor_id}/rename",
             dependencies=[Depends(verify_admin)])
async def admin_visitor_rename(visitor_id: str, body: VisitorBody):
    """Fix a typo'd roster name. Leads join the name live, and visit notes
    carry a denormalized copy — rename_visitor keeps both saying the same
    thing."""
    try:
        if not leads_service.rename_visitor(visitor_id, body.name):
            raise HTTPException(status_code=404, detail="این همکار پیدا نشد.")
    except LeadError as e:
        raise _fail(e)
    return {"ok": True, "name": body.name.strip()}


@router.delete("/admin/api/leads/visitors/{visitor_id}")
async def admin_delete_visitor(visitor_id: str, admin: str = Depends(verify_admin)):
    """Take a colleague off the roster. Their link stops working immediately;
    the leads they captured are history and stay."""
    if not leads_service.delete_visitor(visitor_id, actor=admin):
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


class BulkVisitorIdsBody(BaseModel):
    ids: list[str] = Field(default_factory=list)


@router.post("/admin/api/leads/visitors/bulk-delete")
async def admin_bulk_delete_visitors(body: BulkVisitorIdsBody, admin: str = Depends(verify_admin)):
    """Same removal as the single-visitor DELETE above, looped: each colleague's
    link dies immediately, their captured leads stay exactly where they are.
    An id no longer on the roster is skipped, not an error — the caller is
    acting on whatever the checkboxes still point at, not asserting each one
    still exists."""
    if not body.ids:
        raise HTTPException(status_code=400, detail="هیچ همکاری برای حذف انتخاب نشده است.")
    deleted = sum(1 for visitor_id in body.ids
                  if leads_service.delete_visitor(visitor_id, actor=admin))
    return {"status": "ok", "deleted": deleted}


class BulkVisitorActiveBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    active: bool


@router.post("/admin/api/leads/visitors/bulk-active", dependencies=[Depends(verify_admin)])
async def admin_bulk_set_visitors_active(body: BulkVisitorActiveBody):
    """Same toggle as the single-visitor /active route above, looped."""
    if not body.ids:
        raise HTTPException(status_code=400, detail="هیچ همکاری انتخاب نشده است.")
    updated = sum(1 for visitor_id in body.ids
                  if leads_service.set_visitor_active(visitor_id, body.active))
    return {"status": "ok", "updated": updated}


@router.get("/admin/api/leads/edits", dependencies=[Depends(verify_admin)])
async def admin_edits(status: str = "pending"):
    return {"edits": leads_service.list_edits(status)}


# ── Bulk confirm campaigns (migrations/0024) ─────────────────────────────
# The organizer texts every company with a mobile on file, each with its own
# one-time link. The send itself is paced (~1/second) and lives in a
# background task; these endpoints launch it and report on it.

@router.get("/admin/api/leads/campaigns", dependencies=[Depends(verify_admin)])
async def admin_campaigns():
    from app.services import campaigns, sms_outbox
    listed = campaigns.list_campaigns()
    # Delivery counts per campaign: the report the operator reads is "how many
    # actually arrived", which only the outbox knows.
    for row in listed:
        row["delivery"] = sms_outbox.status_counts(row["id"])
    return {"campaigns": listed, "capability": campaigns.capability()}


class CampaignBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/admin/api/leads/campaigns")
async def admin_launch_campaign(body: CampaignBody, request: Request,
                                background: BackgroundTasks,
                                admin: str = Depends(verify_admin)):
    from app.services import campaigns
    try:
        launched = campaigns.launch(body.text, _base_url(request), actor=admin)
    except campaigns.CampaignError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    # Paced by the second, so the run must not hold the request open: the
    # panel gets the campaign id and watches the report fill in.
    background.add_task(campaigns.run, launched["id"], _base_url(request))
    return launched


@router.get("/admin/api/leads/campaigns/{campaign_id}",
            dependencies=[Depends(verify_admin)])
async def admin_campaign_detail(campaign_id: str):
    from app.services import campaigns
    try:
        return campaigns.campaign_detail(campaign_id)
    except campaigns.CampaignError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


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


@router.get("/secure-panel-inotex/companies", response_class=HTMLResponse)
async def admin_companies_page(request: Request):
    """The organizer's exhibitor book: every company beside what is known
    about it, editable in place."""
    from app.routers.public import _render, _require_admin

    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/companies.html", request=request, active_page="companies")
