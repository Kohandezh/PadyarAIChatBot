from contextlib import asynccontextmanager
import asyncio
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.config import (logger, BASE_DIR, ENABLED_MODULES, COOKIE_SECURE,
                        ADMIN_COOKIE_NAME, SESSION_TIMEOUT_HOURS)
from app.auth.csrf import PROTECTED_PREFIXES
from app.db.connection import init_db
from app.services.search import load_dataset_internal
from app.modules.registry import load_module_routers
from app.routers import public


async def _retention_loop():
    """Enforce log retention every 6 hours.

    A separate task rather than a cron entry so an install has no external
    dependency. It never raises: a purge failure must not cancel the loop and
    silently stop retention forever.
    """
    from app.services import applog
    from app.db import queries
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            applog.purge_expired()
            # chat_logs is the UNREDACTED store — log_chat writes the raw
            # visitor query with no content policy applied — and until the
            # selection tier shipped nothing pruned it. Now that up to five
            # stored turns travel to the AI provider, an operator needs the
            # same dial applog has always had. Default 0 = keep forever, so no
            # existing install loses data by upgrading.
            queries.purge_chat_logs()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("[retention] purge cycle failed: %s", type(e).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # BEFORE anything else. Every setting this checks fails silently — a
    # cookie without Secure still works, a `trust`-auth database still
    # connects — so the only safe moment to complain is before traffic
    # arrives. In production an unsafe setting refuses the boot; in
    # development it only logs. Deliberately NOT guarded by try/except: a
    # production install that cannot verify its own configuration must not
    # start. See app/prodcheck.py.
    from app import prodcheck
    prodcheck.enforce_at_startup(logger)

    init_db()

    # Logging comes up FIRST so anything that fails during the rest of startup
    # has somewhere durable to be recorded. Guarded: a broken log store must
    # never stop the chatbot from booting.
    try:
        from app.services import applog
        applog.ensure_tables()
        applog.purge_expired()
        applog.service("service.started", "برنامه راه‌اندازی شد",
                       target="padyar", outcome="ok",
                       metadata={"modules": sorted(ENABLED_MODULES) or "all"})
    except Exception as e:  # noqa: BLE001
        logger.error("[applog] startup hook failed: %s", type(e).__name__)

    logger.info("Loading dataset...")
    load_dataset_internal()
    try:
        from app.services.search import init_index_version
        init_index_version()
    except Exception as e:  # noqa: BLE001 — freshness stamping must never block boot
        logger.error("[search] index version init failed: %s", type(e).__name__)

    # AI provider control plane: tables (SQLite path — PG owns its schema via
    # migrations), bootstrap pricing, then the one-time legacy-config import.
    # All guarded: the chatbot must boot even if the control plane stumbles.
    try:
        from app.services.ai import store as ai_store
        from app.services.ai import legacy_import
        ai_store.ensure_ai_tables()
        ai_store.seed_bootstrap_pricing()
        legacy_import.run_import(actor="system")
    except Exception as e:  # noqa: BLE001
        logger.error("[ai] control-plane startup hook failed: %s", type(e).__name__)

    # One-time at-rest encryption of the legacy AI key. Existing installs have
    # a plaintext `ai_api_key` settings row (new saves are encrypted by the
    # admin router); get_setting() decrypts transparently, so both forms keep
    # working and this quietly converges every install to encrypted.
    # Guarded: a failure leaves the plaintext row in place, which still works.
    try:
        from app.services import secure_store
        from app.db.connection import get_db_connection
        from app.db.queries import set_setting
        conn = get_db_connection()
        row = conn.execute("SELECT value FROM settings WHERE key = 'ai_api_key'").fetchone()
        conn.close()
        _legacy_key = (row["value"] or "").strip() if row else ""
        if _legacy_key and not secure_store.is_protected(_legacy_key):
            set_setting("ai_api_key", secure_store.protect(_legacy_key))
            logger.info("[secure_store] encrypted the legacy AI key at rest")
    except Exception as e:  # noqa: BLE001
        logger.error("[secure_store] legacy AI key encryption failed: %s",
                     type(e).__name__)

    # Mount theme static directories
    _mount_themes(app)

    # Start the automatic-backup scheduler (honours the admin panel schedule)
    from app.services.backup import scheduler_loop
    backup_task = asyncio.create_task(scheduler_loop())
    retention_task = asyncio.create_task(_retention_loop())

    yield

    try:
        from app.services import applog
        applog.service("service.stopped", "برنامه متوقف شد", target="padyar")
    except Exception:  # noqa: BLE001
        pass

    backup_task.cancel()
    retention_task.cancel()

    try:
        from app.services.ai.adapters import base as _ai_base
        await _ai_base.aclose_shared_client()
    except Exception:  # noqa: BLE001 — shutdown best-effort
        pass


def _mount_themes(app: FastAPI):
    """Discover and mount static directories for each theme."""
    try:
        from app.services.themes import discover_themes, THEMES_DIR
        for theme in discover_themes():
            theme_dir = os.path.join(THEMES_DIR, theme["name"])
            route = f"/themes/{theme['name']}"
            try:
                app.mount(route, StaticFiles(directory=theme_dir), name=f"theme-{theme['name']}")
                logger.info(f"Mounted theme assets: {route}")
            except Exception as e:
                logger.warning(f"Failed to mount theme '{theme['name']}': {e}")
    except Exception as e:
        logger.warning(f"Theme discovery failed: {e}")


app = FastAPI(title="دستیار پادیار", lifespan=lifespan)

# --- Middleware ---
# allow_credentials=False makes the "*" origin safe: browsers refuse to expose
# credentialed cross-origin responses, so no other site can read an admin's
# session. The admin panel is same-origin (CORS does not apply to it), and the
# public /chat endpoint is guarded separately by validate_request_origin against
# ALLOWED_ORIGINS. "*" is kept so customers can embed the chat on their own site.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Backstop on request size, from the Content-Length header. Generous by design
# (video uploads are large and stream to disk); its job is to stop a single
# request from buffering an unbounded body, not to police legitimate uploads.
# Chunked bodies carry no Content-Length — the endpoints that buffer a whole
# upload in memory (transcribe, imports, restore) enforce their own read caps.
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(512 * 1024 * 1024)))


@app.middleware("http")
async def reject_oversized_bodies(request, call_next):
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Request body too large"},
                                    status_code=413)
        except ValueError:
            pass
    return await call_next(request)


# Paths that must never generate a log row. /static, /themes and /media are
# high-volume asset traffic that would drown the store; /admin/api/logs is
# excluded because browsing the log viewer would otherwise log the browsing —
# a feedback loop that grows the table just by looking at it.
_NO_API_LOG_PREFIXES = ("/static", "/themes", "/media", "/favicon",
                        "/admin/api/logs")
_ID_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"


def _clean_id(value: str) -> str:
    """An inbound correlation header is attacker-controlled. Whitelist it."""
    return "".join(c for c in (value or "") if c in _ID_SAFE)[:64]


@app.middleware("http")
async def csrf_protection(request, call_next):
    """Enforce CSRF on every admin mutation, in one place.

    A middleware rather than a per-endpoint dependency: there are dozens of
    admin mutations across several routers, and any new one added later would
    silently be unprotected if this were opt-in. Here it is opt-OUT, and the
    only opt-out is the login endpoint.

    Which prefixes count as admin surface lives in PROTECTED_PREFIXES
    (app/auth/csrf.py — one policy file), and the conformance test in
    tests/test_csrf.py walks every registered route and fails the build if a
    verify_admin-protected mutation appears outside them. That is what keeps
    the "no unprotected mutation by accident" promise true even for routers
    mounted outside /admin/ (the synonyms API is the first such case).
    """
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH", "DELETE") \
            and path.startswith(PROTECTED_PREFIXES):
        from app.auth.csrf import enforce
        from fastapi.responses import JSONResponse
        from fastapi import HTTPException as _HTTPException
        try:
            await enforce(request)
        except _HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)


@app.middleware("http")
async def request_correlation(request, call_next):
    """Stamp every request with an id, then record one API row for it.

    The id lives in a ContextVar (see app/services/applog.py), so code deeper
    in the stack — retrieval, the LLM client, the SMS gateway — can attach it
    to their own rows without every function signature growing a parameter.
    That is what makes a single correlation id reconstruct a whole operation.

    VOLUME DECISION: a 200 on a GET is the overwhelming majority of traffic and
    the least interesting. So this records every response >= 400, plus every
    non-GET (a POST changed something and is worth a row), and drops 2xx GETs.
    An operator who wants them can raise the detail with the debug setting.
    """
    import time as _time
    from app.services import applog

    path = request.url.path
    request_id = _clean_id(request.headers.get("X-Request-ID")) or applog.new_id()
    correlation_id = _clean_id(request.headers.get("X-Correlation-ID")) or request_id
    from app.auth.security import client_ip
    ip = client_ip(request)
    applog.set_request_context(request_id=request_id,
                              correlation_id=correlation_id, ip=ip)

    started = _time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # An unhandled exception is the single most important thing to capture.
        applog.exception("api", "api.request.failed", exc,
                         message=f"{request.method} {path}",
                         route=path, http_method=request.method, http_status=500,
                          duration_ms=int((_time.perf_counter() - started) * 1000),
                          ip=ip,
                          user_agent=request.headers.get("user-agent", ""),
                          request_id=request_id, correlation_id=correlation_id)
        raise

    duration_ms = int((_time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id

    if not path.startswith(_NO_API_LOG_PREFIXES):
        status = response.status_code
        interesting = status >= 400 or request.method != "GET"
        if interesting:
            level = "error" if status >= 500 else ("warning" if status >= 400 else "info")
            applog.record("api",
                          "api.request.failed" if status >= 400 else "api.request.completed",
                          level=level,
                          message=f"{request.method} {path} -> {status}",
                          route=path, http_method=request.method, http_status=status,
                          duration_ms=duration_ms, ip=ip,
                          user_agent=request.headers.get("user-agent", ""),
                          request_id=request_id, correlation_id=correlation_id,
                          outcome="failed" if status >= 400 else "ok")
    return response


@app.middleware("http")
async def security_headers(request, call_next):
    """Baseline response hardening.

    Deliberately conservative — every header here was chosen so it CANNOT
    change what the app does:

    * `X-Content-Type-Options: nosniff` — stops a browser guessing a response
      is script when the server said it is not.
    * `Referrer-Policy` — keeps the admin panel's URL (which contains the
      obscured panel path) out of Referer headers sent to third parties.
    * `X-Frame-Options` is applied to the ADMIN PANEL ONLY. The public chat is
      meant to be embedded on a customer's own site (see the CORS note above),
      so framing it must stay allowed; framing the admin panel is a
      clickjacking vector with no legitimate use.
    * `Cache-Control: no-store` on admin pages and admin APIs. Exhibition
      machines are shared; an admin page sitting in the back/forward cache is
      a real exposure. Public chat and static assets keep their caching.
    * HSTS only when `COOKIE_SECURE=true` — the project's production marker.
      Sending it over plain HTTP in dev would pin a browser to HTTPS for a
      host that does not serve it.

    No Content-Security-Policy is set here: the themes legitimately use inline
    scripts and styles, so a policy strict enough to be worth having would
    break the chat. Adding one properly means giving those blocks a nonce —
    tracked in docs/engineering/DECISIONS.md rather than half-done.
    """
    response = await call_next(request)
    path = request.url.path

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

    if path.startswith(("/secure-panel-inotex", "/admin/api")):
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")

    if COOKIE_SECURE:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    return response


@app.middleware("http")
async def slide_admin_cookie(request, call_next):
    """Re-issue the admin session cookie whenever its DB row just slid.

    verify_admin slides the `admin_sessions` row on every authenticated
    request, but a dependency cannot touch the response — so the cookie kept
    its original 1h max_age and died mid-session while the row stayed valid.
    The dependency sets request.state.slide_admin_cookie instead (dependencies
    run INSIDE call_next, so the flag is guaranteed visible here), and this
    middleware re-issues the cookie with the same attributes login used.

    Deliberately unconditional on every authenticated response (no threshold,
    no hysteresis): cookie lifetime then tracks the DB slide exactly — no
    drift, no new column, no migration — and the cost is one Set-Cookie
    header per response on no-store pages read by a handful of staff.

    Safe against logout by construction: admin_logout never runs verify_admin
    (it reads the cookie directly), so the flag is never set on a logout
    request and the middleware cannot resurrect the cookie logout deleted.
    The status guard keeps renewal off error responses — a 429/500 from an
    admin API call must not look like activity.
    """
    response = await call_next(request)
    if getattr(request.state, "slide_admin_cookie", False) \
            and response.status_code < 400:
        token = request.cookies.get(ADMIN_COOKIE_NAME)
        if token:
            response.set_cookie(
                key=ADMIN_COOKIE_NAME, value=token,
                httponly=True, secure=COOKIE_SECURE, samesite="lax",
                max_age=SESSION_TIMEOUT_HOURS * 3600,
            )
    return response

# --- Static Files ---
app.mount("/LOGO", StaticFiles(directory="LOGO"), name="logo")
app.mount("/static", StaticFiles(directory="static"), name="static")
# NOTE: data/ is deliberately NOT mounted. Nothing in the chat UI or admin
# panel fetches it over HTTP (the taxonomy is read server-side from its file
# path), and the directory can hold dev-only artifacts with visitor PII
# (otp-dev-outbox.log) that must never be downloadable.
# Serve uploaded media (videos) locally. In production nginx serves /media
# directly; this mount is the fallback so video URLs work in local/dev too.
# The media dir is gitignored, so it may be absent on a fresh checkout —
# StaticFiles raises RuntimeError at mount time if the directory is missing.
os.makedirs(os.path.join(BASE_DIR, "media"), exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")

# --- Include Routers ---
# Public router always loaded (serves HTML pages + health check)
app.include_router(public.router)

# AI provider control plane — core admin surface, always loaded.
from app.routers import admin_ai  # noqa: E402
app.include_router(admin_ai.router)

# Module routers — loaded based on ENABLED_MODULES config
for module_name, router in load_module_routers(ENABLED_MODULES):
    app.include_router(router)
    logger.info(f"Registered router for module: {module_name}")
