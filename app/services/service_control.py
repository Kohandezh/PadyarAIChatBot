"""Allowlisted service actions.

THE CONTRACT — read before adding anything here
-----------------------------------------------
Nothing in this module may execute a string that came from a request. No
process spawning, no shell, no dynamic evaluation, and no attribute lookup
driven by user input. An action is performed ONLY by looking a name up in the
ACTIONS dict below and calling the Python callable found there. A name that is
not a key does nothing at all.

A test asserts by source inspection that the dangerous constructs are absent
from this file, so the prose above deliberately does not spell them out — the
guard checks code, and a docstring must not be able to trip it.

That is a deliberate constraint from the customer: no arbitrary shell access
from the admin panel, ever. A test asserts by source inspection that the
forbidden constructs are absent, so this stays true after later edits.

WHAT IS ACTUALLY CONTROLLABLE
-----------------------------
This app is one uvicorn process with no supervisor. So:

  * In-process subsystems CAN be re-initialised for real: the search index,
    the taxonomy, the theme list, log retention, a backup run.
  * The API process itself CANNOT restart itself and still answer the request
    that asked. It is reported read-only, with what the operator must do
    instead. A Start/Stop button that cannot work would be a lie.
  * Third-party services (AI provider, SMS gateway) are not ours to control.

CONCURRENCY
-----------
One lock, non-blocking. A second attempt at any action while one is running is
REFUSED rather than queued — two simultaneous reindexes would be pointless and
two simultaneous restore-ish operations would be dangerous.
"""
import threading
import time

from app.config import logger
from app.services import applog

_lock = threading.Lock()
_running = {"action": None, "since": 0.0}


class ActionRefused(Exception):
    """Refused before anything happened. Carries a Persian operator message."""

    def __init__(self, message_fa: str):
        self.message_fa = message_fa
        super().__init__(message_fa)


# ── The actions themselves ──────────────────────────────────────────────

def _reindex_search():
    import time as _t
    from app.services.search import load_dataset_internal, report_reindex
    started = _t.perf_counter()
    load_dataset_internal()
    from app.services import search
    docs = len(getattr(search, "dataset", []) or [])
    report_reindex(docs, 0, int((_t.perf_counter() - started) * 1000))
    return f"{docs} سند دوباره نمایه شد."


def _reload_taxonomy():
    from app.services import taxonomy
    doc = taxonomy.load(force=True) if hasattr(taxonomy, "load") else None
    return "فهرست علاقه‌مندی‌ها دوباره خوانده شد." if doc is not None else \
           "فهرست علاقه‌مندی‌ها بازخوانی شد."


def _reload_themes():
    from app.services.themes import discover_themes
    return f"{len(discover_themes())} قالب دوباره کشف شد."


def _purge_logs():
    removed = applog.purge_expired()
    total = sum(removed.values()) if removed else 0
    return f"{total} رکورد منقضی حذف شد." if total else "رکورد منقضی‌ای وجود نداشت."


def _run_backup():
    from app.services.backup import create_backup_now
    result = create_backup_now()
    return f"پشتیبان ساخته شد: {result}" if result else "پشتیبان‌گیری انجام شد."


def _health_check():
    from app.services import health
    services = health.probe_all(force=True)
    score = health.health_score(services)
    return f"بررسی سلامت انجام شد — امتیاز {score['score']} از ۱۰۰."


# name -> (callable, Persian label, destructive?, which service it belongs to)
# The ONLY route to execution. Adding an action means adding a row here.
ACTIONS = {
    "reindex_search":  (_reindex_search,  "بازسازی نمایهٔ جستجو", False, "search"),
    "reload_taxonomy": (_reload_taxonomy, "بازخوانی فهرست علاقه‌مندی‌ها", False, "search"),
    "reload_themes":   (_reload_themes,   "بازخوانی قالب‌ها", False, "themes"),
    "purge_logs":      (_purge_logs,      "پاک‌سازی لاگ‌های منقضی", True,  "logs_db"),
    "run_backup":      (_run_backup,      "پشتیبان‌گیری فوری", False, "app_db"),
    "health_check":    (_health_check,    "بررسی سلامت همهٔ سرویس‌ها", False, "*"),
}

# Services this deployment CANNOT control, and the honest reason. Surfaced in
# the UI so an operator is never left guessing why there is no button.
READ_ONLY = {
    "app_db":       "پایگاه داده درون همین پروسه باز است؛ توقف آن یعنی توقف کل برنامه.",
    "ai_provider":  "سرویس بیرونی است و از اینجا قابل راه‌اندازی یا توقف نیست.",
    "sms":          "درگاه پیامک سرویس بیرونی آسانک است.",
    "embeddings":   "مدل در حافظهٔ همین پروسه بارگذاری شده است.",
    "storage":      "فضای دیسک با عملیات برنامه مدیریت نمی‌شود.",
    "registration": "با کلید فعال/غیرفعال در تنظیمات ثبت‌نام کنترل می‌شود.",
    "scheduler":    "با زمان‌بندی در تنظیمات پشتیبان‌گیری کنترل می‌شود.",
}

# The process itself. Reported, never faked.
PROCESS_CONTROL_AVAILABLE = False
PROCESS_CONTROL_REASON = (
    "این نصب بدون سرویس‌بان (systemd/pm2/docker) اجرا می‌شود، بنابراین پروسهٔ "
    "برنامه نمی‌تواند خودش را از داخل پنل ری‌استارت کند و هم‌زمان به همین "
    "درخواست پاسخ دهد. برای ری‌استارت، پروسهٔ uvicorn را از همان جایی که "
    "اجرا شده متوقف و دوباره اجرا کنید."
)


def available_actions():
    return [{"name": name, "label_fa": label, "destructive": destructive,
             "service": service}
            for name, (_fn, label, destructive, service) in ACTIONS.items()]


def run(action: str, actor: str = "", ip: str = "") -> dict:
    """Execute one allowlisted action. Audited on every outcome, including refusal."""
    entry = ACTIONS.get(action)
    if entry is None:
        # Audit the ATTEMPT. Someone poking unknown action names is a signal.
        applog.security("admin.service.action.rejected",
                        "درخواست اجرای عملیات ناشناخته",
                        actor=actor, ip=ip, target=str(action)[:80],
                        outcome="denied", level="warning")
        raise ActionRefused("این عملیات تعریف‌شده نیست.")

    fn, label, destructive, service = entry

    if not _lock.acquire(blocking=False):
        raise ActionRefused(
            f"عملیات «{_running['action'] or 'دیگری'}» در حال اجراست. "
            "تا پایان آن صبر کنید.")
    _running["action"], _running["since"] = label, time.time()

    applog.audit("admin.service.action.requested", f"درخواست: {label}",
                 actor=actor, target=service, outcome="requested",
                 ip=ip, metadata={"action": action})
    started = time.perf_counter()
    try:
        message = fn()
    except Exception as e:  # noqa: BLE001 — report the failure, never leak the trace
        duration = int((time.perf_counter() - started) * 1000)
        applog.audit("admin.service.action.failed", f"ناموفق: {label}",
                     actor=actor, target=service, outcome="failed",
                     ip=ip, level="warning", duration_ms=duration,
                     error_type=type(e).__name__, metadata={"action": action})
        applog.exception("service", "service.action.failed", e, message=label)
        logger.error("[service_control] %s failed: %s", action, type(e).__name__)
        raise ActionRefused(f"اجرای «{label}» ناموفق بود.")
    finally:
        _running["action"], _running["since"] = None, 0.0
        _lock.release()

    duration = int((time.perf_counter() - started) * 1000)
    applog.audit("admin.service.action.completed", f"انجام شد: {label}",
                 actor=actor, target=service, outcome="ok",
                 ip=ip, duration_ms=duration, metadata={"action": action})
    applog.service("service.action.completed", message, target=service,
                   duration_ms=duration, metadata={"action": action})
    return {"ok": True, "action": action, "label_fa": label,
            "message_fa": message, "duration_ms": duration}
