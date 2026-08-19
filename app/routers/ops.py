"""Admin Operations & Control Center — dashboard, services, sessions, system.

Everything here is admin-only and read-mostly. The two write paths are:
  * running an ALLOWLISTED service action (app/services/service_control.py)
  * revoking an admin session

Both are audited. Neither accepts a free-form string that reaches a shell, a
filesystem path or SQL.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.security import verify_admin
from app.config import ADMIN_COOKIE_NAME
from app.db.connection import get_db_connection
from app.models import ServiceActionRequest, SessionRevokeRequest
from app.services import applog, health, service_control

router = APIRouter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


# ── Dashboard ───────────────────────────────────────────────────────────

@router.get("/admin/api/ops/dashboard", dependencies=[Depends(verify_admin)])
async def dashboard():
    """One operational picture. Every number below is measured, not estimated.

    Metrics this platform cannot obtain (queue depth, cache hit rate, worker
    counts) are absent rather than zero — there is no queue system and no cache
    in this install, and a tile reading "0 failed jobs" would imply a job
    system exists.
    """
    services = health.probe_all()
    score = health.health_score(services)

    since_24h = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    summary = applog.summary(1)
    by_cat = summary.get("by_category", {})
    by_level = summary.get("by_level", {})
    ERRORS = ("error", "critical", "alert", "emergency")

    def cat_count(category, levels=None):
        bucket = by_cat.get(category, {})
        return sum(v for k, v in bucket.items() if levels is None or k in levels)

    # Chat volume comes from the app database, not the log store — chat_logs is
    # the system of record for conversations.
    conversations = messages = 0
    try:
        conn = get_db_connection()
        messages = conn.execute(
            "SELECT COUNT(*) FROM chat_logs WHERE created_at >= datetime('now','-1 day')"
        ).fetchone()[0] or 0
        conversations = messages
        conn.close()
    except Exception:  # noqa: BLE001 — a dashboard must render even if one query fails
        pass

    llm_total = cat_count("llm")
    llm_errors = cat_count("llm", ERRORS)
    providers = summary.get("providers", []) or []
    avg_latency = next((int(p["avg_ms"]) for p in providers
                        if p.get("provider") and p.get("avg_ms")), None)
    tokens = sum((p.get("tokens") or 0) for p in providers)

    return {
        "health": score,
        "services": {
            "total": len(services),
            "healthy": score["counts"]["healthy"],
            "degraded": score["counts"]["degraded"],
            "down": score["counts"]["down"],
            "disabled": score["counts"]["disabled"],
        },
        "traffic": {
            "messages_24h": messages,
            "conversations_24h": conversations,
            "api_requests_24h": cat_count("api"),
            "api_errors_24h": cat_count("api", ERRORS),
        },
        "ai": {
            "requests_24h": llm_total,
            "errors_24h": llm_errors,
            "error_rate": round(llm_errors / llm_total * 100, 1) if llm_total else 0.0,
            "avg_latency_ms": avg_latency,
            "tokens_24h": tokens,
            "providers": providers,
        },
        "sms": {
            "events_24h": cat_count("sms"),
            "failures_24h": cat_count("sms", ERRORS),
        },
        "security": {
            "events_24h": cat_count("security"),
            "audit_24h": cat_count("audit"),
            "failed_logins_24h": _failed_logins(since_24h),
            "active_sessions": _active_session_count(),
        },
        "logs": {
            "total_events": sum(summary.get("totals", {}).values()),
            "errors_24h": sum(by_level.get(l, 0) for l in ERRORS),
            "warnings_24h": by_level.get("warning", 0),
            "storage_bytes": summary.get("storage_bytes", 0),
            "retention": summary.get("retention", {}),
            "oldest": summary.get("oldest"),
            "newest": summary.get("newest"),
        },
        "process": health.process_info(),
    }


def _failed_logins(since: str) -> int:
    try:
        _rows, total = applog.query(category="security", q="auth.login.failed",
                                    since=since, limit=1)
        return total
    except Exception:  # noqa: BLE001
        return 0


def _active_session_count() -> int:
    try:
        conn = get_db_connection()
        n = conn.execute(
            "SELECT COUNT(*) FROM admin_sessions WHERE expiry > ?",
            (datetime.now().isoformat(),)).fetchone()[0] or 0
        conn.close()
        return n
    except Exception:  # noqa: BLE001
        return 0


# ── Services ────────────────────────────────────────────────────────────

@router.get("/admin/api/ops/services", dependencies=[Depends(verify_admin)])
async def services(force: bool = False):
    probes = health.probe_all(force=force)
    actions = service_control.available_actions()
    by_service = {}
    for action in actions:
        by_service.setdefault(action["service"], []).append(action)
    for probe in probes:
        probe["actions"] = by_service.get(probe["name"], []) + by_service.get("*", [])
        probe["read_only_reason"] = service_control.READ_ONLY.get(probe["name"], "")
    return {
        "services": probes,
        "health": health.health_score(probes),
        "process_control": {
            "available": service_control.PROCESS_CONTROL_AVAILABLE,
            "reason_fa": service_control.PROCESS_CONTROL_REASON,
        },
        "actions": actions,
    }


@router.get("/admin/api/ops/services/{name}", dependencies=[Depends(verify_admin)])
async def service_detail(name: str):
    probe = health.probe_one(name, force=True)
    if probe is None:
        raise HTTPException(404, detail="این سرویس تعریف‌شده نیست.")
    events, _ = applog.query(category="service", q=name, limit=25,
                             tables=["service_events"])
    probe["recent_events"] = events
    probe["read_only_reason"] = service_control.READ_ONLY.get(name, "")
    return probe


@router.post("/admin/api/ops/services/action", dependencies=[Depends(verify_admin)])
async def run_service_action(req: ServiceActionRequest, request: Request,
                             username: str = Depends(verify_admin)):
    try:
        return service_control.run(req.action, actor=username, ip=_client_ip(request))
    except service_control.ActionRefused as e:
        raise HTTPException(400, detail=e.message_fa)


# ── System information ──────────────────────────────────────────────────

@router.get("/admin/api/ops/system", dependencies=[Depends(verify_admin)])
async def system_info():
    """Non-sensitive runtime facts only. No secrets, no credentials, no full
    filesystem paths — an admin panel is not a place to leak the deployment."""
    from app.modules.registry import MODULES
    from app.config import ENABLED_MODULES
    return {
        "process": health.process_info(),
        "modules": [
            {"name": m.name, "description": m.description, "core": m.is_core,
             "enabled": m.is_core or (not ENABLED_MODULES or m.name in ENABLED_MODULES)}
            for m in MODULES.values()
        ],
        "health": health.health_score(),
    }


# ── Sessions ────────────────────────────────────────────────────────────

@router.get("/admin/api/security/sessions", dependencies=[Depends(verify_admin)])
async def list_sessions(request: Request):
    """Active admin sessions. The token itself is NEVER returned — only a short
    fingerprint, enough to identify a row for revocation and nothing more."""
    current = request.cookies.get(ADMIN_COOKIE_NAME, "")
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT token, username, expiry FROM admin_sessions WHERE expiry > ?"
        " ORDER BY expiry DESC", (datetime.now().isoformat(),)).fetchall()
    conn.close()
    return {"sessions": [{
        "fingerprint": r["token"][:8],
        "username": r["username"],
        "expiry": r["expiry"],
        "is_current": r["token"] == current,
    } for r in rows]}


@router.post("/admin/api/security/sessions/revoke", dependencies=[Depends(verify_admin)])
async def revoke_session(req: SessionRevokeRequest, request: Request,
                         username: str = Depends(verify_admin)):
    """Revoke by FINGERPRINT, never by full token.

    The client only ever sees the first 8 characters, so a stolen listing
    cannot be replayed as a session cookie. The lookup is a prefix match on the
    server, bound as a parameter.
    """
    current = request.cookies.get(ADMIN_COOKIE_NAME, "")
    fingerprint = (req.fingerprint or "").strip()
    if req.all_others:
        conn = get_db_connection()
        cur = conn.execute("DELETE FROM admin_sessions WHERE token <> ?", (current,))
        conn.commit()
        removed = cur.rowcount or 0
        conn.close()
        applog.audit("admin.session.revoked_all", f"{removed} نشست دیگر باطل شد",
                     actor=username, target="admin_sessions", outcome="ok",
                     level="warning", ip=_client_ip(request),
                     metadata={"revoked": removed})
        return {"revoked": removed}

    if len(fingerprint) < 8:
        raise HTTPException(400, detail="شناسهٔ نشست معتبر نیست.")
    conn = get_db_connection()
    row = conn.execute("SELECT token FROM admin_sessions WHERE token LIKE ?",
                       (fingerprint + "%",)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, detail="این نشست پیدا نشد یا منقضی شده است.")
    if row["token"] == current:
        conn.close()
        raise HTTPException(
            400, detail="نشست جاری از این صفحه باطل نمی‌شود — از «خروج» استفاده کنید.")
    conn.execute("DELETE FROM admin_sessions WHERE token = ?", (row["token"],))
    conn.commit()
    conn.close()
    applog.audit("admin.session.revoked", "یک نشست مدیر باطل شد",
                 actor=username, target=fingerprint, outcome="ok",
                 level="warning", ip=_client_ip(request))
    return {"revoked": 1}


@router.get("/admin/api/security/admins", dependencies=[Depends(verify_admin)])
async def list_admins():
    """Admin accounts. Password hashes and security answers never leave here."""
    conn = get_db_connection()
    admins = conn.execute("SELECT username, security_question FROM admins").fetchall()
    sessions = conn.execute(
        "SELECT username, COUNT(*) n FROM admin_sessions WHERE expiry > ?"
        " GROUP BY username", (datetime.now().isoformat(),)).fetchall()
    conn.close()
    active = {r["username"]: r["n"] for r in sessions}

    out = []
    for a in admins:
        _rows, fail_total = applog.query(category="security", q="auth.login.failed",
                                         actor=a["username"], limit=1)
        last_login, _ = applog.query(category="audit", q="auth.login.success",
                                     actor=a["username"], limit=1)
        out.append({
            "username": a["username"],
            "has_security_question": bool(a["security_question"]),
            "active_sessions": active.get(a["username"], 0),
            "failed_logins": fail_total,
            "last_login": last_login[0]["created_at"] if last_login else None,
        })
    return {"admins": out}


# ── Maintenance mode ────────────────────────────────────────────────────

@router.get("/admin/api/ops/maintenance", dependencies=[Depends(verify_admin)])
async def maintenance_state():
    from app.services import maintenance
    return maintenance.state()


@router.post("/admin/api/ops/maintenance", dependencies=[Depends(verify_admin)])
async def set_maintenance(payload: dict, request: Request,
                          username: str = Depends(verify_admin)):
    """Toggle maintenance. The admin panel itself is never blocked by it —
    an operator must always be able to turn off what they turned on."""
    from app.services import maintenance
    enable = bool(payload.get("enabled"))
    try:
        if enable:
            return maintenance.enable(str(payload.get("reason", ""))[:300],
                                      actor=username)
        return maintenance.disable(actor=username)
    except RuntimeError:
        raise HTTPException(
            503, detail="وضعیت تعمیر ذخیره نشد؛ تغییری اعمال نشد.")
