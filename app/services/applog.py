"""Padyar central logging service — its own database, four tables, never fatal.

WHY A SEPARATE DATABASE
-----------------------
SQLite locks per FILE, not per table. Log writes are high-volume and bursty
(an LLM outage produces a row per request). If those rows landed in
chat_history.db, a log storm could block the chatbot's own reads. So logging
owns `application_logs.db` (LOGS_DB_PATH). The worst case is now "logging gets
slow", never "the chatbot stops answering".

FOUR TABLES, NOT ONE
--------------------
  app_logs        operational events — retention configurable, default 90 days
  audit_logs      who changed what — retention SEPARATE and longer
  security_events attacks, denials, rate limits — retention SEPARATE and longer
  service_events  start/stop/health of services and dependencies

Audit and security rows are evidence. They are deliberately NOT governed by the
operational retention setting: an administrator lowering "log retention" to 7
days must not thereby erase the record of their own destructive actions.

THREE RULES THIS MODULE WILL NOT BREAK
--------------------------------------
1. Logging never breaks the caller. Every public function swallows its own
   errors and returns a falsy value. A full disk must degrade to "no row",
   never to a failed OTP or a 500 on /chat.
2. Secrets never reach a row. Redaction happens HERE, not at the call sites —
   a call site added later will forget. See `_redact` / `SECRET_KEY_HINTS`.
3. No recursion. A failure inside logging goes to the stdlib logger only,
   never back into these tables.

CORRELATION
-----------
`request_id` is stamped per HTTP request by the middleware and carried in a
ContextVar, so any code deeper in the stack can log without threading an
argument through every signature. `correlation_id` groups a whole logical
operation (visitor message -> retrieval -> LLM -> SMS) so an operator can
reconstruct the chain from one id.
"""
import csv
import io
import json
import re
import sqlite3
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from app.config import logger

# ── Correlation context ─────────────────────────────────────────────────
# ContextVars, not globals: each request/task gets its own value, and asyncio
# does not leak one visitor's id into another's log rows.
_request_id: ContextVar[str] = ContextVar("padyar_request_id", default="")
_correlation_id: ContextVar[str] = ContextVar("padyar_correlation_id", default="")
_actor: ContextVar[str] = ContextVar("padyar_actor", default="")
_actor_ip: ContextVar[str] = ContextVar("padyar_actor_ip", default="")
# Live credential VALUES in play on this task, so `scrub_text` can remove them
# by exact match instead of guessing their shape. Holding them here is not a
# new exposure: the same strings are already in process memory (they must be,
# to be sent in a header). What it buys is that a provider echoing its own key
# back inside an error body cannot reach a log row, an API response, or an
# audit record — regardless of what that vendor's key happens to look like.
_active_secrets: ContextVar[frozenset] = ContextVar(
    "padyar_active_secrets", default=frozenset())


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_context(request_id: str = "", correlation_id: str = "",
                       actor: str = "", ip: str = "") -> None:
    if request_id:
        _request_id.set(request_id)
    if correlation_id:
        _correlation_id.set(correlation_id)
    if actor:
        _actor.set(actor)
    if ip:
        _actor_ip.set(ip)


def register_secret(value: str) -> None:
    """Declare a credential VALUE that must never appear in any output.

    Called wherever a secret is decrypted for use. `scrub_text` then strips
    that exact string from anything heading for a log row, an audit record,
    an admin API response or an exception message.

    Short values are ignored: a 4-character "secret" would match ordinary
    words and redact the diagnostics we need to keep. Real provider keys are
    far longer than this floor.
    """
    if not value or len(value) < 8:
        return
    current = _active_secrets.get()
    if value not in current:
        _active_secrets.set(current | {value})


def forget_secrets() -> None:
    """Drop the registered values (end of a task / test isolation)."""
    _active_secrets.set(frozenset())


def current_request_id() -> str:
    return _request_id.get()


def current_correlation_id() -> str:
    return _correlation_id.get()


# ── Vocabulary ──────────────────────────────────────────────────────────
# slug -> Persian label. Adding a category means adding one row here; the API,
# the sub-menu and the filters all read this map.
CATEGORIES: dict[str, str] = {
    "llm":         "هوش مصنوعی",
    "sms":         "پیامک",
    "otp":         "ثبت‌نام و کد تأیید",
    "auth":        "ورود مدیر",
    "chat":        "گفتگو",
    "api":         "درخواست‌های API",
    "retrieval":   "جستجو و بازیابی",
    "content":     "محتوا و دیتاست",
    "leads":       "جذب سرنخ نمایشگاه",
    "backup":      "پشتیبان‌گیری",
    "integration": "سرویس‌های بیرونی",
    "service":     "سرویس‌ها و سلامت",
    "security":    "امنیت",
    "audit":       "رخدادهای حساس",
    "system":      "سیستم",
}

# Syslog-style severities, mapped onto what this codebase already uses.
LEVELS: tuple[str, ...] = ("debug", "info", "notice", "warning", "error",
                           "critical", "alert", "emergency")
_LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}

DEFAULT_RETENTION_DAYS = 90
DEFAULT_AUDIT_RETENTION_DAYS = 365
DEFAULT_SECURITY_RETENTION_DAYS = 365

MAX_METADATA_CHARS = 4000
MAX_MESSAGE_CHARS = 1000
MAX_STACK_CHARS = 4000

# ── Log-storm protection ────────────────────────────────────────────────
# Identical (category,event,level) rows arriving faster than this are counted
# and collapsed. A provider outage must not write 50k rows a minute; the
# suppression itself is reported so the volume is never silently lost.
_SUPPRESS_WINDOW_SECONDS = 10
_SUPPRESS_THRESHOLD = 20
_recent: dict[tuple, list] = {}   # key -> [window_start, count, suppressed]
# Evidence is never sampled away.
_NEVER_SUPPRESS = ("security", "audit")


# ── Redaction ───────────────────────────────────────────────────────────
SECRET_KEY_HINTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "api-key",
    "authorization", "auth", "cookie", "session", "credential", "private",
    "otp", "code_hash", "verification", "security_answer", "hmac", "bearer",
    "salt", "signature",
)
_REDACTED = "[redacted]"

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?98|0)9\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-=/+]{8,}")
# The key body may contain hyphens: Anthropic issues `sk-ant-api03-...`, and a
# character class of [A-Za-z0-9] alone stops at the first hyphen, redacting
# nothing and leaving the live key in the log.
_SK_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9][A-Za-z0-9_\-]{11,}")
_ENC_RE = re.compile(r"\benc:[A-Za-z0-9_\-=]{16,}")
# Google API keys are not `sk-` prefixed. Gemini authenticates with
# `x-goog-api-key: AIza...`, so without this the whole key survives scrubbing.
_GOOGLE_KEY_RE = re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}")
# Vendor-prefixed keys for providers this install can actually be pointed at.
# Shape matching alone is NOT the defence (see `register_secret`) — it is the
# second line, for text that reaches a log without a registered secret.
_VENDOR_KEY_RE = re.compile(
    r"(?i)\b(?:xai|gsk|glm|mistral|dashscope|sk-or|gw|key)[-_][A-Za-z0-9_\-]{10,}")
# A credential passed in a query string — `?key=`, `?api_key=`, `?access_token=`.
# Provider errors and our own diagnostics quote full request URLs.
_QUERY_KEY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|access[_-]?token|token)=)[A-Za-z0-9._\-]{8,}")
# `"api_key": "..."` inside a quoted provider error body.
_JSON_KEY_RE = re.compile(
    r'(?i)("(?:api[_-]?key|secret|password|access[_-]?token)"\s*:\s*")[^"]{8,}(")')
# Control characters and newlines: a visitor must not be able to forge a second
# log line, and ANSI escapes must not reach an operator's terminal.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def mask_phone(value: str) -> str:
    """0912***3024 — recognisable, not dialable."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 8:
        return "*" * len(digits)
    return digits[:4] + "*" * (len(digits) - 8) + digits[-4:]


def mask_email(value: str) -> str:
    name, _, domain = (value or "").partition("@")
    if not domain:
        return "*" * len(value or "")
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}***@{domain}"


def scrub_text(text) -> str:
    """Everything that goes into a text column passes through here.

    Order matters: strip credential shapes first, then PII, then neutralise
    anything that could forge a log line or drive a terminal.
    """
    s = str(text if text is not None else "")
    # VALUE-based first. Regexes can only catch key SHAPES someone thought of,
    # and a red-team pass proved that is not enough: `xai-…`, Mistral's bare
    # 32-char alphanumeric, and `gw_live_…` gateway keys all sailed through
    # and landed verbatim in `audit_logs`, which is exempt from retention
    # pruning — a permanent credential disclosure. Whoever holds the actual
    # secret registers it (see `register_secret`), and then its exact value is
    # removed no matter what shape it has.
    for secret in _active_secrets.get():
        if secret and secret in s:
            s = s.replace(secret, _REDACTED)
    s = _BEARER_RE.sub(_REDACTED, s)
    s = _SK_RE.sub(_REDACTED, s)
    s = _VENDOR_KEY_RE.sub(_REDACTED, s)
    s = _GOOGLE_KEY_RE.sub(_REDACTED, s)
    s = _ENC_RE.sub(_REDACTED, s)
    s = _JSON_KEY_RE.sub(rf"\1{_REDACTED}\2", s)
    s = _QUERY_KEY_RE.sub(rf"\1{_REDACTED}", s)
    s = _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), s)
    s = _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), s)
    s = _CONTROL_RE.sub(" ", s)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return s


def _redact(value, key: str = "", _depth: int = 0):
    """Recursively strip credentials out of anything heading for a row."""
    if _depth > 6:
        return "[too deep]"
    low = (key or "").lower()
    if any(hint in low for hint in SECRET_KEY_HINTS):
        return _REDACTED
    if isinstance(value, dict):
        return {str(k)[:80]: _redact(v, str(k), _depth + 1)
                for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple, set)):
        return [_redact(v, key, _depth + 1) for v in list(value)[:60]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub_text(value)


# ── Storage ─────────────────────────────────────────────────────────────
# The canonical column set. All four tables share it so one query shape, one
# export writer and one detail view serve every category.
_COLUMNS = """
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    level         TEXT    NOT NULL DEFAULT 'info',
    category      TEXT    NOT NULL DEFAULT 'system',
    subcategory   TEXT    NOT NULL DEFAULT '',
    event_name    TEXT    NOT NULL DEFAULT '',
    message       TEXT    NOT NULL DEFAULT '',
    outcome       TEXT    NOT NULL DEFAULT '',
    actor         TEXT    NOT NULL DEFAULT '',
    actor_type    TEXT    NOT NULL DEFAULT '',
    target        TEXT    NOT NULL DEFAULT '',
    ip            TEXT    NOT NULL DEFAULT '',
    user_agent    TEXT    NOT NULL DEFAULT '',
    provider      TEXT    NOT NULL DEFAULT '',
    model         TEXT    NOT NULL DEFAULT '',
    route         TEXT    NOT NULL DEFAULT '',
    http_method   TEXT    NOT NULL DEFAULT '',
    http_status   INTEGER,
    duration_ms   INTEGER,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    cost          REAL,
    retry_count   INTEGER,
    error_type    TEXT    NOT NULL DEFAULT '',
    error_code    TEXT    NOT NULL DEFAULT '',
    stack         TEXT    NOT NULL DEFAULT '',
    request_id    TEXT    NOT NULL DEFAULT '',
    correlation_id TEXT   NOT NULL DEFAULT '',
    conversation_id TEXT  NOT NULL DEFAULT '',
    metadata      TEXT    NOT NULL DEFAULT ''
"""

TABLES = ("app_logs", "audit_logs", "security_events", "service_events")

# Category -> which table the row belongs in. Everything else is operational.
_TABLE_FOR = {"audit": "audit_logs", "security": "security_events",
              "service": "service_events"}


def table_for(category: str) -> str:
    return _TABLE_FOR.get(category, "app_logs")


def get_logs_connection():
    """The observability connection — same routing as the application store."""
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        from app.db import pg
        return pg.connect()

    from app.config import LOGS_DB_PATH
    conn = sqlite3.connect(LOGS_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_tables() -> None:
    """Create every table and index. Idempotent; safe on each boot."""
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        return          # schema is owned by migrations/, not by runtime DDL
    try:
        conn = get_logs_connection()
        for table in TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({_COLUMNS})")
            # Indexes chosen from the queries the admin UI actually issues:
            # newest-first paging, per-category paging, severity filtering, and
            # the three correlation lookups. No more — every index costs write
            # amplification on the hottest write path in the app.
            for name, cols in (
                ("created", "created_at DESC"),
                ("cat", "category, created_at DESC"),
                ("level", "level, created_at DESC"),
                ("req", "request_id"),
                ("corr", "correlation_id"),
                ("conv", "conversation_id"),
                ("actor", "actor, created_at DESC"),
            ):
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_{name} ON {table}({cols})")
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001 — a log table is never worth a crash
        logger.error("[applog] could not ensure tables: %s", type(e).__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _should_suppress(category: str, event: str, level: str) -> tuple:
    """(suppress?, suppressed_so_far). Evidence categories are never dropped."""
    if category in _NEVER_SUPPRESS:
        return False, 0
    key = (category, event, level)
    now = time.time()
    slot = _recent.get(key)
    if slot is None or now - slot[0] > _SUPPRESS_WINDOW_SECONDS:
        _recent[key] = [now, 1, 0]
        if len(_recent) > 2000:          # bound the dict itself
            _recent.clear()
            _recent[key] = [now, 1, 0]
        return False, 0
    slot[1] += 1
    if slot[1] > _SUPPRESS_THRESHOLD:
        slot[2] += 1
        return True, slot[2]
    return False, 0


def record(category: str, event_name: str, level: str = "info", message: str = "",
           **fields):
    """Write one row. Returns its id, or None. NEVER raises.

    Recognised keyword fields mirror the canonical schema: subcategory,
    outcome, actor, actor_type, target, ip, user_agent, provider, model,
    route, http_method, http_status, duration_ms, tokens_in, tokens_out,
    cost, retry_count, error_type, error_code, stack, request_id,
    correlation_id, conversation_id, metadata.
    """
    try:
        cat = category if category in CATEGORIES else "system"
        lvl = level if level in LEVELS else "info"

        # Two settings govern severity and they must not contradict each other.
        # `min_level` is the ordinary floor; `log_debug_enabled` LOWERS that
        # floor to debug. Applying min_level on top of an explicitly enabled
        # debug switch made the switch a no-op — an operator would turn it on,
        # see nothing, and conclude logging was broken.
        floor = "debug" if debug_enabled() else min_level()
        if _LEVEL_RANK[lvl] < _LEVEL_RANK[floor]:
            return None

        suppress, seen = _should_suppress(cat, event_name, lvl)
        if suppress:
            if seen == 1 or seen % 500 == 0:
                logger.warning("[applog] suppressing repeats of %s/%s (%s so far)",
                               cat, event_name, seen)
            return None

        meta = fields.get("metadata")
        # NULL, not "": metadata is JSONB under PostgreSQL and an empty
        # string is not valid JSON, so "" made EVERY metadata-free insert
        # fail with InvalidTextRepresentation.
        # PostgreSQL: JSONB rejects "" (not valid JSON), so absent -> NULL.
        # SQLite: the column is TEXT NOT NULL, so absent -> "".
        # One engine's correct value is the other's constraint violation.
        from app.config import DB_BACKEND
        meta_json = None if DB_BACKEND == "postgres" else ""
        if meta is not None:
            meta_json = json.dumps(_redact(meta), ensure_ascii=False, default=str)
            if len(meta_json) > MAX_METADATA_CHARS:
                # Truncation must also leave VALID JSON — a string cut
                # mid-object is rejected by jsonb the same way.
                meta_json = json.dumps(
                    {"_truncated": True,
                     "_preview": meta_json[:MAX_METADATA_CHARS]},
                    ensure_ascii=False)

        row = {
            "created_at": _now(),
            "level": lvl,
            "category": cat,
            "subcategory": scrub_text(fields.get("subcategory", ""))[:80],
            "event_name": scrub_text(event_name)[:120],
            "message": scrub_text(message)[:MAX_MESSAGE_CHARS],
            "outcome": scrub_text(fields.get("outcome", ""))[:40],
            "actor": scrub_text(fields.get("actor") or _actor.get())[:120],
            "actor_type": scrub_text(fields.get("actor_type", ""))[:40],
            "target": scrub_text(fields.get("target", ""))[:200],
            "ip": scrub_text(fields.get("ip") or _actor_ip.get())[:64],
            "user_agent": scrub_text(fields.get("user_agent", ""))[:200],
            "provider": scrub_text(fields.get("provider", ""))[:60],
            "model": scrub_text(fields.get("model", ""))[:80],
            "route": scrub_text(fields.get("route", ""))[:200],
            "http_method": scrub_text(fields.get("http_method", ""))[:10],
            "http_status": _as_int(fields.get("http_status")),
            "duration_ms": _as_int(fields.get("duration_ms")),
            "tokens_in": _as_int(fields.get("tokens_in")),
            "tokens_out": _as_int(fields.get("tokens_out")),
            "cost": _as_float(fields.get("cost")),
            "retry_count": _as_int(fields.get("retry_count")),
            "error_type": scrub_text(fields.get("error_type", ""))[:120],
            "error_code": scrub_text(fields.get("error_code", ""))[:80],
            "stack": scrub_text(fields.get("stack", ""))[:MAX_STACK_CHARS],
            "request_id": scrub_text(fields.get("request_id") or _request_id.get())[:64],
            "correlation_id": scrub_text(
                fields.get("correlation_id") or _correlation_id.get())[:64],
            "conversation_id": scrub_text(fields.get("conversation_id", ""))[:64],
            "metadata": meta_json,
        }

        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        # try/finally is load-bearing, not tidiness. Under PostgreSQL,
        # `close()` is what hands the connection back to the pool — so if the
        # INSERT or the commit raised, the old code leaked a POOLED connection
        # still carrying an aborted transaction. The AI engine logs
        # `llm.route.selected` immediately before awaiting the provider, so a
        # leaked connection was then held for the entire external HTTP call.
        # Repeated log failures walked the pool to exhaustion and took down
        # every request in the app, not just AI. Observed growing 2 -> 5 live
        # connections during review.
        conn = get_logs_connection()
        try:
            cur = conn.execute(
                f"INSERT INTO {table_for(cat)} ({cols}) VALUES ({marks})",
                list(row.values()))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — see rule 1
        logger.error("[applog] dropped %s/%s: %s", category, event_name, type(e).__name__)
        return None


def _as_int(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _as_float(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


# ── Convenience façade ──────────────────────────────────────────────────
# One import, one call style, everywhere in the app.

def debug(category, event, message="", **f):    return record(category, event, "debug", message, **f)
def info(category, event, message="", **f):     return record(category, event, "info", message, **f)
def warning(category, event, message="", **f):  return record(category, event, "warning", message, **f)
def error(category, event, message="", **f):    return record(category, event, "error", message, **f)
def critical(category, event, message="", **f): return record(category, event, "critical", message, **f)


def security(event, message="", level="warning", **f):
    """A security event. Never suppressed, never expired by the ops setting."""
    return record("security", event, level, message, **f)


def audit(event, message="", actor="", target="", outcome="ok", level="notice", **f):
    """A sensitive administrative action. Evidence — protected retention."""
    return record("audit", event, level, message,
                  actor=actor, target=target, outcome=outcome, **f)


def service(event, message="", level="info", **f):
    return record("service", event, level, message, **f)


def exception(category, event, exc, message="", **f):
    """Normalise any exception into the error columns.

    The stack goes in `stack`, which the API only exposes to an operator — a
    traceback is a map of the codebase and does not belong in a public error.
    """
    import traceback
    return record(category, event, "error", message or str(exc),
                  error_type=type(exc).__name__,
                  stack="".join(traceback.format_exception(
                      type(exc), exc, exc.__traceback__))[-MAX_STACK_CHARS:],
                  **f)


# ── Settings ────────────────────────────────────────────────────────────

def _setting(key: str, default: str = "") -> str:
    try:
        from app.db.queries import get_setting
        return (get_setting(key, "") or "").strip() or default
    except Exception:  # noqa: BLE001
        return default


def retention_days() -> int:
    """Operational retention. 0 = keep forever (explicit operator choice)."""
    try:
        return max(0, int(_setting("log_retention_days", str(DEFAULT_RETENTION_DAYS))))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def audit_retention_days() -> int:
    try:
        return max(0, int(_setting("log_audit_retention_days",
                                   str(DEFAULT_AUDIT_RETENTION_DAYS))))
    except ValueError:
        return DEFAULT_AUDIT_RETENTION_DAYS


def security_retention_days() -> int:
    try:
        return max(0, int(_setting("log_security_retention_days",
                                   str(DEFAULT_SECURITY_RETENTION_DAYS))))
    except ValueError:
        return DEFAULT_SECURITY_RETENTION_DAYS


def debug_enabled() -> bool:
    return _setting("log_debug_enabled", "false") == "true"


def min_level() -> str:
    lvl = _setting("log_min_level", "info")
    return lvl if lvl in LEVELS else "info"


def content_policy() -> str:
    """How much conversation/LLM content may be persisted.

    metadata | redacted | full — default is the safe one. `full` is an
    explicit, auditable operator decision, not a default.
    """
    policy = _setting("log_content_policy", "redacted")
    return policy if policy in ("metadata", "redacted", "full") else "redacted"


def apply_content_policy(text: str) -> str:
    """Filter user/LLM content through the configured policy."""
    policy = content_policy()
    if policy == "metadata":
        return ""
    if policy == "full":
        # Still credential-scrubbed. "full" means full CONTENT, never secrets.
        return scrub_text(text)[:MAX_MESSAGE_CHARS]
    return scrub_text(text)[:200]


# ── Reading ─────────────────────────────────────────────────────────────
_SORTABLE = {"created_at", "level", "category", "event_name", "duration_ms",
             "http_status", "actor", "id"}


def query(category: str = "", level: str = "", q: str = "", since: str = "",
          until: str = "", actor: str = "", ip: str = "", provider: str = "",
          model: str = "", request_id: str = "", correlation_id: str = "",
          conversation_id: str = "", http_status: str = "", outcome: str = "",
          min_duration: str = "", sort: str = "created_at", direction: str = "desc",
          limit: int = 50, offset: int = 0, tables=None):
    """Filtered page across one or more tables. Returns (rows, total).

    Every value is bound as a parameter; `sort` is validated against an
    allowlist. No caller-supplied string ever reaches the SQL text.
    """
    try:
        if tables is None:
            tables = [table_for(category)] if category else list(TABLES)
        tables = [t for t in tables if t in TABLES] or ["app_logs"]

        where, params = [], []

        def eq(col, val, maxlen=200):
            if val:
                where.append(f"{col} = ?")
                params.append(str(val)[:maxlen])

        eq("category", category if category in CATEGORIES else "")
        eq("level", level if level in LEVELS else "")
        eq("actor", actor)
        eq("ip", ip, 64)
        eq("provider", provider, 60)
        eq("model", model, 80)
        eq("request_id", request_id, 64)
        eq("correlation_id", correlation_id, 64)
        eq("conversation_id", conversation_id, 64)
        eq("outcome", outcome, 40)
        if _as_int(http_status) is not None:
            where.append("http_status = ?")
            params.append(_as_int(http_status))
        if _as_int(min_duration) is not None:
            where.append("duration_ms >= ?")
            params.append(_as_int(min_duration))
        if since:
            where.append("created_at >= ?")
            params.append(str(since)[:40])
        if until:
            where.append("created_at <= ?")
            params.append(str(until)[:40])
        if q:
            # metadata is JSONB under PostgreSQL and TEXT under SQLite; the
            # CAST is a no-op on SQLite and is what makes LIKE legal on JSONB.
            where.append("(event_name LIKE ? OR message LIKE ? OR actor LIKE ?"
                         " OR error_type LIKE ? OR target LIKE ?"
                         " OR CAST(metadata AS TEXT) LIKE ?)")
            like = f"%{str(q)[:200]}%"
            params.extend([like] * 6)

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sort_col = sort if sort in _SORTABLE else "created_at"
        sort_dir = "ASC" if str(direction).lower() == "asc" else "DESC"
        limit = max(1, min(_as_int(limit) or 50, 500))
        offset = max(0, _as_int(offset) or 0)

        cols = ("id, created_at, level, category, subcategory, event_name, message,"
                " outcome, actor, actor_type, target, ip, user_agent, provider, model,"
                " route, http_method, http_status, duration_ms, tokens_in, tokens_out,"
                " cost, retry_count, error_type, error_code, stack, request_id,"
                " correlation_id, conversation_id, metadata")
        union = " UNION ALL ".join(
            f"SELECT {cols}, '{t}' AS source FROM {t}{clause}" for t in tables)
        count_union = " UNION ALL ".join(
            f"SELECT id FROM {t}{clause}" for t in tables)
        all_params = params * len(tables)

        conn = get_logs_connection()
        total = conn.execute(
            f"SELECT COUNT(*) FROM ({count_union})", all_params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM ({union}) ORDER BY {sort_col} {sort_dir}, id {sort_dir}"
            f" LIMIT ? OFFSET ?", all_params + [limit, offset]).fetchall()
        conn.close()
        return [dict(r) for r in rows], total
    except Exception as e:  # noqa: BLE001
        logger.error("[applog] query failed: %s: %s", type(e).__name__, e)
        return [], 0


def get_row(row_id: int, table: str = ""):
    """One row by id. Searches every table when `table` is not given."""
    try:
        candidates = [table] if table in TABLES else list(TABLES)
        conn = get_logs_connection()
        for t in candidates:
            r = conn.execute(f"SELECT *, '{t}' AS source FROM {t} WHERE id = ?",
                             (row_id,)).fetchone()
            if r:
                conn.close()
                return dict(r)
        conn.close()
        return None
    except Exception as e:  # noqa: BLE001
        logger.error("[applog] get_row failed: %s", type(e).__name__)
        return None


def related(row: dict, limit: int = 200):
    """Every event sharing this row's correlation / request / conversation id.

    This is what turns a pile of rows into an incident timeline.
    """
    for key in ("correlation_id", "request_id", "conversation_id"):
        value = (row or {}).get(key) or ""
        if value:
            rows, _ = query(**{key: value}, limit=limit, tables=list(TABLES),
                            sort="created_at", direction="asc")
            if len(rows) > 1:
                return rows, key
    return [], ""


def summary(days: int = 1):
    """Numbers for the overview page. All computed in SQL, never in Python."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))
                 ).isoformat(timespec="seconds")
        conn = get_logs_connection()
        out = {"window_days": days, "by_category": {}, "by_level": {},
               "totals": {}, "top_errors": [], "providers": {}}
        for t in TABLES:
            out["totals"][t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for r in conn.execute(
                    f"SELECT category, level, COUNT(*) n FROM {t}"
                    f" WHERE created_at >= ? GROUP BY category, level", (since,)):
                out["by_category"].setdefault(r["category"], {})[r["level"]] = r["n"]
                out["by_level"][r["level"]] = out["by_level"].get(r["level"], 0) + r["n"]
        out["top_errors"] = [dict(r) for r in conn.execute(
            "SELECT category, event_name, error_type, COUNT(*) n FROM app_logs"
            " WHERE level IN ('error','critical','alert','emergency') AND created_at >= ?"
            " GROUP BY category, event_name, error_type ORDER BY n DESC LIMIT 10",
            (since,))]
        out["providers"] = [dict(r) for r in conn.execute(
            "SELECT provider, COUNT(*) n, AVG(duration_ms) avg_ms,"
            " SUM(CASE WHEN level IN ('error','critical') THEN 1 ELSE 0 END) errors,"
            " SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)) tokens"
            " FROM app_logs WHERE provider <> '' AND created_at >= ?"
            " GROUP BY provider ORDER BY n DESC LIMIT 20", (since,))]
        row = conn.execute(
            "SELECT MIN(created_at) a, MAX(created_at) b FROM app_logs").fetchone()
        out["oldest"], out["newest"] = row["a"], row["b"]
        conn.close()
        out["retention"] = {
            "operational_days": retention_days(),
            "audit_days": audit_retention_days(),
            "security_days": security_retention_days(),
        }
        out["storage_bytes"] = _db_size()
        return out
    except Exception as e:  # noqa: BLE001
        logger.error("[applog] summary failed: %s: %s", type(e).__name__, e)
        return {"by_category": {}, "by_level": {}, "totals": {}, "top_errors": [],
                "providers": [], "retention": {}, "storage_bytes": 0}


def _db_size() -> int:
    try:
        import os
        from app.config import LOGS_DB_PATH
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = LOGS_DB_PATH + suffix
            if os.path.exists(p):
                total += os.path.getsize(p)
        return total
    except Exception:  # noqa: BLE001
        return 0


# ── Retention and deletion ──────────────────────────────────────────────

def purge_expired() -> dict:
    """Delete rows past their class's retention window.

    Three independent windows, so lowering the operational setting can never
    erase audit or security evidence.
    """
    removed = {}
    plan = (("app_logs", retention_days()),
            ("service_events", retention_days()),
            ("audit_logs", audit_retention_days()),
            ("security_events", security_retention_days()))
    try:
        conn = get_logs_connection()
        for table, days in plan:
            if days <= 0:
                continue
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)
                      ).isoformat(timespec="seconds")
            cur = conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
            if cur.rowcount:
                removed[table] = cur.rowcount
        conn.commit()
        conn.close()
        if removed:
            logger.info("[applog] retention purge removed %s", removed)
    except Exception as e:  # noqa: BLE001
        logger.error("[applog] purge failed: %s", type(e).__name__)
    return removed


def count_matching(category: str = "", before: str = "", level: str = "",
                   table: str = "") -> int:
    """How many rows a truncate WOULD remove — for the confirmation dialog."""
    tables = [table] if table in TABLES else (
        [table_for(category)] if category else list(TABLES))
    n = 0
    try:
        conn = get_logs_connection()
        for t in tables:
            where, params = [], []
            if category in CATEGORIES:
                where.append("category = ?")
                params.append(category)
            if level in LEVELS:
                where.append("level = ?")
                params.append(level)
            if before:
                where.append("created_at < ?")
                params.append(str(before)[:40])
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            n += conn.execute(f"SELECT COUNT(*) FROM {t}{clause}", params).fetchone()[0]
        conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("[applog] count_matching failed: %s", type(e).__name__)
    return n


def truncate(category: str = "", before: str = "", level: str = "",
             table: str = "") -> int:
    """Operator-triggered delete. Returns rows removed.

    The CALLER must write the audit row — and because audit_logs is a separate
    table with its own retention, a truncate of operational logs can never
    remove the record of itself.
    """
    tables = [table] if table in TABLES else (
        [table_for(category)] if category else list(TABLES))
    total = 0
    try:
        conn = get_logs_connection()
        for t in tables:
            where, params = [], []
            if category in CATEGORIES:
                where.append("category = ?")
                params.append(category)
            if level in LEVELS:
                where.append("level = ?")
                params.append(level)
            if before:
                where.append("created_at < ?")
                params.append(str(before)[:40])
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            cur = conn.execute(f"DELETE FROM {t}{clause}", params)
            total += cur.rowcount or 0
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("[applog] truncate failed: %s", type(e).__name__)
        return 0
    return total


# ── Export ──────────────────────────────────────────────────────────────
EXPORT_FIELDS = ("id", "created_at", "level", "category", "subcategory",
                 "event_name", "message", "outcome", "actor", "actor_type",
                 "target", "ip", "provider", "model", "route", "http_method",
                 "http_status", "duration_ms", "tokens_in", "tokens_out",
                 "retry_count", "error_type", "error_code", "request_id",
                 "correlation_id", "conversation_id", "metadata")


def iter_rows(cap: int = 100000, **filters):
    """Paged generator so a 90-day export never loads the table into memory."""
    page, sent = 500, 0
    filters.pop("limit", None)
    filters.pop("offset", None)
    while sent < cap:
        rows, _ = query(limit=min(page, cap - sent), offset=sent, **filters)
        if not rows:
            return
        for r in rows:
            yield r
        sent += len(rows)
        if len(rows) < page:
            return


def _csv_cell(value) -> str:
    """Neutralise spreadsheet formula injection.

    A visitor can put `=cmd|'/c calc'!A1` in a user-agent. Excel would execute
    it on open. Prefixing with an apostrophe makes it text.
    """
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t") else s


def export_csv(cap: int = 100000, **filters):
    """Yields a UTF-8 CSV with a BOM so Excel renders Persian correctly."""
    yield "﻿"
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(EXPORT_FIELDS)
    yield buf.getvalue()
    for row in iter_rows(cap=cap, **filters):
        buf.seek(0), buf.truncate(0)
        writer.writerow([_csv_cell(row.get(f)) for f in EXPORT_FIELDS])
        yield buf.getvalue()


def export_json(cap: int = 100000, **filters):
    """Streams a JSON array without building it in memory."""
    yield "[\n"
    first = True
    for row in iter_rows(cap=cap, **filters):
        yield ("" if first else ",\n") + json.dumps(
            {f: row.get(f) for f in EXPORT_FIELDS}, ensure_ascii=False, default=str)
        first = False
    yield "\n]\n"
