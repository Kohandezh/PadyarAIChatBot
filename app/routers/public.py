import os
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

from app.config import BASE_DIR, is_module_enabled, ENABLED_MODULES
from app.db.queries import get_setting
from app.auth import security
from app.auth.security import client_ip, generate_chat_token, verify_admin


router = APIRouter()

# Use Jinja2 directly — bypasses Starlette's Jinja2Templates which has
# compatibility issues with Jinja2 3.1.x (unhashable type: dict / dict has no split)
_jinja_env = Environment(
    loader=FileSystemLoader(os.path.join(BASE_DIR, "templates")),
    autoescape=True,
    auto_reload=False,
)


def _render(template_name: str, **context) -> HTMLResponse:
    """Render a Jinja2 template and return HTMLResponse.

    Every admin page gets `enabled_modules` so the sidebar can hide links to
    pages an install does not have. Without it, an install whose
    ENABLED_MODULES omits a module still shows its menu entry, and the admin
    lands on a 404.

    Every admin page also gets the maintenance state so layout.html can show
    the persistent warning banner. The banner is NOT module-gated: the state
    is real no matter which module set it (an operator, a backups restore),
    so it must follow the operator onto every authed page — only the
    «خاموش کردن» link inside it gates on the ops module (see layout.html).
    The state() read is exception-safe and excluded from the settings TTL
    cache by key, so this is one fresh, cheap SELECT per low-traffic admin
    page render with no staleness window.
    """
    from app.config import ENABLED_MODULES
    context.setdefault("enabled_modules", ENABLED_MODULES)
    # The panel's own name. This repository deploys to more than one install
    # (inotex, elecomp) from one branch, so a literal name in layout.html
    # would brand every install with one event's identity — the white-label
    # key is the install's display name. RAW value, deliberately not
    # pre-escaped: this Jinja env has autoescape=True, so Jinja escapes it —
    # pre-escaping here would double-escape in the sidebar. (The theme env is
    # the opposite: see app/services/branding.py.)
    from app.services.branding import get_branding
    context.setdefault("wl_app_name", get_branding()["whitelabel_app_name"])
    context.setdefault("maintenance_on", False)
    try:
        from app.services import maintenance
        m = maintenance.state()
        if m.get("enabled"):
            context["maintenance_on"] = True
            context["maintenance_reason"] = m.get("reason", "")
            context["maintenance_by"] = m.get("enabled_by", "")
            context["maintenance_at"] = m.get("enabled_at") or ""
    except Exception:
        # state() already swallows its own errors; this belt-and-braces wrap
        # keeps _render total no matter what — a broken banner read must
        # never take an admin page down.
        context["maintenance_on"] = False
    template = _jinja_env.get_template(template_name)
    html = template.render(context)
    return HTMLResponse(html)


def admin_js_version(*names: str) -> str:
    """Cache-buster for an admin page's own JavaScript.

    Same reasoning as _asset_version below, applied to the admin panel, which
    never had it. Nothing sends Cache-Control for /static, so browsers cache it
    heuristically: a page was shipped whose HTML had a new button while the
    browser kept running the previous script, so the button existed, was bound
    to nothing, and did nothing when clicked — indistinguishable from a broken
    feature. Stamping the newest mtime of the page's scripts forces a refetch.

    Falls back to "0" if nothing can be read: a missing buster costs freshness,
    it must never break the page.
    """
    newest = 0
    for name in names:
        try:
            newest = max(newest, int(os.path.getmtime(
                os.path.join(BASE_DIR, "static", "admin", "js", name))))
        except OSError:
            continue
    return str(newest)


def _asset_version(theme_name: str) -> str:
    """Cache-buster token for the chat stylesheets.

    Browsers cache /static and /themes assets aggressively (no Cache-Control is
    sent, so they heuristically cache), which meant customers kept seeing the
    OLD CSS after a theme upgrade. Stamping the newest mtime of the stylesheets
    onto their <link> href makes every upgrade produce a new URL, so the browser
    is forced to refetch. Falls back to "0" if the files are unreadable — a
    missing buster only costs freshness, it must never break the page.
    """
    paths = [
        os.path.join(BASE_DIR, "static", "chat", "base.css"),
        os.path.join(BASE_DIR, "themes", theme_name, "static", "style.css"),
        os.path.join(BASE_DIR, "static", "chat", "core.js"),
    ]
    newest = 0
    for path in paths:
        try:
            newest = max(newest, int(os.path.getmtime(path)))
        except OSError:
            continue
    return str(newest)


# --- Public Pages ---

@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # Every render mints a fresh signed chat token — i.e. a fresh rate-limit
    # identity — so the mint path gets its own generous per-IP fence. Renders
    # are file reads and cache hits (cheap), but unbounded hammering would
    # mint unbounded identities. 120/min is far above any human or kiosk, and
    # this is defense in depth: the /chat IP backstop already fences what
    # minted tokens can spend. PAGE_RATE_LIMIT is read off the security module
    # at call time so the enforcing module stays the one place tests tune.
    security.check_rate_limit(
        request,
        key=f"page:{client_ip(request) or 'unknown'}",
        limit=security.PAGE_RATE_LIMIT,
    )
    try:
        from app.services.themes import get_active_theme, render_theme_index
        from app.services.branding import chat_branding_context
        active_theme = get_active_theme()
        token = generate_chat_token()
        # Branding is baked into the (cached) shell: same bytes for every
        # visitor, so the only per-visitor splice stays the token. The cache
        # key carries wl_cache_key for exactly this reason — see
        # app/services/themes.py.
        context = {
            "theme_name": active_theme,
            "chat_token": token,
            "asset_version": _asset_version(active_theme),
        }
        context.update(chat_branding_context())
        html = render_theme_index(active_theme, context)
        return html
    except Exception:
        # Fallback to root index.html
        try:
            with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as f:
                html = f.read()
            token = generate_chat_token()
            html = html.replace("<!-- CHAT_TOKEN -->", f'<meta name="chat-token" content="{token}">')
            return html
        except FileNotFoundError:
            return "index.html not found."


# --- Public Data API (DB is the single source of truth) ---
# The chat frontend reads these endpoints for its suggested-question list.
# Dataset/questions live in the database only — there are no JSON data files.

# Defined as sync `def` (not `async def`): they run blocking database queries,
# so FastAPI runs them in a threadpool instead of blocking the event loop —
# important since the chat frontend hits these frequently.
@router.get("/api/dataset")
def api_dataset():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    # Insertion order, not alphabetical: the chat renders the first entries as
    # its one-click question menu, so the curated order is what users see.
    rows = conn.execute(
        # `position`, not `rowid`. rowid is a SQLite pseudo-column and does not
        # exist in PostgreSQL, so this endpoint — the one the public chat UI
        # loads its knowledge base from — returned a hard 500 in production.
        # COALESCE keeps the two backends agreeing on where an unpositioned row
        # lands: PostgreSQL sorts NULL last on ASC, SQLite sorts it first.
        # `id` is the tiebreak so the order is total, never arbitrary.
        'SELECT id, title, text, video_url, title_en, text_en FROM dataset'
        ' ORDER BY COALESCE(position, 2147483647), id'
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/questions")
def api_questions():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT id, question, dataset_id, video_url FROM questions ORDER BY id'
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Admin Pages (Jinja2 templates, server-side auth) ---

async def _require_admin(request: Request):
    """Check admin session; return RedirectResponse to login on failure, or None on success."""
    try:
        await verify_admin(request)
        return None
    except Exception:
        return RedirectResponse(url="/secure-panel-inotex/login", status_code=303)


def _require_module(module_name: str) -> None:
    """404 unless this install enabled the module — mirrors the sidebar,
    which hides the link, so only a hand-typed URL ever lands here.

    404, not a redirect: the module's data APIs already 404 and its router
    (leads/tts/otp pattern) unmounts entirely, so this keeps one semantic
    everywhere — "not part of this install". A redirect to admin home would
    mask a stale bookmark and send the admin hunting for a page that is
    not there. The invariant "no visible link ever 404s" holds because each
    call site's module name is copied verbatim from the sidebar guard in
    layout.html.
    """
    if not is_module_enabled(module_name):
        raise HTTPException(status_code=404, detail="Module not enabled")


@router.get("/secure-panel-inotex/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    redirect = await _require_admin(request)
    if redirect is None:
        return RedirectResponse(url="/secure-panel-inotex", status_code=303)
    return _render("admin/login.html", request=request)


@router.get("/secure-panel-inotex", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/dashboard.html", request=request, active_page="dashboard",
                   js_version=admin_js_version("dashboard.js", "resources.js"))


@router.get("/secure-panel-inotex/manage-datasets", response_class=HTMLResponse)
async def admin_datasets(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/dataset.html", request=request, active_page="dataset")


@router.get("/secure-panel-inotex/manage-questions", response_class=HTMLResponse)
async def admin_questions(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/questions.html", request=request, active_page="questions")


@router.get("/secure-panel-inotex/synonyms", response_class=HTMLResponse)
async def admin_synonyms(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/synonyms.html", request=request, active_page="synonyms")


@router.get("/secure-panel-inotex/themes", response_class=HTMLResponse)
async def admin_themes(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/themes.html", request=request, active_page="themes")


@router.get("/secure-panel-inotex/infrastructure/database", response_class=HTMLResponse)
async def admin_infra_database(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("infra")
    return _render("admin/infra_database.html", request=request,
                   active_page="infra_database")


@router.get("/secure-panel-inotex/infrastructure/storage", response_class=HTMLResponse)
async def admin_infra_storage(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("infra")
    return _render("admin/infra_storage.html", request=request,
                   active_page="infra_storage")


@router.get("/secure-panel-inotex/ops", response_class=HTMLResponse)
async def admin_ops_dashboard(request: Request):
    # Page shell only — the maintenance banner it once carried lives in
    # layout.html and renders on every admin page regardless of this gate.
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("ops")
    return _render("admin/ops_dashboard.html", request=request, active_page="ops")


@router.get("/secure-panel-inotex/ops/services", response_class=HTMLResponse)
async def admin_ops_services(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("ops")
    return _render("admin/ops_services.html", request=request, active_page="ops_services")


@router.get("/secure-panel-inotex/security/sessions", response_class=HTMLResponse)
async def admin_security_sessions(request: Request):
    # Sessions sit under the ops module per the sidebar grouping; if session
    # management ever becomes its own module, layout.html moves with it.
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("ops")
    return _render("admin/security_sessions.html", request=request,
                   active_page="security_sessions")


@router.get("/secure-panel-inotex/logs", response_class=HTMLResponse)
async def admin_logs(request: Request):
    """The one explorer serves every category via ?category= — twelve
    near-identical templates would be twelve places to fix a bug."""
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("logs")
    from app.services.applog import CATEGORIES
    preset = request.query_params.get("category", "")
    title = CATEGORIES.get(preset, "همهٔ رخدادها")
    return _render("admin/logs.html", request=request, active_page="logs",
                   categories=CATEGORIES, preset_category=preset,
                   page_title=f"لاگ‌ها — {title}")


@router.get("/secure-panel-inotex/logs/overview", response_class=HTMLResponse)
async def admin_logs_overview(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("logs")
    return _render("admin/logs_overview.html", request=request,
                   active_page="logs_overview")


# ── AI provider control plane pages ─────────────────────────────────────

@router.get("/secure-panel-inotex/ai/providers", response_class=HTMLResponse)
async def admin_ai_providers(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/ai_providers.html", request=request, active_page="ai_providers")


@router.get("/secure-panel-inotex/ai/models", response_class=HTMLResponse)
async def admin_ai_models(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/ai_models.html", request=request, active_page="ai_models")


@router.get("/secure-panel-inotex/ai/routing", response_class=HTMLResponse)
async def admin_ai_routing(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/ai_routing.html", request=request, active_page="ai_routing")


@router.get("/secure-panel-inotex/ai/usage", response_class=HTMLResponse)
async def admin_ai_usage(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/ai_usage.html", request=request, active_page="ai_usage")


@router.get("/secure-panel-inotex/ai/debug", response_class=HTMLResponse)
async def admin_ai_debug(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/ai_debug.html", request=request, active_page="ai_debug")


@router.get("/secure-panel-inotex/logs/settings", response_class=HTMLResponse)
async def admin_logs_settings(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    _require_module("logs")
    from app.services.applog import CATEGORIES
    return _render("admin/logs_settings.html", request=request,
                   active_page="logs_settings", categories=CATEGORIES)


@router.get("/secure-panel-inotex/settings", response_class=HTMLResponse)
async def admin_settings(request: Request):
    # Settings is split into sub-pages; land on the account page.
    return RedirectResponse(url="/secure-panel-inotex/settings/account", status_code=303)


@router.get("/secure-panel-inotex/settings/account", response_class=HTMLResponse)
async def admin_settings_account(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/settings_account.html", request=request, active_page="settings_account")


@router.get("/secure-panel-inotex/settings/ai", response_class=HTMLResponse)
async def admin_settings_ai(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/settings_ai.html", request=request, active_page="settings_ai")


@router.get("/secure-panel-inotex/settings/sms", response_class=HTMLResponse)
async def admin_settings_sms(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/settings_sms.html", request=request, active_page="settings_sms")


@router.get("/secure-panel-inotex/settings/backup", response_class=HTMLResponse)
async def admin_settings_backup(request: Request):
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    # On PostgreSQL, the backup list/upload on this page is SQLite-era dead
    # weight: the files it would list (.db sets) do not exist and the upload
    # cannot restore a pg_dump archive. Only the schedule belongs here; the
    # list, verify, download and restore of PostgreSQL backups live on the
    # infrastructure page. The template hides the dead section on this flag.
    from app.config import DB_BACKEND
    return _render("admin/settings_backup.html", request=request,
                   active_page="settings_backup",
                   backup_engine=("postgres" if DB_BACKEND == "postgres" else "sqlite"))


@router.get("/secure-panel-inotex/settings/branding", response_class=HTMLResponse)
async def admin_settings_branding(request: Request):
    # Branding is core, never module-gated: every install has a name and
    # colors, whether or not it bought any optional module.
    redirect = await _require_admin(request)
    if redirect:
        return redirect
    # Server-rendered field values (raw — this env autoescapes) so the form
    # is filled on first paint, no JS required; initBranding() re-reads via
    # the API like every other settings page.
    from app.services.branding import get_branding
    return _render("admin/settings_branding.html", request=request,
                   active_page="settings_branding", branding=get_branding())


# --- Public APIs ---

@router.get("/api/health")
async def health_check():
    """Liveness: the process answers. One word, deliberately.

    This endpoint is UNAUTHENTICATED and reachable from the internet, so its
    body is a deliberate minimum. It used to also return the enabled module
    list, the AI toggle, the knowledge version and the dataset size — a free
    reconnaissance map of the attack surface ("registration is on, voice is
    on, AI fallback is off") for anyone who typed the URL. Those diagnostics
    now live behind admin auth at /admin/api/ops/health.

    It also used to run a dataset COUNT(*): a load balancer, a Docker
    HEALTHCHECK and every deploy script poll this endpoint, so a flood of
    cheap GETs (or a wedged database) exhausted the connection pool and took
    the panel down with the chat. Now it touches NOTHING. Anything that must
    inspect the database or the AI providers belongs on /api/ready, which
    exists for exactly that.
    """
    return {"status": "ok"}


@router.get("/api/ready")
async def readiness_check(deep: bool = False, request: Request = None):
    """Readiness: is the retrieval layer actually able to answer?

    ``deep=true`` additionally probes the external AI endpoint (never done
    in the request path). Returns 503 while the local layer is not ready so
    an orchestrator holds traffic until the index is built.

    The deep probe is admin-only: it makes the server call the external
    provider, and an anonymous caller able to trigger outbound traffic on
    demand is a free abuse relay. Unauthenticated requests silently get the
    shallow answer an orchestrator needs.
    """
    from fastapi.responses import JSONResponse
    from app.services.providers import local_provider, external_provider

    if deep:
        try:
            await verify_admin(request)
        except HTTPException:
            deep = False

    local = local_provider.health_check()
    body = {
        "status": "ready" if local.available else "not_ready",
        "knowledge_version": get_setting("knowledge_version", "unversioned"),
        "providers": [local.as_dict()],
    }
    if deep:
        ext = await external_provider.health_check()
        body["providers"].append(ext.as_dict())
    return JSONResponse(body, status_code=200 if local.available else 503)


@router.get("/api/voice-status")
async def voice_status():
    tts = get_setting('tts_enabled', 'true') == 'true'
    lang = get_setting('default_chat_lang', 'fa')
    if not is_module_enabled("voice"):
        return {"voice_enabled": False, "tts_enabled": tts, "default_lang": lang}
    enabled = get_setting('voice_enabled', 'true') == 'true'
    return {"voice_enabled": enabled, "tts_enabled": tts, "default_lang": lang}
