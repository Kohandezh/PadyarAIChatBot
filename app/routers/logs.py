"""Admin API for the central log store.

Every endpoint is admin-only. Three things here are deliberate and should not
be "simplified" later:

  * The list endpoint never returns `stack`. A traceback is a map of the
    codebase; it is available only on the single-row detail endpoint.
  * `truncate` refuses to touch `audit_logs`. An administrator must not be able
    to erase the record of their own destructive actions from the same screen
    that performs them.
  * Every export and every truncate writes an audit row BEFORE returning, with
    the actor and the real row count.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth.security import client_ip, verify_admin
from app.db.queries import get_setting, set_setting
from app.models import LogSettingsRequest, LogTruncateRequest
from app.services import applog

router = APIRouter()

# The filters the list, export and preview endpoints all share. Kept in one
# place so the three can never drift apart.
_FILTER_KEYS = ("category", "level", "q", "since", "until", "actor", "ip",
                "provider", "model", "request_id", "correlation_id",
                "conversation_id", "http_status", "outcome", "min_duration")

# `stack` is withheld from list responses on purpose — see the module docstring.
_LIST_OMIT = ("stack",)


def _filters(request: Request) -> dict:
    return {k: (request.query_params.get(k) or "") for k in _FILTER_KEYS}


@router.get("/admin/api/logs", dependencies=[Depends(verify_admin)])
async def list_logs(request: Request):
    params = _filters(request)

    def _int_param(name: str, default: int) -> int:
        try:
            return int(request.query_params.get(name) or default)
        except (TypeError, ValueError):
            return default

    rows, total = applog.query(
        sort=request.query_params.get("sort", "created_at"),
        direction=request.query_params.get("direction", "desc"),
        limit=_int_param("limit", 50),
        offset=_int_param("offset", 0),
        **params)
    return {
        "rows": [{k: v for k, v in r.items() if k not in _LIST_OMIT} for r in rows],
        "total": total,
        "limit": min(max(_int_param("limit", 50), 1), 500),
        "offset": max(_int_param("offset", 0), 0),
        "categories": applog.CATEGORIES,
        "levels": list(applog.LEVELS),
        "filters": params,
    }


@router.get("/admin/api/logs/summary", dependencies=[Depends(verify_admin)])
async def logs_summary(days: int = 1):
    try:
        window = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        window = 1
    data = applog.summary(window)
    data["categories"] = applog.CATEGORIES
    data["levels"] = list(applog.LEVELS)
    return data


@router.get("/admin/api/logs/settings", dependencies=[Depends(verify_admin)])
async def get_log_settings():
    return {
        "retention_days": applog.retention_days(),
        "audit_retention_days": applog.audit_retention_days(),
        "security_retention_days": applog.security_retention_days(),
        "debug_enabled": applog.debug_enabled(),
        "min_level": applog.min_level(),
        "content_policy": applog.content_policy(),
        "defaults": {
            "retention_days": applog.DEFAULT_RETENTION_DAYS,
            "audit_retention_days": applog.DEFAULT_AUDIT_RETENTION_DAYS,
            "security_retention_days": applog.DEFAULT_SECURITY_RETENTION_DAYS,
        },
        "allowed": {
            "levels": list(applog.LEVELS),
            "content_policies": ["metadata", "redacted", "full"],
            "retention_range": [0, 3650],
        },
        "storage_bytes": applog._db_size(),
    }


@router.post("/admin/api/logs/settings", dependencies=[Depends(verify_admin)])
async def save_log_settings(req: LogSettingsRequest, request: Request,
                            username: str = Depends(verify_admin)):
    def _days(value, label):
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, detail=f"مقدار «{label}» باید یک عدد باشد.")
        if not 0 <= n <= 3650:
            raise HTTPException(400, detail=f"«{label}» باید بین ۰ تا ۳۶۵۰ روز باشد.")
        return n

    retention = _days(req.retention_days, "نگهداشت لاگ عملیاتی")
    audit_ret = _days(req.audit_retention_days, "نگهداشت رخدادهای حساس")
    sec_ret = _days(req.security_retention_days, "نگهداشت رخدادهای امنیتی")

    if req.min_level not in applog.LEVELS:
        raise HTTPException(400, detail="سطح لاگ انتخاب‌شده معتبر نیست.")
    if req.content_policy not in ("metadata", "redacted", "full"):
        raise HTTPException(400, detail="سیاست ثبت محتوا معتبر نیست.")

    before = {
        "retention_days": applog.retention_days(),
        "audit_retention_days": applog.audit_retention_days(),
        "security_retention_days": applog.security_retention_days(),
        "debug_enabled": applog.debug_enabled(),
        "min_level": applog.min_level(),
        "content_policy": applog.content_policy(),
    }
    set_setting("log_retention_days", str(retention))
    set_setting("log_audit_retention_days", str(audit_ret))
    set_setting("log_security_retention_days", str(sec_ret))
    set_setting("log_debug_enabled", "true" if req.debug_enabled else "false")
    set_setting("log_min_level", req.min_level)
    set_setting("log_content_policy", req.content_policy)
    after = {
        "retention_days": retention, "audit_retention_days": audit_ret,
        "security_retention_days": sec_ret, "debug_enabled": bool(req.debug_enabled),
        "min_level": req.min_level, "content_policy": req.content_policy,
    }

    # A content policy of "full" starts persisting conversation text. That is a
    # privacy decision and belongs in the audit trail, not only in a setting.
    level = "warning" if req.content_policy == "full" else "notice"
    applog.audit("settings.logging.updated",
                 message="تنظیمات لاگ تغییر کرد",
                 actor=username, target="logging", outcome="ok", level=level,
                 ip=client_ip(request),
                 metadata={"before": before, "after": after})
    return {"status": "updated", **after}


@router.get("/admin/api/logs/truncate/preview", dependencies=[Depends(verify_admin)])
async def truncate_preview(request: Request):
    """How many rows a truncate WOULD remove, so the UI can show a real count."""
    before = _older_than(request.query_params.get("older_than_days"))
    return {"matching": applog.count_matching(
        category=request.query_params.get("category", ""),
        level=request.query_params.get("level", ""),
        before=before,
        table=request.query_params.get("table", ""))}


def _older_than(days) -> str:
    if days in (None, "", "0"):
        return ""
    try:
        n = int(days)
    except (TypeError, ValueError):
        raise HTTPException(400, detail="بازه زمانی وارد‌شده معتبر نیست.")
    if n <= 0:
        return ""
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")


@router.post("/admin/api/logs/truncate", dependencies=[Depends(verify_admin)])
async def truncate_logs(req: LogTruncateRequest, request: Request,
                        username: str = Depends(verify_admin)):
    # Refusing this is the whole point of keeping audit rows in a separate
    # table: the destructive screen cannot delete the evidence it generates.
    if req.table == "audit_logs" or req.category == "audit":
        raise HTTPException(
            400,
            detail="رخدادهای حساس (audit) از این صفحه پاک نمی‌شوند. این محدودیت "
                   "عمدی است تا سابقهٔ اقدامات مدیر قابل حذف نباشد.")

    before = _older_than(req.older_than_days)
    requested = applog.count_matching(category=req.category or "", level=req.level or "",
                                      before=before, table=req.table or "")
    deleted = applog.truncate(category=req.category or "", level=req.level or "",
                              before=before, table=req.table or "")

    applog.audit("admin.logs.truncated",
                 message=f"{deleted} رکورد لاگ حذف شد",
                 actor=username, target=req.table or req.category or "all",
                 outcome="ok", level="warning",
                 ip=client_ip(request),
                 metadata={"category": req.category, "level": req.level,
                           "table": req.table, "older_than_days": req.older_than_days,
                           "matched": requested, "deleted": deleted})
    return {"deleted": deleted, "requested": requested}


@router.get("/admin/api/logs/export", dependencies=[Depends(verify_admin)])
async def export_logs(request: Request, username: str = Depends(verify_admin)):
    fmt = (request.query_params.get("format") or "csv").lower()
    if fmt not in ("csv", "json"):
        raise HTTPException(400, detail="قالب خروجی فقط csv یا json می‌تواند باشد.")
    params = _filters(request)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    name = f"logs-{stamp}.{fmt}"

    applog.audit("admin.logs.exported",
                 message=f"خروجی {fmt.upper()} لاگ گرفته شد",
                 actor=username, target=params.get("category") or "all",
                 outcome="ok",
                 ip=client_ip(request),
                 metadata={"format": fmt, "filters": params})

    generator = applog.export_csv(**params) if fmt == "csv" else applog.export_json(**params)
    media = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
    return StreamingResponse(
        generator, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "Cache-Control": "no-store"})


# Registered last: a literal path must not shadow "/summary", "/settings" or
# "/export", and FastAPI matches in declaration order.
@router.get("/admin/api/logs/{row_id}", dependencies=[Depends(verify_admin)])
async def log_detail(row_id: int):
    row = applog.get_row(row_id)
    if not row:
        raise HTTPException(404, detail="این رکورد لاگ پیدا نشد.")
    chain, key = applog.related(row)
    return {"row": row,
            "related": [{k: v for k, v in r.items() if k not in _LIST_OMIT}
                        for r in chain],
            "related_key": key}
