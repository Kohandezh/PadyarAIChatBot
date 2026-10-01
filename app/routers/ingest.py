"""Admin API of the ingest module (knowledge ingestion, slice S3): upload a
file or give a web page, watch the job, review each proposal, approve.

Every route takes the admin session itself (`Depends(verify_admin)`); a job
or proposal id in the path names a row, it never grants access (SEC-001).
All paths sit under /admin/, so the CSRF middleware covers every mutation
(SEC-002) without a change to app/auth/csrf.py. With the module off this
router is never mounted and every path is a 404 (SEC-003).

A refusal is {"detail": <Persian sentence>, "code": <code>} (SPEC section 6).
The business rules live in app/services/ingest.py; this file only reads the
request, runs the blocking work off the event loop, and schedules the work
that must not hold the response: the job itself, and after an approval the
publish of a new index version (REQ-059). The approve response does not
wait for that publish; `index_version_before` lets the page see it land.
"""
import anyio
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.auth import security
from app.auth.security import verify_admin
from app.services import ingest, ingest_extract, ingest_fetch, search
from app.services.ingest import IngestError

router = APIRouter()

API = "/admin/api/ingest"


def _refuse(exc: IngestError) -> JSONResponse:
    return JSONResponse(status_code=exc.status,
                        content={"detail": exc.message_fa, "code": exc.code, **exc.extra})


def _rate_limit(request: Request, admin: str) -> None:
    # SEC-018: 20 new jobs an hour per admin, files and pages together.
    try:
        security.check_rate_limit(request, key=f"ingest:{admin}", limit=20, window=3600)
    except HTTPException as exc:
        if exc.status_code != 429:
            raise
        raise IngestError(429, "rate_limited")


def _accepted(job: dict, existing: bool, background: BackgroundTasks, *args):
    if existing:
        return {"job": job, "existing": True}
    background.add_task(ingest.run_job, job["id"], *args)
    return JSONResponse(status_code=202, content={"job": job})


def _detect(data: bytes, filename: str) -> str:
    # REQ-025: the type is decided here, inside the request, from the name
    # and the first bytes only (detect_format opens no zip member).
    try:
        return ingest_extract.detect_format(data, filename)
    except ingest_extract.IngestRejected as exc:
        status = 413 if exc.code in ("too_large", "zip_too_big") else 415
        raise IngestError(status, exc.code, exc.message_fa)


@router.post(f"{API}/jobs/upload")
async def upload(request: Request, background: BackgroundTasks, file: UploadFile | None = File(None),
                 admin: str = Depends(verify_admin)):
    try:
        if file is None or not (file.filename or "").strip():
            raise IngestError(422, "no_name")
        # SEC-004: the body middleware reads only Content-Length, so the cap
        # is enforced here on the bytes themselves.
        data = await file.read(ingest_extract.MAX_FILE_BYTES + 1)
        if len(data) > ingest_extract.MAX_FILE_BYTES:
            raise IngestError(413, "too_large")
        fmt = await anyio.to_thread.run_sync(_detect, data, file.filename)
        # SEC-018 limits new jobs. A refused file is not a job, and sending a
        # file again to see its progress answers the job already running, so
        # neither spends the budget (the grandmother test: a wrong pick or a
        # second press must not lock an admin out for an hour).
        running = await anyio.to_thread.run_sync(ingest.active_job_for, data)
        if running is not None:
            return {"job": running, "existing": True}
        _rate_limit(request, admin)
        job, existing = await anyio.to_thread.run_sync(ingest.create_job, "file", file.filename, data, fmt, admin)
    except IngestError as exc:
        return _refuse(exc)
    return _accepted(job, existing, background)


_PAGE_FORMATS = {"application/pdf": "pdf", "text/plain": "txt"}


@router.post(f"{API}/jobs/url")
async def from_url(request: Request, background: BackgroundTasks, payload: dict | None = Body(None),
                   admin: str = Depends(verify_admin)):
    try:
        # Counted before the fetch on purpose: the budget also bounds how
        # often this server connects out to an address an admin types.
        _rate_limit(request, admin)
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url.strip():
            raise IngestError(422, "bad_url", ingest_fetch.MESSAGES_FA["bad_url"])
        # REQ-026: fetched inside the request (at most 20 seconds, under
        # nginx's 120) and off the event loop. Only FetchRejected is a
        # refusal: anything else is a broken install and must surface.
        try:
            page = await anyio.to_thread.run_sync(ingest_fetch.fetch_url, url.strip())
        except ingest_fetch.FetchRejected as exc:
            raise IngestError(504 if exc.code == "timeout" else 422, exc.code, exc.message_fa)
        fmt = _PAGE_FORMATS.get(page.content_type, "html")
        job, existing = await anyio.to_thread.run_sync(
            ingest.create_job, "url", ingest.display_url(page.final_url), page.data, fmt, admin)
    except IngestError as exc:
        return _refuse(exc)
    return _accepted(job, existing, background, page.charset)


@router.get(f"{API}/jobs", dependencies=[Depends(verify_admin)])
async def list_jobs(background: BackgroundTasks, limit: int = ingest.LIST_LIMIT, offset: int = 0):
    ingest.recover_stale()
    ingest.resume_waiting(background)
    return ingest.list_jobs(limit, offset)


@router.get(f"{API}/jobs/{{job_id}}", dependencies=[Depends(verify_admin)])
async def job_detail(job_id: str):
    ingest.recover_stale()
    try:
        return ingest.job_detail(job_id)
    except IngestError as exc:
        return _refuse(exc)


@router.get(f"{API}/jobs/{{job_id}}/proposals", dependencies=[Depends(verify_admin)])
async def list_proposals(job_id: str, status: str = "pending", limit: int = ingest.LIST_LIMIT,
                         offset: int = 0):
    try:
        return ingest.list_proposals(job_id, status, limit, offset)
    except IngestError as exc:
        return _refuse(exc)


@router.post(f"{API}/jobs/{{job_id}}/cancel")
async def cancel(job_id: str, admin: str = Depends(verify_admin)):
    try:
        return ingest.cancel_job(job_id, admin)
    except IngestError as exc:
        return _refuse(exc)


@router.post(f"{API}/jobs/{{job_id}}/approve-seen")
async def approve_seen(job_id: str, background: BackgroundTasks, admin: str = Depends(verify_admin)):
    try:
        result = ingest.approve_seen(job_id, admin)
    except IngestError as exc:
        return _refuse(exc)
    result["index_version_before"] = search.published_index_version()
    if result["approved"]:
        # One publish for the whole batch, after every commit (REQ-061).
        background.add_task(search.reindex_and_publish_until_done)
    return result


@router.post(f"{API}/proposals/seen")
async def mark_seen(payload: dict | None = Body(None), admin: str = Depends(verify_admin)):
    try:
        ids = payload.get("ids") if isinstance(payload, dict) else None
        return {"marked": ingest.mark_seen(ids, admin)}
    except IngestError as exc:
        return _refuse(exc)


@router.put(f"{API}/proposals/{{proposal_id}}")
async def edit(proposal_id: str, payload: dict | None = Body(None), admin: str = Depends(verify_admin)):
    try:
        return ingest.edit_proposal(proposal_id, payload or {}, admin)
    except IngestError as exc:
        return _refuse(exc)


@router.post(f"{API}/proposals/{{proposal_id}}/approve")
async def approve(proposal_id: str, background: BackgroundTasks, admin: str = Depends(verify_admin)):
    try:
        result = ingest.approve(proposal_id, admin)
    except IngestError as exc:
        return _refuse(exc)
    result["index_version_before"] = search.published_index_version()
    background.add_task(search.reindex_and_publish_until_done)
    return result


@router.post(f"{API}/proposals/{{proposal_id}}/reject")
async def reject(proposal_id: str, payload: dict | None = Body(None), admin: str = Depends(verify_admin)):
    try:
        reason = payload.get("reason") if isinstance(payload, dict) else ""
        return ingest.reject(proposal_id, admin, reason)
    except IngestError as exc:
        return _refuse(exc)


@router.get(f"{API}/index-status", dependencies=[Depends(verify_admin)])
async def index_status():
    return {"version": search.published_index_version()}
