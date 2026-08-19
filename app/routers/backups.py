"""Admin API for Infrastructure → Backups.

Every endpoint is admin-only, and three things here are deliberate:

  * No filesystem path and no raw exception text ever reaches the browser. The
    operator gets a Persian sentence; the detail goes to the log store, where
    only an operator can read it. A path in an error is a map of the server.
  * Restore requires the operator to TYPE `RESTORE BACKUP <id>`. It is compared
    against the id from the URL, so a copied-and-pasted confirmation for a
    different backup is refused. A mismatch returns 400 and nothing at all
    happens — the check runs before any file is touched.
  * Download only ever serves a file that the set's own manifest lists, resolved
    through app/services/backup_center.py's allowlist. There is no endpoint
    anywhere here that accepts a path from the browser.

The heavy endpoints are plain `def`, not `async def`: they run blocking SQLite
and disk work, so FastAPI hands them to the threadpool instead of stalling the
event loop for every other request in the app.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.auth.security import verify_admin
from app.config import logger
from app.routers.public import _render, _require_admin
from app.services import applog, backup_center

router = APIRouter()


def _engine():
    """Which backup implementation is authoritative.

    Post-cutover the SQLite backup service backs up files the application no
    longer writes — a backup that restores nothing. On PostgreSQL every call
    routes to pg_backup (pg_dump custom archives).
    """
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        from app.services import pg_backup
        return pg_backup, True
    from app.services import backup_center
    return backup_center, False

# One place for the operator-facing strings, so a message cannot drift between
# two endpoints that mean the same thing.
FA_NOT_FOUND = "نسخهٔ پشتیبان پیدا نشد."
FA_GENERIC = "انجام نشد. جزئیات در بخش گزارش‌ها ثبت شد."
FA_BAD_CONFIRM = "عبارت تأیید درست نیست. هیچ تغییری انجام نشد."
FA_FILE_GONE = "این فایل در نسخهٔ پشتیبان وجود ندارد."


class RestoreRequest(BaseModel):
    """The typed confirmation. `confirm` must equal `RESTORE BACKUP <id>`."""
    confirm: str = ""


def _fail(status: int, message: str, event: str, actor: str = "",
          target: str = "", exc: Exception = None):
    """Log the real reason, return the safe sentence."""
    if exc is not None:
        applog.exception("backup", event, exc, message,
                         actor=actor, target=target, outcome="error")
    return HTTPException(status_code=status, detail=message)


# ── Page ────────────────────────────────────────────────────────────────
# Served from this router so an install only has to wire ONE thing to get the
# feature. `_render`/`_require_admin` are reused from the public router rather
# than re-implemented — there must be one template environment, not two.

@router.get("/secure-panel-inotex/infrastructure/backups", response_class=HTMLResponse)
async def infra_backups_page(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/infra_backups.html", request=request,
                   active_page="infra_backups")


# ── API ─────────────────────────────────────────────────────────────────

@router.get("/admin/api/infra/backups", dependencies=[Depends(verify_admin)])
def list_backups():
    from app.services.backup import get_schedule
    try:
        schedule = get_schedule()
    except Exception as exc:  # noqa: BLE001 — the list must render regardless
        logger.warning("Backup schedule unreadable: %s", type(exc).__name__)
        schedule = {}
    return {
        "backups": (_engine()[0].list_backups() if _engine()[1]
                    else backup_center.list_sets()),
        "engine": "postgresql" if _engine()[1] else "sqlite",
        "schedule": schedule,
        "labels": backup_center.ROLE_LABELS,
    }


@router.post("/admin/api/infra/backups", dependencies=[Depends(verify_admin)])
def create_backup(username: str = Depends(verify_admin)):
    try:
        engine, is_pg = _engine()
        return engine.create(actor=username)
    except backup_center.BackupError as exc:
        raise _fail(500, exc.fa, "backup.api.create_failed", username, "", exc)
    except Exception as exc:  # noqa: BLE001
        raise _fail(500, FA_GENERIC, "backup.api.create_failed", username, "", exc)


@router.post("/admin/api/infra/backups/{backup_id}/verify",
             dependencies=[Depends(verify_admin)])
def verify_backup(backup_id: str, username: str = Depends(verify_admin)):
    try:
        engine, _ = _engine()
        return engine.verify(backup_id, actor=username)
    except backup_center.UnknownBackup:
        raise HTTPException(status_code=404, detail=FA_NOT_FOUND)
    except Exception as exc:  # noqa: BLE001
        raise _fail(500, FA_GENERIC, "backup.api.verify_failed", username,
                    backup_id, exc)


@router.get("/admin/api/infra/backups/{backup_id}/download",
            dependencies=[Depends(verify_admin)])
def download_backup(backup_id: str, file: str = "",
                    username: str = Depends(verify_admin)):
    """Serve ONE file from a set — only a name the set's manifest lists.

    `file` is never joined onto a path directly: backup_center.member_path()
    accepts only the fixed member names and re-checks containment, and the name
    additionally has to appear in this set's manifest."""
    rows = [r for r in backup_center.list_sets() if r["backup_id"] == backup_id]
    if not rows:
        raise HTTPException(status_code=404, detail=FA_NOT_FOUND)
    manifest_files = rows[0]["files"]
    if not manifest_files:
        raise HTTPException(status_code=404, detail=FA_NOT_FOUND)

    wanted = file or manifest_files[0]["name"]
    if wanted not in {f["name"] for f in manifest_files}:
        raise HTTPException(status_code=404, detail=FA_FILE_GONE)

    path = backup_center.member_path(backup_id, wanted)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=FA_FILE_GONE)

    applog.audit("admin.backup.download", "دریافت فایل نسخهٔ پشتیبان",
                 actor=username, target=f"{backup_id}/{wanted}", outcome="ok")
    return FileResponse(path, media_type="application/octet-stream",
                        filename=f"{backup_id}_{wanted}")


@router.delete("/admin/api/infra/backups/{backup_id}",
               dependencies=[Depends(verify_admin)])
def delete_backup(backup_id: str, username: str = Depends(verify_admin)):
    try:
        engine, _ = _engine()
        return engine.delete(backup_id, actor=username)
    except backup_center.UnknownBackup:
        raise HTTPException(status_code=404, detail=FA_NOT_FOUND)
    except backup_center.BackupError as exc:
        raise _fail(500, exc.fa, "backup.api.delete_failed", username, backup_id, exc)
    except Exception as exc:  # noqa: BLE001
        raise _fail(500, FA_GENERIC, "backup.api.delete_failed", username,
                    backup_id, exc)


@router.post("/admin/api/infra/backups/{backup_id}/restore",
             dependencies=[Depends(verify_admin)])
def restore_backup(backup_id: str, body: RestoreRequest,
                   username: str = Depends(verify_admin)):
    """Overwrite the live databases from a backup set.

    The confirmation is checked FIRST and against this id — before verify,
    before the safety backup, before anything is written. A wrong phrase costs
    nothing but a 400."""
    expected = f"RESTORE BACKUP {backup_id}"
    if (body.confirm or "").strip() != expected:
        applog.security("admin.backup.restore.bad_confirmation",
                        "عبارت تأیید بازگردانی نادرست بود",
                        actor=username, target=backup_id, outcome="refused")
        raise HTTPException(status_code=400, detail=FA_BAD_CONFIRM)

    engine, is_pg = _engine()
    if is_pg:
        # PostgreSQL: the coordinated lifecycle (maintenance -> safety backup
        # -> pg_restore -> validate -> maintenance off) lives in pg_backup and
        # performs its own confirmation check too.
        try:
            return engine.restore(backup_id, actor=username,
                                  confirmation=body.confirm)
        except engine.AuditUnavailable as exc:
            raise HTTPException(status_code=503, detail=exc.message_fa)
        except engine.BackupError as exc:
            raise HTTPException(status_code=409, detail=exc.message_fa)

    try:
        result = backup_center.restore(backup_id, actor=username)
    except backup_center.UnknownBackup:
        raise HTTPException(status_code=404, detail=FA_NOT_FOUND)
    except backup_center.BackupNotVerified as exc:
        raise HTTPException(status_code=409, detail=exc.fa)
    except backup_center.AuditUnavailable as exc:
        # Fail-closed: no audit trail, no restore. 503, because this is a
        # temporary condition an operator can fix.
        raise HTTPException(status_code=503, detail=exc.fa)
    except backup_center.RestoreFailed as exc:
        raise _fail(500, str(exc) or exc.fa, "backup.api.restore_failed",
                    username, backup_id, exc)
    except Exception as exc:  # noqa: BLE001
        raise _fail(500, FA_GENERIC, "backup.api.restore_failed", username,
                    backup_id, exc)

    result["message"] = (
        "بازگردانی انجام شد. برای اطمینان از اینکه همه‌ی بخش‌ها اطلاعات تازه را"
        " می‌بینند، برنامه را یک‌بار راه‌اندازی مجدد کنید."
    )
    return result
