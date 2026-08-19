"""Maintenance mode — shared state, so every process observes the same reality.

WHY THE STATE LIVES IN POSTGRESQL
---------------------------------
A module-level flag would be per-process. Under gunicorn with several workers,
turning maintenance ON in one worker would leave the others happily accepting
writes — which is precisely the failure the mode exists to prevent during a
restore. The row in `app.settings` is the single source of truth, and every
process reads it per request.

That read is deliberately NOT cached. Caching would create a window in which a
worker keeps writing after maintenance is on; correctness beats a saved query
on a table this small.

WHAT IT BLOCKS, AND WHAT IT MUST NOT
------------------------------------
Blocked: visitor-facing writes (the chat endpoint, OTP issue/verify).
NOT blocked: the admin panel and its APIs. An operator locked out of the panel
by their own maintenance mode could not turn it off again, and could not watch
the restore they started. Health endpoints stay up for the same reason.
"""
import json
from datetime import datetime, timezone

from app.config import logger
from app.services import applog

_KEY = "maintenance_state"


def _read() -> dict:
    try:
        from app.db.queries import get_setting
        raw = (get_setting(_KEY, "") or "").strip()
        if not raw:
            return {"enabled": False}
        state = json.loads(raw)
        return state if isinstance(state, dict) else {"enabled": False}
    except Exception as e:  # noqa: BLE001 — an unreadable flag must not 500 the app
        logger.error("[maintenance] state unreadable: %s", type(e).__name__)
        return {"enabled": False}


def _write(state: dict) -> bool:
    try:
        from app.db.queries import set_setting
        set_setting(_KEY, json.dumps(state, ensure_ascii=False))
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("[maintenance] state not written: %s", type(e).__name__)
        return False


def state() -> dict:
    s = _read()
    return {
        "enabled": bool(s.get("enabled")),
        "reason": s.get("reason", ""),
        "enabled_by": s.get("enabled_by", ""),
        "enabled_at": s.get("enabled_at"),
    }


def is_enabled() -> bool:
    return bool(_read().get("enabled"))


def enable(reason: str, actor: str = "") -> dict:
    new = {
        "enabled": True,
        "reason": (reason or "")[:300],
        "enabled_by": (actor or "system")[:120],
        "enabled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not _write(new):
        raise RuntimeError("maintenance state could not be persisted")
    applog.audit("admin.maintenance.enabled", "حالت تعمیر روشن شد",
                 actor=actor, target="maintenance", outcome="ok",
                 level="warning", metadata={"reason": new["reason"]})
    logger.warning("[maintenance] ENABLED by %s: %s", actor, new["reason"])
    return new


def disable(actor: str = "") -> dict:
    previous = state()
    if not _write({"enabled": False}):
        raise RuntimeError("maintenance state could not be persisted")
    applog.audit("admin.maintenance.disabled", "حالت تعمیر خاموش شد",
                 actor=actor, target="maintenance", outcome="ok",
                 metadata={"was_reason": previous.get("reason")})
    logger.warning("[maintenance] DISABLED by %s", actor)
    return {"enabled": False}


# The controlled response a visitor sees. Persian, no internal detail.
MAINTENANCE_MESSAGE = (
    "سامانه برای نگهداری کوتاه‌مدت در دسترس نیست. لطفاً چند دقیقهٔ دیگر دوباره تلاش کنید."
)


def guard() -> None:
    """Raise 503 when maintenance is on. Call from visitor WRITE paths only."""
    if is_enabled():
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=MAINTENANCE_MESSAGE,
                            headers={"Retry-After": "120"})
