"""Admin API for Infrastructure → Database and Infrastructure → Storage.

Three things here are deliberate and must survive later "simplification":

  * The database is chosen by NAME, never by path, and the name is validated
    against `dbadmin._DATABASES` before anything opens a file.
  * The action is looked up in `dbadmin.ACTIONS`, an explicit dict of functions.
    Nothing is ever fetched off a module by a string from the request, so an
    unknown action reaches no code at all — it returns 400 having executed
    nothing.
  * VACUUM requires the operator to have typed the exact confirmation phrase,
    and that is checked HERE as well as in the page. A confirmation enforced
    only in the browser is not a control.

Every error an operator can see is Persian, and no response ever carries a raw
exception message or a filesystem path.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.security import verify_admin
from app.services import applog, dbadmin, storage

router = APIRouter()


def _postgres() -> bool:
    """Post-cutover the SQLite endpoints describe a store the app no longer
    uses. Serving them would show an operator a verified-looking result about
    the wrong database."""
    from app.config import DB_BACKEND
    return DB_BACKEND == "postgres"


class MaintenanceRequest(BaseModel):
    action: str = ""
    # Only read for the operations listed in dbadmin.DANGEROUS_ACTIONS.
    confirm: str = ""


@router.get("/admin/api/infra/database/pg", dependencies=[Depends(verify_admin)])
async def pg_overview():
    """PostgreSQL-native overview: version, size per schema, pool, activity,
    applied migrations and the allowlisted maintenance actions."""
    if not _postgres():
        raise HTTPException(400, detail="این نصب روی پستگرس اجرا نمی‌شود.")
    from app.services import pg_admin
    data = pg_admin.overview()
    data["actions"] = pg_admin.available_actions()
    return data


@router.get("/admin/api/infra/database/pg/tables", dependencies=[Depends(verify_admin)])
async def pg_tables():
    if not _postgres():
        raise HTTPException(400, detail="این نصب روی پستگرس اجرا نمی‌شود.")
    from app.services import pg_admin
    return {"tables": pg_admin.table_stats(), "indexes": pg_admin.index_stats()}


@router.post("/admin/api/infra/database/pg/maintenance", dependencies=[Depends(verify_admin)])
async def pg_maintenance(payload: dict, username: str = Depends(verify_admin)):
    if not _postgres():
        raise HTTPException(400, detail="این نصب روی پستگرس اجرا نمی‌شود.")
    from app.services import pg_admin
    try:
        return pg_admin.run_action(str(payload.get("action", ""))[:60], actor=username)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.get("/admin/api/infra/database", dependencies=[Depends(verify_admin)])
async def infra_database():
    """Both databases, side by side. There are exactly two and always will be."""
    if _postgres():
        raise HTTPException(400, detail="این نصب روی پستگرس اجرا می‌شود؛ از صفحهٔ «پایگاه داده» استفاده کنید.")
    return {
        "databases": [dbadmin.overview(name) for name in dbadmin.NAMES],
        "actions": dbadmin.action_catalog(),
    }


@router.get("/admin/api/infra/database/{name}/tables",
            dependencies=[Depends(verify_admin)])
async def infra_database_tables(name: str):
    if _postgres():
        raise HTTPException(400, detail="این نصب روی پستگرس اجرا می‌شود؛ از صفحهٔ «پایگاه داده» استفاده کنید.")
    if not dbadmin.is_known(name):
        raise HTTPException(status_code=404, detail=dbadmin.UNKNOWN_DB_FA)
    return {"name": name.strip().lower(), "tables": dbadmin.tables(name)}


@router.post("/admin/api/infra/database/{name}/maintenance",
             dependencies=[Depends(verify_admin)])
async def infra_database_maintenance(name: str, req: MaintenanceRequest,
                                     username: str = Depends(verify_admin)):
    if _postgres():
        raise HTTPException(400, detail="این نصب روی پستگرس اجرا می‌شود؛ از صفحهٔ «پایگاه داده» استفاده کنید.")
    action = (req.action or "").strip()

    # Allowlist first: an unknown action must not even learn whether the
    # database name was valid, and must run nothing.
    runner = dbadmin.ACTIONS.get(action)
    if runner is None:
        applog.security("admin.database.unknown_action",
                        "عملیات ناشناخته روی پایگاه داده درخواست شد",
                        level="warning", actor=username, actor_type="admin",
                        target=str(name)[:80], outcome="denied",
                        subcategory="database",
                        metadata={"requested_action": str(action)[:80]})
        raise HTTPException(status_code=400, detail=dbadmin.UNKNOWN_ACTION_FA)

    if not dbadmin.is_known(name):
        raise HTTPException(status_code=404, detail=dbadmin.UNKNOWN_DB_FA)

    if action in dbadmin.DANGEROUS_ACTIONS:
        expected = dbadmin.confirm_phrase(name)
        if (req.confirm or "").strip() != expected:
            applog.audit("admin.database." + action,
                         message="عبارت تأیید وارد نشده بود", actor=username,
                         target=name.strip().lower(), outcome="denied",
                         level="warning", subcategory="database")
            raise HTTPException(
                status_code=400,
                detail="برای انجام این عملیات باید عبارت تأیید را دقیقاً وارد کنید.")

    # The service never raises: a failed operation is a report, not a transport
    # error, and the page shows its Persian message either way.
    return runner(name, actor=username)


@router.get("/admin/api/infra/storage", dependencies=[Depends(verify_admin)])
async def infra_storage():
    return storage.overview()
