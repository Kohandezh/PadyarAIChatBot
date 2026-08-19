"""Service registry, health probes and the system health score.

DEPLOYMENT HONESTY
------------------
This install runs as a single uvicorn process started from .claude/launch.json
or `python main.py`. There is no systemd unit, no PM2, no container
orchestrator, no Redis, no queue broker and no separate worker process. That
fact drives the whole design:

  * Services that live INSIDE this process (search index, taxonomy, themes,
    log retention, backup scheduler) can genuinely be re-initialised at
    runtime, so they get real actions.
  * The uvicorn process itself CANNOT restart itself and still answer the
    request that asked it to. A "Restart API" button here would either lie or
    hang. So the API is reported READ-ONLY with a Persian explanation of what
    the operator must do instead. A button that cannot work is worse than no
    button.
  * External dependencies (the AI provider, the SMS gateway) are not ours to
    start or stop. They are probe-only.

PROBE RULES
-----------
  * A probe NEVER raises. Anything unexpected becomes status "unknown".
  * A probe is CHEAP and never costs money. We never send an SMS or spend a
    token to answer "are you healthy". Credit lookups and config checks only.
  * Results are CACHED (15s). The admin dashboard polls; without a cache every
    page render would hammer the SMS gateway, which is a real network call.
"""
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone

from app.config import logger

# Status vocabulary shared by the API and the UI.
OK, DEGRADED, DOWN, DISABLED, UNKNOWN = "healthy", "degraded", "down", "disabled", "unknown"

STATUS_FA = {OK: "سالم", DEGRADED: "کاهش کیفیت", DOWN: "قطع",
             DISABLED: "غیرفعال", UNKNOWN: "نامشخص"}

# Per-probe cache lifetimes. A dashboard left open polls; without this the SMS
# probe would call Asanak's getcredit every 15 seconds forever. Measured: that
# probe is a real 1.9s network round-trip, and the gateway rate-limits. Local
# probes are cheap and can stay fresh; anything that leaves this machine gets a
# long TTL.
_CACHE_SECONDS = 15                      # default, for local probes
_PROBE_TTL = {
    "sms": 300,           # real network call to the gateway
    "ai_provider": 120,   # imports the provider client
    "embeddings": 120,    # touches the model cache
    "search": 60,
}
_cache: dict = {"at": 0.0, "data": None}
_probe_cache: dict = {}                  # name -> (expires_at, result)
_PROCESS_STARTED = time.time()


def _probe(name, label_fa, fn, critical=False, dependencies=()):
    """Run one probe under a guard. Never raises, always returns a dict."""
    started = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as e:  # noqa: BLE001 — an exploding probe is a finding, not a crash
        status, detail = UNKNOWN, f"بررسی ناموفق: {type(e).__name__}"
        logger.error("[health] probe %s failed: %s", name, type(e).__name__)
    return {
        "name": name,
        "label_fa": label_fa,
        "status": status,
        "status_fa": STATUS_FA.get(status, status),
        "detail_fa": detail,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "critical": critical,
        "dependencies": list(dependencies),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ── Individual probes ───────────────────────────────────────────────────

def _probe_app_db():
    """Probe the engine the runtime ACTUALLY uses.

    Before this guard the probe read the SQLite file unconditionally, so with
    PostgreSQL stopped it still reported "healthy" — the file was fine, but the
    application could not serve a single request. A health check that inspects
    a store the app no longer uses is worse than none.
    """
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        from app.db import pg
        ok, detail = pg.healthy()
        if not ok:
            return DOWN, f"پستگرس در دسترس نیست: {detail}"
        stats = pg.pool_stats()
        return OK, (f"پاسخ‌گو · استخر {stats.get('size')}/{stats.get('max')} "
                    f"· منتظر {stats.get('waiting')}")

    from app.config import DB_PATH
    if not os.path.exists(DB_PATH):
        return DOWN, "فایل پایگاه داده پیدا نشد."
    conn = sqlite3.connect(DB_PATH, timeout=3.0)
    try:
        conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    size = os.path.getsize(DB_PATH) / 1e6
    return OK, f"پاسخ‌گو · {size:.1f} مگابایت · journal={mode}"


def _probe_logs_db():
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        from app.services import applog
        try:
            conn = applog.get_logs_connection()
            total = sum(conn.execute(f"SELECT COUNT(*) AS n FROM observability.{t}")
                        .fetchone()["n"] for t in applog.TABLES)
            conn.close()
        except Exception as e:  # noqa: BLE001
            return DOWN, f"پایگاه لاگ در دسترس نیست: {type(e).__name__}"
        return OK, f"{total:,} رخداد در پستگرس"

    from app.config import LOGS_DB_PATH
    if not os.path.exists(LOGS_DB_PATH):
        return DEGRADED, "پایگاه لاگ هنوز ساخته نشده است."
    from app.services import applog
    conn = applog.get_logs_connection()
    try:
        total = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in applog.TABLES)
    finally:
        conn.close()
    size = os.path.getsize(LOGS_DB_PATH) / 1e6
    return OK, f"{total:,} رخداد · {size:.1f} مگابایت"


def _probe_search():
    from app.services import search
    docs = len(getattr(search, "dataset", []) or [])
    if docs == 0:
        return DEGRADED, "هیچ سندی بارگذاری نشده است — چت‌بات پاسخ محلی ندارد."
    return OK, f"{docs} سند نمایه‌شده"


def _probe_embeddings():
    from app.services import embeddings
    if not embeddings.available():
        return DEGRADED, "مدل محلی در دسترس نیست — بازیابی به BM25 تکیه می‌کند."
    return OK, "مدل محلی بارگذاری شده است."


def _probe_ai_provider():
    """Config reachability only — a health check must never spend tokens."""
    from app.services.openai import provider_config
    base, key = provider_config()
    if not key:
        return DISABLED, "کلید سرویس هوش مصنوعی تنظیم نشده است."
    host = (base or "").split("//")[-1].split("/")[0]
    return OK, f"پیکربندی‌شده · {host}"


def _probe_sms():
    """Uses getcredit — proves the credentials WITHOUT sending a message."""
    from app.db.queries import get_setting
    provider = (get_setting("sms_provider", "") or "dev").strip()
    if provider == "dev":
        return DISABLED, "سرویس‌دهنده روی dev است — پیامک واقعی ارسال نمی‌شود."
    from app.services import sms
    if not sms.is_configured(provider):
        return DOWN, "اعتبارنامه‌های پیامک کامل نیست."
    try:
        credit = sms.credit(provider)
    except Exception as e:  # noqa: BLE001
        detail = getattr(e, "detail", None) or type(e).__name__
        return DEGRADED, f"درگاه پاسخ نداد: {detail}"
    if credit <= 0:
        return DEGRADED, "اعتبار پیامک تمام شده است."
    return OK, f"{credit:,} پیامک اعتبار"


def _probe_registration():
    from app.config import ENABLED_MODULES
    if ENABLED_MODULES and "registration" not in ENABLED_MODULES:
        return DISABLED, "ماژول ثبت‌نام در این نصب فعال نیست."
    from app.db.queries import get_setting
    on = get_setting("registration_enabled", "false") == "true"
    return (OK, "فعال") if on else (DISABLED, "از پنل خاموش شده است.")


def _probe_scheduler():
    """The backup scheduler and the log-retention loop are the only real
    scheduled jobs in this install — both are asyncio tasks in app/main.py."""
    from app.services.backup import get_schedule
    sched = get_schedule() or {}
    if not sched.get("enabled"):
        return DISABLED, "زمان‌بندی پشتیبان‌گیری خاموش است."
    return OK, f"اجرای بعدی: {sched.get('next_run', '—')}"


def _probe_storage():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    total, used, free = shutil.disk_usage(root)
    pct = used / total * 100 if total else 0
    detail = f"{pct:.0f}٪ مصرف‌شده · {free / 1e9:.1f} گیگابایت آزاد"
    if pct >= 95:
        return DOWN, detail
    if pct >= 85:
        return DEGRADED, detail
    return OK, detail


def _probe_themes():
    from app.services.themes import discover_themes
    themes = discover_themes()
    if not themes:
        return DOWN, "هیچ قالبی پیدا نشد — رابط کاربری بارگذاری نمی‌شود."
    return OK, f"{len(themes)} قالب"


# The registry. `controllable` records the honest truth about what this
# deployment can actually act on — see the module docstring.
REGISTRY = (
    ("app_db",       "پایگاه دادهٔ برنامه",      _probe_app_db,       True,  ()),
    ("logs_db",      "پایگاه دادهٔ لاگ",         _probe_logs_db,      False, ()),
    ("search",       "موتور جستجو",             _probe_search,       True,  ("app_db",)),
    ("embeddings",   "مدل محلی امبدینگ",        _probe_embeddings,   False, ()),
    ("ai_provider",  "سرویس هوش مصنوعی",        _probe_ai_provider,  False, ()),
    ("sms",          "درگاه پیامک",             _probe_sms,          False, ()),
    ("registration", "ثبت‌نام بازدیدکننده",     _probe_registration, False, ("sms",)),
    ("scheduler",    "زمان‌بند پشتیبان‌گیری",    _probe_scheduler,    False, ("app_db",)),
    ("storage",      "فضای ذخیره‌سازی",         _probe_storage,      True,  ()),
    ("themes",       "قالب‌های رابط کاربری",     _probe_themes,       True,  ()),
)


def probe_all(force: bool = False):
    """Every probe, each honouring its own TTL. `force=True` refreshes all."""
    now = time.time()
    results = []
    for name, label, fn, critical, deps in REGISTRY:
        cached = _probe_cache.get(name)
        if not force and cached and cached[0] > now:
            results.append(cached[1])
            continue
        result = _probe(name, label, fn, critical, deps)
        ttl = _PROBE_TTL.get(name, _CACHE_SECONDS)
        result["cached_for"] = ttl
        _probe_cache[name] = (now + ttl, result)
        results.append(result)
    return results


def probe_one(name: str, force: bool = True):
    """One probe by name. Only a name in the REGISTRY is ever executed — a
    caller-supplied string never selects a function by any other route."""
    now = time.time()
    for n, label, fn, critical, deps in REGISTRY:
        if n != name:
            continue
        cached = _probe_cache.get(n)
        if not force and cached and cached[0] > now:
            return cached[1]
        result = _probe(n, label, fn, critical, deps)
        _probe_cache[n] = (now + _PROBE_TTL.get(n, _CACHE_SECONDS), result)
        return result
    return None


# ── Health score ────────────────────────────────────────────────────────
# Documented weighting, not an arbitrary number.
#   Every service starts at full marks. A CRITICAL service (the chatbot cannot
#   serve without it) is worth twice a non-critical one. `down` loses all of a
#   service's marks, `degraded` loses half, `unknown` loses a quarter (we do
#   not know, and not knowing is itself a mild problem). `disabled` is an
#   operator's deliberate choice and is excluded from the denominator entirely
#   — switching a feature off must not make the system look unhealthy.
_PENALTY = {OK: 0.0, DEGRADED: 0.5, DOWN: 1.0, UNKNOWN: 0.25, DISABLED: 0.0}


def health_score(services=None):
    services = services if services is not None else probe_all()
    earned = possible = 0.0
    for svc in services:
        if svc["status"] == DISABLED:
            continue
        weight = 2.0 if svc["critical"] else 1.0
        possible += weight
        earned += weight * (1.0 - _PENALTY.get(svc["status"], 0.25))
    score = int(round((earned / possible) * 100)) if possible else 100
    if any(s["status"] == DOWN and s["critical"] for s in services):
        # One dead critical service must never read as "healthy" just because
        # everything else is fine.
        score = min(score, 40)
    label = "سالم" if score >= 90 else ("کاهش کیفیت" if score >= 60 else "بحرانی")
    return {"score": score, "label_fa": label,
            "counts": {k: sum(1 for s in services if s["status"] == k)
                       for k in (OK, DEGRADED, DOWN, DISABLED, UNKNOWN)}}


def process_info():
    """Non-sensitive runtime facts. No secrets, no full paths."""
    import platform
    import sys
    from app.config import ENABLED_MODULES
    uptime = int(time.time() - _PROCESS_STARTED)
    return {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.system(),
        "started_at": datetime.fromtimestamp(_PROCESS_STARTED, timezone.utc)
                              .isoformat(timespec="seconds"),
        "uptime_seconds": uptime,
        "uptime_fa": _human_uptime(uptime),
        "modules": sorted(ENABLED_MODULES) if ENABLED_MODULES else ["همه"],
        "timezone": str(datetime.now().astimezone().tzinfo),
    }


def _human_uptime(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d} روز و {h} ساعت"
    if h:
        return f"{h} ساعت و {m} دقیقه"
    return f"{m} دقیقه"
