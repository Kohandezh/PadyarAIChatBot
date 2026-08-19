"""OTP verification (SMS sign-up / sign-in step) — public API + /verify page.

PORTABLE MODULE. Nothing here is INOTEX-specific: the brand name, mark,
palette and companion avatar all come from settings (see `branding()` below),
so the same files drop into any Padyar-based installation — see
docs/engineering/OTP_MODULE.md and scripts/export-otp-module.py.

The page is an authentication step, not part of the chat skeleton: it is a
standalone route. It is not linked from the public chat navigation; flows that
need verification link to it.
"""
import json
import os
import shutil
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.config import BASE_DIR, logger
from app.auth.security import check_rate_limit, verify_admin
from app.services import otp as otp_service

router = APIRouter()

# Per-install branding. Defaults reproduce this installation's look, so an
# install that never touches these settings sees no change; another install
# overrides them from its own admin settings table instead of editing code.
_BRAND_DEFAULTS = {
    "otp_brand_name": "INOTEX",
    "otp_brand_mark": (
        '<svg viewBox="0 0 64 64" width="30" height="30">'
        '<path d="M32 3 57 17.5v29L32 61 7 46.5v-29Z" fill="#1E2D52" stroke="#2D5CA7"'
        ' stroke-width="3" stroke-linejoin="round" />'
        '<path d="M32 17 45 24.5v15L32 47 19 39.5v-15Z" fill="#2D5CA7" />'
        '<path d="M32 17 45 24.5 32 32 19 24.5Z" fill="#5B8AD9" />'
        '<path d="M32 47 45 39.5v-6.2L32 40.8Z" fill="#FCB715" />'
        '</svg>'
    ),
    # Companion avatar: empty atlas URL = no companion at all, which is the
    # correct default for an install that has no character of its own.
    "otp_companion_atlas": "/static/otp/pet/inotex-pose-atlas-hd.webp",
    "otp_companion_cell": "512",
    # Palette — the CSS custom properties the stylesheet already consumes.
    "otp_color_primary": "#FCB715",
    "otp_color_primary_hover": "#FEBE27",
    "otp_color_blue": "#2D5CA7",
    "otp_color_navy": "#1E2D52",
    "otp_color_teal": "#04A584",
    "otp_color_background": "#000000",
    "otp_background_image": "/themes/inotex/static/bg-bricks.jpg",
}


def branding() -> dict:
    from app.db.queries import get_setting
    return {key: (get_setting(key, default) or default) for key, default in _BRAND_DEFAULTS.items()}


class OtpRequestBody(BaseModel):
    destination: str = Field(..., min_length=5, max_length=32)
    first_name: str = Field("", max_length=60)
    last_name: str = Field("", max_length=60)
    job: str = Field("", max_length=80)
    position: str = Field("", max_length=80)
    interests: str = Field("", max_length=200)


class OtpVerifyBody(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=64)
    code: str = Field(..., min_length=1, max_length=16)


class OtpResendBody(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=64)


class VisitPlanBody(BaseModel):
    """A verified challenge, or the raw profile fields, or neither.

    Neither is valid and returns the generic plan — the planner is useful to a
    visitor who never registered, so it must not require an identity.
    """
    challenge_id: str = Field("", max_length=64)
    job: str = Field("", max_length=80)
    position: str = Field("", max_length=80)
    interests: str = Field("", max_length=200)
    lang: str = Field("fa", max_length=8)


@router.post("/api/auth/otp/request")
async def otp_request(body: OtpRequestBody, request: Request):
    from app.services.maintenance import guard as _maintenance_guard
    _maintenance_guard()
    check_rate_limit(request)  # per-IP, same window as /chat
    try:
        return otp_service.request_challenge(
            body.destination, body.first_name, body.last_name,
            body.job, body.position, body.interests,
        )
    except otp_service.OtpError as e:
        raise HTTPException(status_code=e.status, detail=e.public)


@router.post("/api/auth/otp/verify")
async def otp_verify(body: OtpVerifyBody, request: Request):
    check_rate_limit(request)
    ok, message = otp_service.verify(body.challenge_id, body.code)
    if ok:
        # The profile is returned only on success, and only the display name —
        # the phone number stays masked everywhere the browser can see it.
        profile = otp_service.profile_for(body.challenge_id)
        return {"verified": True, "message": message, "profile": profile}
    # Generic public error — the reason detail stays in the audit log.
    raise HTTPException(status_code=400, detail=message)


@router.post("/api/auth/otp/resend")
async def otp_resend(body: OtpResendBody, request: Request):
    check_rate_limit(request)
    try:
        return otp_service.resend(body.challenge_id)
    except otp_service.OtpError as e:
        raise HTTPException(status_code=e.status, detail=e.public)


class ProfileUpdateBody(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=64)
    job: str = Field("", max_length=80)
    position: str = Field("", max_length=80)
    interests: str = Field("", max_length=400)


@router.get("/api/registration/options")
async def registration_options(lang: str = "fa"):
    """The job list, interest list and checkboxes the form renders.

    Served from the taxonomy file, never hardcoded in the form, so replacing
    that file changes what visitors can pick without touching the frontend.
    """
    from app.services import taxonomy
    return taxonomy.form_options("en" if lang.lower().startswith("en") else "fa")


@router.post("/api/auth/profile")
async def update_profile(body: ProfileUpdateBody, request: Request):
    """Let a verified visitor correct their work profile and re-plan.

    Only the descriptive fields move: name and phone are fixed at verification
    and cannot be edited from the browser. An unverified (or unknown) challenge
    is refused, so this cannot be used to write into someone else's row.
    """
    check_rate_limit(request)
    ok = otp_service.update_profile(
        body.challenge_id, body.job, body.position, body.interests
    )
    if not ok:
        raise HTTPException(status_code=403, detail="این نشست معتبر نیست.")
    return {"updated": True, "profile": otp_service.profile_for(body.challenge_id)}


@router.post("/api/visit-plan")
async def visit_plan_endpoint(body: VisitPlanBody, request: Request):
    """Which official INOTEX sections match this visitor's work and interests.

    When a verified `challenge_id` is supplied the stored profile wins over the
    fields in the body: the server already knows what the visitor typed at
    registration, and that copy cannot be edited from the browser.
    """
    check_rate_limit(request)
    from app.services import visit_plan as planner

    profile = {"job": body.job, "position": body.position, "interests": body.interests}
    if body.challenge_id:
        stored = otp_service.profile_for(body.challenge_id)  # {} of blanks unless verified
        if any(stored.get(k) for k in ("job", "position", "interests")):
            profile = {k: stored.get(k, "") for k in ("job", "position", "interests")}

    lang = "en" if body.lang.lower().startswith("en") else "fa"
    return planner.recommend(profile, lang)


@router.get("/api/auth/registration-status")
async def registration_status():
    """Whether the Smart Visit entry point should be offered at all.

    Public and deliberately thin: it reveals only the on/off switch and the
    configured code length — never the provider account or whether the
    gateway credentials exist.
    """
    from app.db.queries import get_setting
    return {
        "enabled": get_setting("registration_enabled", "false") == "true",
        "otp_length": otp_service.OTP_LENGTH,
    }


@router.get("/api/auth/otp/status/{challenge_id}")
async def otp_status(challenge_id: str):
    """Timer reconciliation after a page refresh (unguessable id)."""
    try:
        return otp_service.get_status(challenge_id)
    except otp_service.OtpError as e:
        raise HTTPException(status_code=e.status, detail=e.public)


@router.get("/verify", response_class=HTMLResponse)
async def otp_page():
    """Serve the verification page with this install's branding injected.

    Uses the project's existing placeholder-replacement pattern for raw HTML
    pages (same approach as the public chat UI) rather than a template engine,
    so the module stays copy-deployable into installs that render their public
    pages differently.
    """
    path = os.path.join(BASE_DIR, "templates", "otp", "verify.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    b = branding()
    companion = b["otp_companion_atlas"].strip()
    brand_css = (
        "<style>:root{"
        f"--inotex-primary:{b['otp_color_primary']};"
        f"--inotex-yellow-light:{b['otp_color_primary_hover']};"
        f"--inotex-blue:{b['otp_color_blue']};"
        f"--inotex-navy:{b['otp_color_navy']};"
        f"--inotex-teal:{b['otp_color_teal']};"
        f"--inotex-background:{b['otp_color_background']};"
        "}"
        f"body{{background-image:url('{b['otp_background_image']}');}}"
        + ("" if companion else ".pet-slot{display:none;}")
        + "</style>"
    )
    config = (
        "<script>window.OTP_CONFIG="
        f'{{"companionAtlas":"{companion}","companionCell":{b["otp_companion_cell"]}}};'
        "</script>"
    )

    html = html.replace("<!-- BRAND_CSS -->", brand_css)
    html = html.replace("<!-- BRAND_CONFIG -->", config)
    html = html.replace("<!-- BRAND_MARK -->", b["otp_brand_mark"])
    html = html.replace("<!-- BRAND_NAME -->", b["otp_brand_name"])
    return HTMLResponse(html)


# ── Admin: editing the taxonomy ──────────────────────────────────────────
# `data/visit-taxonomy.json` decides what the registration form offers and
# what the visit planner may recommend. This module owns that file, so the
# admin screen that edits it lives here, behind the same admin session as
# every other /admin/api endpoint.

# Persian names for the lists and fields, so a refusal reads like a sentence
# a receptionist can act on rather than a schema error.
_LIST_LABELS = {
    "jobs": "شغل‌ها",
    "positions": "سمت‌ها",
    "interests": "علاقه‌مندی‌ها",
    "flags": "گزینه‌های تیک‌دار",
    "sections": "بخش‌های نمایشگاه",
}
_FIELD_LABELS = {"id": "شناسه", "fa": "عنوان فارسی", "en": "عنوان انگلیسی"}


def _rows_the_loader_would_drop(doc: dict) -> list:
    """Persian reasons for rows the loader would silently skip.

    The loader is deliberately forgiving: a row with an empty label is dropped
    and the rest of the list still ships. That is right for a file drop, but
    wrong for a person: an admin who typed a job title and pressed save must
    not be told "saved" while the row was thrown away. So the save refuses and
    names the row instead.
    """
    problems = []
    for key, label in _LIST_LABELS.items():
        rows = doc.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list):
            problems.append(f"«{label}» باید یک فهرست باشد.")
            continue
        required = ("id", "fa", "en") if key == "sections" else ("id", "fa")
        seen = set()
        for number, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                problems.append(f"«{label}» ردیف {number}: ساختار درستی ندارد.")
                continue
            for field in required:
                if not str(row.get(field, "")).strip():
                    problems.append(
                        f"«{label}» ردیف {number}: {_FIELD_LABELS[field]} خالی است."
                    )
            row_id = str(row.get("id", "")).strip()
            if row_id and row_id in seen:
                problems.append(f"«{label}» ردیف {number}: شناسهٔ «{row_id}» تکراری است.")
            seen.add(row_id)
            keywords = row.get("keywords")
            if key == "sections" and not (isinstance(keywords, list) and keywords):
                problems.append(f"«{label}» ردیف {number}: کلیدواژه‌ها خالی است.")
    return problems


class TaxonomySaveBody(BaseModel):
    # The whole file as text — the friendly editor and the raw editor both
    # send this, so there is exactly one save path and one validation path.
    text: str = Field(..., min_length=2, max_length=500_000)


@router.get("/admin/api/taxonomy", dependencies=[Depends(verify_admin)])
async def get_taxonomy():
    """The file as it is on disk, plus what the running app is actually using."""
    from app.services import taxonomy

    path = taxonomy.TAXONOMY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""

    data, parse_error = None, ""
    if text.strip():
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else None
            if data is None:
                parse_error = "ساختار فایل باید یک شیء JSON باشد."
        except ValueError as e:
            parse_error = str(e)

    live = taxonomy.document()
    return {
        "text": text,
        "data": data,
        "parse_error": parse_error,
        "file_name": os.path.basename(path),
        # What visitors see right now. Differs from the file only when the file
        # is broken and the loader is still serving the last good version.
        "live_version": live["version"],
        "live_counts": {k: len(live.get(k, [])) for k in
                        ("jobs", "positions", "interests", "flags", "sections")},
        "using_fallback": live["version"] == "builtin-minimum",
    }


@router.post("/admin/api/taxonomy", dependencies=[Depends(verify_admin)])
async def save_taxonomy(body: TaxonomySaveBody):
    """Validate first, back up, then replace the file in one atomic step.

    Nothing is written unless the loader would accept the result, so a save can
    never be the reason registration falls back to the built-in minimum.
    """
    from app.services import taxonomy

    try:
        parsed = json.loads(body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"متن واردشده JSON معتبر نیست: {e}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="ساختار فایل باید یک شیء JSON باشد.")

    problems = _rows_the_loader_would_drop(parsed)
    if problems:
        shown = problems[:5]
        if len(problems) > 5:
            shown.append(f"و {len(problems) - 5} مورد دیگر.")
        raise HTTPException(status_code=400, detail="ذخیره نشد — " + " ".join(shown))

    # The product's own validator has the final say: if it would refuse this
    # document, the file must not change.
    if taxonomy._validate(parsed) is None:
        raise HTTPException(
            status_code=400,
            detail="ذخیره نشد — فایل حداقل به یک «بخش نمایشگاه» سالم "
                   "(با شناسه، عنوان فارسی، عنوان انگلیسی و کلیدواژه) نیاز دارد.",
        )

    path = taxonomy.TAXONOMY_PATH
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)

    # Backup before overwrite — same timestamped-sibling pattern as
    # scripts/reset-content-to-defaults.py, so the previous file is one
    # rename away if an edit turns out to be wrong.
    backup_name = ""
    root, ext = os.path.splitext(path)
    if os.path.exists(path):
        backup = f"{root}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        shutil.copy2(path, backup)
        backup_name = os.path.basename(backup)

    # Re-serialise rather than writing the request body verbatim. The friendly
    # editor posts compact JSON, which would collapse the whole taxonomy onto
    # one line — unreadable to hand-edit and useless in a diff. Pretty-printing
    # the already-validated document also gives the raw editor a canonical
    # format, so two admins editing the same file produce comparable output.
    text = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".visit-taxonomy-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Atomic on POSIX: a crash leaves either the old file or the new one,
        # never a truncated taxonomy.
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    # Keep the 10 most recent backups and drop the rest. An operator tuning the
    # form during an event saves many times a day, and nothing else ever cleans
    # data/ up. The timestamp in the name sorts oldest-first as plain text, so
    # sorting by name is sorting by age.
    #
    # Housekeeping must never cost us the save: the new taxonomy is already on
    # disk and live, so a file we cannot delete is logged and forgotten.
    try:
        prefix = os.path.basename(root) + ".backup."
        old = sorted(n for n in os.listdir(folder)
                     if n.startswith(prefix) and n.endswith(ext))
        for name in old[:-10]:
            os.remove(os.path.join(folder, name))
    except Exception as e:
        logger.warning("[taxonomy] could not prune old backups: %s", e)

    # The loader watches the mtime, so the new file is live on the next read.
    live = taxonomy.document()
    logger.info(
        "[taxonomy] saved from admin panel — v%s, %d sections (backup: %s)",
        live["version"], len(live["sections"]), backup_name or "none",
    )
    return {
        "status": "saved",
        "backup": backup_name,
        "live_version": live["version"],
        "live_counts": {k: len(live.get(k, [])) for k in
                        ("jobs", "positions", "interests", "flags", "sections")},
    }


@router.get("/secure-panel-inotex/settings/taxonomy", response_class=HTMLResponse)
async def admin_taxonomy_page(request: Request):
    """The admin screen. Same session check and login redirect as the other
    admin pages (see app/routers/public.py)."""
    from app.routers.public import _render, _require_admin

    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render(
        "admin/settings_taxonomy.html", request=request, active_page="settings_taxonomy"
    )
