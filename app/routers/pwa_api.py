"""pwa_api — Bearer-token API for the InotexPWA app.

See docs/features/pwa-api/SPEC.md for the full spec: a public, allowlisted
companies directory (Group B), an independent chat-token mint off a visitor
session (Group C), per-visitor settings (Group D), and a short-lived personal
QR for connecting two visitors (Group E). Group A (the Bearer fallback in
`resolve_visitor`, and the `access_token` field on OTP verify) lives in
app/main.py and app/routers/otp.py, not here.
"""
import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import visitor as visitor_auth
from app.auth import security
from app.auth.security import validate_request_origin, client_ip
from app.config import CHAT_TOKEN_TTL
from app.services import company_profiles
from app.services import conversations

router = APIRouter()


# ── Group B: public companies directory (no auth, no origin check) ────────

@router.get("/api/companies")
async def list_companies(request: Request, q: str = "", industry: str = "",
                         type: str = "", page: int = 1, page_size: int = 20):
    """Public, allowlisted companies directory (REQ-005, REQ-007, REQ-008)."""
    security.check_rate_limit(request, key=f"companies:{client_ip(request)}",
                              limit=security.PAGE_RATE_LIMIT)
    rows, has_more = company_profiles.list_public_directory(
        q=q, industry=industry, company_type=type, page=page, page_size=page_size)
    return {"companies": rows, "page": page, "has_more": has_more}


@router.get("/api/companies/{company_id}")
async def get_company(company_id: str, request: Request):
    """REQ-006."""
    security.check_rate_limit(request, key=f"companies:{client_ip(request)}",
                              limit=security.PAGE_RATE_LIMIT)
    entry = company_profiles.public_directory_entry(company_id)
    if not entry:
        raise HTTPException(status_code=404, detail="یافت نشد.")
    return entry


# ── Group C: mint the first chat token off a visitor session ──────────────

@router.post("/api/chat-token/mint",
            dependencies=[Depends(validate_request_origin)])
async def mint_chat_token(visitor_id: str = Depends(visitor_auth.require_visitor)):
    """REQ-009 to REQ-012: mint the FIRST chat token from a visitor session —
    not a refresh of an existing one (that's the existing POST /api/chat-token
    in app/routers/chat.py, which needs a valid token already; this needs
    none, only a session)."""
    expires_at = datetime.datetime.now(datetime.timezone.utc) \
        + datetime.timedelta(seconds=CHAT_TOKEN_TTL)
    return {"chat_token": security.generate_chat_token(),
            "expires_at": expires_at.isoformat()}


# ── Group D: per-visitor settings (calendar, contacts, language) ──────────

@router.get("/api/me/settings",
           dependencies=[Depends(validate_request_origin)])
async def get_my_settings(visitor_id: str = Depends(visitor_auth.require_visitor)):
    """REQ-014."""
    return conversations.get_visitor_settings(visitor_id)


class CalendarEventBody(BaseModel):
    event_id: str


@router.post("/api/me/calendar",
            dependencies=[Depends(validate_request_origin)])
async def post_calendar_event(body: CalendarEventBody,
                              visitor_id: str = Depends(visitor_auth.require_visitor)):
    """REQ-015."""
    return {"calendar": conversations.add_calendar_event(visitor_id, body.event_id)}


@router.delete("/api/me/calendar/{event_id}",
              dependencies=[Depends(validate_request_origin)])
async def delete_calendar_event(event_id: str,
                                visitor_id: str = Depends(visitor_auth.require_visitor)):
    """REQ-016."""
    return {"calendar": conversations.remove_calendar_event(visitor_id, event_id)}


class ConnectContactsBody(BaseModel):
    qr_payload: str


@router.post("/api/me/contacts/connect",
            dependencies=[Depends(validate_request_origin)])
async def connect_contacts(body: ConnectContactsBody,
                           visitor_id: str = Depends(visitor_auth.require_visitor)):
    """REQ-017."""
    other_id = security.validate_visitor_qr_payload(body.qr_payload)
    if not other_id or other_id == visitor_id:
        raise HTTPException(status_code=400, detail="کد نامعتبر یا منقضی‌شده است.")
    try:
        contacts = conversations.connect_visitors(visitor_id, other_id)
    except conversations.ContactsAlreadyConnected:
        raise HTTPException(status_code=409, detail="این دو نفر قبلاً متصل شده‌اند.")
    except ValueError:
        raise HTTPException(status_code=400, detail="کد نامعتبر یا منقضی‌شده است.")
    return {"contacts": contacts}


# ── Group E: short-lived personal QR ───────────────────────────────────────

@router.get("/api/me/qr",
           dependencies=[Depends(validate_request_origin)])
async def get_my_qr(visitor_id: str = Depends(visitor_auth.require_visitor)):
    """REQ-018, REQ-019."""
    payload, expires_at = security.generate_visitor_qr_payload(visitor_id)
    return {"payload": payload, "expires_at": expires_at}
