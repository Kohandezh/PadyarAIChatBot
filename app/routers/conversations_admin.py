"""Admin API for who visited, what they said, and where the bot was wrong.

This is the READ side of the seam described in app/services/conversations.py.
The chat router writes visitors, conversations and messages; these endpoints
are the only way a human ever sees them, and they exist for three questions
the owner of an exhibition install actually asks:

  1. Who came, and how do I reach them again?      -> /admin/api/visitors
  2. What did this person and the bot say?         -> /admin/api/conversations
  3. Where did the bot answer badly, so I can fix
     the content that should have answered?        -> .../conversations/weak

(3) is the one that makes the product better, so it is a first-class endpoint
with its own screen and its own sidebar link, not a filter buried in a list.

There is ONE write endpoint here, and it is deliberately the only one: ending
a visitor's sessions (.../visitors/{id}/sessions/revoke). It lives on this
router because the visitor list is the screen where an operator is standing
when somebody tells them their phone was stolen. See its own docstring.

PRIVACY — every route here is admin-only
----------------------------------------
These responses carry names, raw phone numbers, IP addresses and the exact
words visitors typed. Nothing here is reachable from the chat surface: every
route carries `dependencies=[Depends(verify_admin)]`, and the HTML shells in
app/routers/public.py go through the same session check. Compare
app/services/company_profiles.py, which withholds contact fields from a
VISITOR; that allowlist is about the public chat. An administrator of the
install is the data controller and sees the record in full — but only after
authenticating, and every export they take is written to the audit trail
first, with the actor and the real row count.

SHARED FILTER KEYS
------------------
The list endpoint and the CSV export read their filters from ONE tuple each
(`_CONV_FILTERS`, `_VISITOR_FILTERS`), the way app/routers/logs.py does. An
export that silently ignores a filter the screen applied would hand the
operator a file that is not what they were looking at.
"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import visitor as visitor_auth
from app.auth.security import client_ip, verify_admin
from app.config import LOCAL_FALLBACK_THRESHOLD, TRUSTED_MATCH_THRESHOLD
from app.services import applog
from app.services import conversations as store
# The one function that neutralises spreadsheet formula injection. Imported
# rather than copied: visitor-typed text goes into both exports, and two
# copies of this rule are one copy that will be forgotten.
from app.routers.admin import _csv_safe

router = APIRouter()


# What "the bot was not sure" means on these screens. It is the pipeline's own
# floor (app/config.LOCAL_FALLBACK_THRESHOLD): below it the chat router will
# not serve a local match at all, so every answer under this line is either a
# guess or a "please rephrase". Reusing the pipeline's number rather than
# inventing a panel-only one keeps the screen honest when the number moves.
WEAK_BELOW = LOCAL_FALLBACK_THRESHOLD

# How many turns the weak screen loads at once. It is a work queue, not an
# archive: an operator fixes the newest mistakes and comes back tomorrow.
WEAK_LIMIT_DEFAULT = 50
WEAK_LIMIT_MAX = 500

# Rows one CSV may carry, and the page size used to walk there. The service
# caps a single call at 500, so the export pages through instead of asking for
# a number it would silently not get.
EXPORT_PAGE = 500
EXPORT_MAX = 5000

# Which tier answered, in words an operator can read. The keys are the
# `source` strings app/routers/chat.py records. An unknown key is rendered
# raw by the UI, so a new tier shows up as itself instead of disappearing.
SOURCE_FA = {
    "local_pick": "انتخاب از فهرست",
    "local_questions": "سوال آماده",
    "local": "پاسخ آماده",
    "local_intent": "تشخیص موضوع",
    "local_entity": "نام شرکت",
    "local_company_search": "فهرست شرکت‌ها",
    "local_company_field": "اطلاعات شرکت",
    "ai_selected": "انتخاب هوشمند",
    "ai_options": "پیشنهاد چند گزینه",
    "openai": "پاسخ هوش مصنوعی",
    "refuse": "خارج از موضوع",
    "system": "بدون پاسخ",
}

# The sources that mean the visitor got no real answer. They are flagged in
# the weak list even when their score sits above the line, because "I could
# not help" is a failure whatever number came with it.
NO_ANSWER_SOURCES = ("system", "refuse")

_CONV_FILTERS = ("since", "until", "registered", "visitor_id", "source",
                 "min_confidence", "max_confidence", "q")
_VISITOR_FILTERS = ("since", "until", "q", "job", "interest")


# ── Query-string helpers ─────────────────────────────────────────────────

def _raw(request: Request, keys) -> dict:
    """The filter values exactly as the screen sent them.

    Echoed back in the list response and recorded in the export's audit row,
    so "what was this file filtered by" is answerable later.
    """
    return {k: (request.query_params.get(k) or "").strip() for k in keys}


def _int(request: Request, name: str, default: int) -> int:
    try:
        return int(request.query_params.get(name) or default)
    except (TypeError, ValueError):
        return default


def _float_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, detail="مقدار «میزان اطمینان» باید یک عدد باشد.")


def _registered(value):
    """'yes' -> registered only, 'no' -> anonymous only, anything else -> both."""
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _until(value: str) -> str:
    """Make «تا تاریخ» mean the END of that day.

    The operator picks a date and means "up to and including this day". A bare
    date is midnight, so without this the last day of the range comes back
    empty and the screen looks broken. app/services/conversations.py says the
    same thing from the other side: pass a time for an end-of-day bound.
    """
    if value and len(value) == 10:
        return value + " 23:59:59"
    return value


def _conv_kwargs(params: dict) -> dict:
    return {
        "since": params["since"] or None,
        "until": _until(params["until"]) or None,
        "has_visitor": _registered(params["registered"]),
        "visitor_id": params["visitor_id"],
        "source": params["source"],
        "min_confidence": _float_or_none(params["min_confidence"]),
        "max_confidence": _float_or_none(params["max_confidence"]),
        "q": params["q"],
    }


def _visitor_kwargs(params: dict) -> dict:
    return {
        "since": params["since"] or None,
        "until": _until(params["until"]) or None,
        "q": params["q"],
        "job": params["job"],
        "interest": params["interest"],
    }


# ── Conversations ────────────────────────────────────────────────────────

@router.get("/admin/api/conversations", dependencies=[Depends(verify_admin)])
async def list_conversations(request: Request):
    """One page of conversations, newest activity first.

    There is no total count. Getting one means a second COUNT(*) over the same
    filters on every page turn, and the screen does not need it: it asks for
    one row more than it shows, and `has_more` turns that into a working
    "next" button. Cheap, exact, and no number that goes stale mid-scroll.
    """
    params = _raw(request, _CONV_FILTERS)
    limit = max(1, min(_int(request, "limit", 50), 200))
    offset = max(0, _int(request, "offset", 0))

    rows = store.list_conversations(limit=limit + 1, offset=offset,
                                    weak_below=WEAK_BELOW,
                                    **_conv_kwargs(params))
    has_more = len(rows) > limit
    return {
        "rows": rows[:limit],
        "has_more": has_more,
        "limit": limit,
        "offset": offset,
        "sources": SOURCE_FA,
        "weak_below": WEAK_BELOW,
        "trusted_above": TRUSTED_MATCH_THRESHOLD,
        "filters": params,
    }


@router.get("/admin/api/conversations/weak", dependencies=[Depends(verify_admin)])
async def weak_answers(request: Request):
    """The turns where the bot was unsure or said it could not help.

    Each row carries the QUESTION that produced it, which the store does not
    hold on the answer row — the question is the previous message. The
    transcript of each conversation is read once and shared by every weak turn
    inside it, so a screen full of mistakes from one long session costs one
    read, not one per row.
    """
    threshold = _float_or_none(request.query_params.get("threshold")) or WEAK_BELOW
    threshold = max(0.01, min(float(threshold), 1.0))
    limit = max(1, min(_int(request, "limit", WEAK_LIMIT_DEFAULT), WEAK_LIMIT_MAX))

    rows = store.weak_answers(threshold=threshold, limit=limit)

    transcripts = {}
    out = []
    for row in rows:
        cid = row.get("conversation_id") or ""
        if cid not in transcripts:
            transcripts[cid] = store.conversation_messages(cid)
        question = ""
        for message in transcripts[cid]:
            if message["id"] >= row["id"]:
                break
            if message.get("role") == store.ROLE_VISITOR:
                question = message.get("text") or ""
        item = dict(row)
        item["question"] = question
        item["no_answer"] = row.get("source") in NO_ANSWER_SOURCES
        out.append(item)

    return {"rows": out, "threshold": threshold, "limit": limit,
            "sources": SOURCE_FA}


@router.get("/admin/api/conversations/export", dependencies=[Depends(verify_admin)])
async def export_conversations(request: Request,
                               username: str = Depends(verify_admin)):
    """The filtered conversation list as CSV.

    The audit row is written BEFORE the file leaves, and it carries the real
    row count — same rule as app/routers/logs.py. An export of personal data
    that nobody can prove happened is the thing an audit trail is for.
    """
    params = _raw(request, _CONV_FILTERS)
    rows = _drain(store.list_conversations, weak_below=WEAK_BELOW,
                  **_conv_kwargs(params))

    applog.audit("admin.conversations.exported",
                 message=f"خروجی CSV گفتگوها گرفته شد ({len(rows)} ردیف)",
                 actor=username, target="conversations", outcome="ok",
                 ip=client_ip(request),
                 metadata={"format": "csv", "rows": len(rows),
                           "filters": params})

    header = ["شناسه گفتگو", "شروع", "آخرین پیام", "تعداد پیام",
              "نام", "نام خانوادگی", "شماره تماس", "زبان",
              "پاسخ‌های ضعیف", "IP"]
    body = [[r["id"], r["started_at"], r["last_message_at"], r["message_count"],
             r["first_name"], r["last_name"], r["phone"], r["lang"],
             r.get("weak_count", 0), r["ip"]] for r in rows]
    return _csv_response(header, body, "conversations")


@router.get("/admin/api/conversations/{conversation_id}",
            dependencies=[Depends(verify_admin)])
async def conversation_detail(conversation_id: str):
    """One whole session: the header, then every turn in the order it happened.

    Declared LAST on purpose. FastAPI matches in declaration order, so a
    literal path registered after this one ("/weak", "/export") would be
    swallowed by this parameter. app/routers/logs.py carries the same note.
    """
    header = store.get_conversation(conversation_id)
    if not header:
        raise HTTPException(404, detail="این گفتگو پیدا نشد.")
    messages = store.conversation_messages(conversation_id)
    visitor = store.get_visitor(header.get("visitor_id") or "")
    return {"conversation": header, "messages": messages,
            "visitor": visitor, "sources": SOURCE_FA,
            "weak_below": WEAK_BELOW}


# ── Visitors ─────────────────────────────────────────────────────────────

@router.get("/admin/api/visitors", dependencies=[Depends(verify_admin)])
async def list_visitors(request: Request):
    """One page of registered people, newest first.

    The search box takes a name OR a phone number. The store deliberately
    keeps phone out of its free-text search (a partial-number scan over
    everyone is a different feature), so a term that parses as a real number
    is looked up as a WHOLE number through find_visitor_by_phone and put at
    the top. The operator types one thing into one box and both work.
    """
    params = _raw(request, _VISITOR_FILTERS)
    limit = max(1, min(_int(request, "limit", 50), 200))
    offset = max(0, _int(request, "offset", 0))

    rows = store.list_visitors(limit=limit + 1, offset=offset,
                               **_visitor_kwargs(params))
    has_more = len(rows) > limit
    rows = rows[:limit]

    if params["q"] and not offset:
        match = store.find_visitor_by_phone(params["q"])
        if match and all(r["id"] != match["id"] for r in rows):
            match["conversation_count"] = len(store.list_conversations(
                visitor_id=match["id"], limit=EXPORT_PAGE))
            rows.insert(0, match)

    return {"rows": rows, "has_more": has_more, "limit": limit,
            "offset": offset, "filters": params}


@router.get("/admin/api/visitors/export", dependencies=[Depends(verify_admin)])
async def export_visitors(request: Request,
                          username: str = Depends(verify_admin)):
    """The filtered visitor list as CSV — the lead list the exhibition is for."""
    params = _raw(request, _VISITOR_FILTERS)
    rows = _drain(store.list_visitors, **_visitor_kwargs(params))

    applog.audit("admin.visitors.exported",
                 message=f"خروجی CSV بازدیدکنندگان گرفته شد ({len(rows)} ردیف)",
                 actor=username, target="visitors", outcome="ok",
                 ip=client_ip(request),
                 metadata={"format": "csv", "rows": len(rows),
                           "filters": params})

    header = ["نام", "نام خانوادگی", "شماره تماس", "شغل", "سمت",
              "زمینه‌های مورد علاقه", "تعداد گفتگو", "ثبت‌نام", "آخرین بازدید"]
    body = [[r["first_name"], r["last_name"], r["phone"], r["job"],
             r["position"], r["interests"], r.get("conversation_count", 0),
             r["created_at"], r["last_seen_at"]] for r in rows]
    return _csv_response(header, body, "visitors")


@router.post("/admin/api/visitors/{visitor_id}/sessions/revoke",
             dependencies=[Depends(verify_admin)])
async def revoke_visitor_sessions(visitor_id: str, request: Request,
                                  username: str = Depends(verify_admin)):
    """Sign one visitor out of every browser and phone at once.

    THE CASE THIS IS FOR. A visitor registers at the kiosk, walks the hall,
    and their phone is stolen. The session cookie in that phone is the whole
    credential. Whoever holds it is that person to this install, for the
    remaining 30 days. They tell the booth, and until now an operator had no
    way to end it: `revoke_all()` shipped with nothing calling it.

    This is also the reason `visitor_sessions` is a TABLE and not a signed
    token. migrations/0012_visitor_sessions.sql justifies the rows with
    exactly this: a session has to be revocable the second someone asks, and
    a signature cannot be un-signed. Without a caller that justification was
    a promise the product did not keep.

    404 on an unknown visitor rather than a quiet 0. An operator who mistyped
    an id would otherwise see "done" and believe the stolen phone was cut off.

    The audit row names the visitor by ID, never by phone number. Audit rows
    are read by more people than the visitor list is, and the id is enough to
    find the person again.

    It takes the id in the PATH and no body, so the visitor list can call it
    with the id it already has. CSRF is covered: /admin/ is inside
    PROTECTED_PREFIXES, so the middleware in app/main.py checks the token
    before this function runs.
    """
    visitor = store.get_visitor((visitor_id or "").strip())
    if not visitor:
        raise HTTPException(404, detail="این بازدیدکننده پیدا نشد.")

    removed = visitor_auth.revoke_all(visitor["id"])

    applog.audit("admin.visitor.sessions_revoked",
                 message=f"همهٔ نشست‌های یک بازدیدکننده باطل شد ({removed} نشست)",
                 actor=username, target=visitor["id"], outcome="ok",
                 level="warning", ip=client_ip(request),
                 metadata={"revoked": removed})
    return {"revoked": removed}


# ── Export plumbing ──────────────────────────────────────────────────────

def _drain(list_fn, **kwargs) -> list:
    """Every matching row, in pages, up to EXPORT_MAX.

    The store clamps a single call to 500 rows. Asking it for 5000 would
    quietly return 500 and the operator would export a file missing most of
    their day without being told.
    """
    rows, offset = [], 0
    while len(rows) < EXPORT_MAX:
        page = list_fn(limit=EXPORT_PAGE, offset=offset, **kwargs)
        rows.extend(page)
        if len(page) < EXPORT_PAGE:
            break
        offset += EXPORT_PAGE
    return rows[:EXPORT_MAX]


def _csv_response(header: list, body: list, name: str) -> StreamingResponse:
    """A CSV Excel opens correctly in Persian, with every cell defused.

    utf-8-sig: without the BOM, Excel reads Persian as mojibake and the owner
    concludes the export is broken. Same choice as the chat-history export in
    app/routers/admin.py.
    """
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for line in body:
        writer.writerow([_csv_safe(cell) for cell in line])
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        io.BytesIO(out.getvalue().encode("utf-8-sig")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.csv"',
                 "Cache-Control": "no-store"})
