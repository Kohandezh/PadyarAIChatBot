"""AI control-plane data access: instances, routes, catalog, pricing, usage.

Runs on the same `get_db_connection()` surface as the rest of the app, so the
whole thing works on PostgreSQL in production and SQLite in the test suite.
`ensure_ai_tables()` is the SQLite mirror of migrations/0003 — the two must
stay in sync (same columns, same constraints, minus PG-only types).

SECURITY RULES ENFORCED HERE
----------------------------
* `secret_enc` is written via secure_store.protect() and is NEVER returned
  by a read function. Public rows expose `has_secret` instead. The only
  reader of the secret is `_reveal_secret()` on the way into a runtime.
* Admin mutations go through functions that also write audit rows — there is
  no raw UPDATE path an endpoint could accidentally use un-audited.
* Route reordering is a single transaction with a two-phase priority offset,
  so no observer can see duplicate or gapped priorities (the UNIQUE(task,
  priority) constraint would reject a naive swap mid-flight).
"""
import json
import secrets as _secrets
import threading

from app.config import logger

from . import errors as ai_errors
from .adapters import adapter_for, AI_PROVIDER_REGISTRY
from .adapters import bootstrap as bootstrap_mod
from .adapters.base import ProviderRuntime

# ── SQLite DDL for tests (mirror of migrations/0003_ai_control_plane.sql) ──
#
# Flags are declared BOOLEAN so this mirror and the migration say the same
# thing. SQLite does not enforce it: BOOLEAN carries NUMERIC affinity, the
# engine does no type checking, and Python `True` and `1` are both stored as
# `integer`. The declaration buys agreement with the migration and a reader
# who is not told the column is an integer when production says otherwise. An
# int bound to one of these is caught only against a real server, in
# tests/postgres/. Timestamps stay TEXT and JSONB stays TEXT because SQLite
# has neither type.

_TABLES = {
    "ai_provider_instances": """
        CREATE TABLE IF NOT EXISTS ai_provider_instances (
            id TEXT PRIMARY KEY,
            provider_type TEXT NOT NULL,
            display_name TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            trust_class TEXT NOT NULL DEFAULT 'public'
                CHECK (trust_class IN ('public','internal')),
            config TEXT NOT NULL DEFAULT '{}',
            secret_enc TEXT NOT NULL DEFAULT '',
            has_secret BOOLEAN NOT NULL DEFAULT FALSE,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT ''
        )""",
    "ai_provider_models": """
        CREATE TABLE IF NOT EXISTS ai_provider_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_instance_id TEXT NOT NULL REFERENCES ai_provider_instances(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'bootstrap'
                CHECK (source IN ('bootstrap','discovered','manual')),
            status TEXT NOT NULL DEFAULT 'available'
                CHECK (status IN ('available','preview','deprecated','legacy',
                                  'unavailable','unknown','manual')),
            supports_chat BOOLEAN NOT NULL DEFAULT TRUE,
            supports_reasoning BOOLEAN NOT NULL DEFAULT FALSE,
            supports_streaming BOOLEAN NOT NULL DEFAULT TRUE,
            supports_tools BOOLEAN NOT NULL DEFAULT FALSE,
            supports_structured BOOLEAN NOT NULL DEFAULT FALSE,
            supports_vision BOOLEAN NOT NULL DEFAULT FALSE,
            context_window INTEGER,
            max_output_tokens INTEGER,
            metadata TEXT,
            metadata_refreshed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (provider_instance_id, model_id)
        )""",
    "ai_routes": """
        CREATE TABLE IF NOT EXISTS ai_routes (
            task TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    "ai_route_targets": """
        CREATE TABLE IF NOT EXISTS ai_route_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL REFERENCES ai_routes(task) ON DELETE CASCADE,
            provider_instance_id TEXT NOT NULL REFERENCES ai_provider_instances(id),
            model_id TEXT NOT NULL,
            priority INTEGER NOT NULL CHECK (priority > 0),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            max_attempts INTEGER CHECK (max_attempts >= 1),
            timeout_s REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (task, provider_instance_id, model_id),
            UNIQUE (task, priority)
        )""",
    "ai_circuit_state": """
        CREATE TABLE IF NOT EXISTS ai_circuit_state (
            provider_instance_id TEXT PRIMARY KEY REFERENCES ai_provider_instances(id) ON DELETE CASCADE,
            state TEXT NOT NULL DEFAULT 'closed'
                CHECK (state IN ('closed','open','half_open')),
            failure_count INTEGER NOT NULL DEFAULT 0,
            window_started_at TEXT,
            last_failure_at TEXT,
            last_failure_code TEXT NOT NULL DEFAULT '',
            last_success_at TEXT,
            opened_at TEXT,
            cooldown_until TEXT,
            probe_owner TEXT NOT NULL DEFAULT '',
            probe_expires_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    "ai_model_pricing": """
        CREATE TABLE IF NOT EXISTS ai_model_pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_type TEXT NOT NULL,
            model_id TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            input_per_million REAL NOT NULL,
            cached_input_per_million REAL,
            output_per_million REAL NOT NULL,
            effective_from TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    "ai_usage_events": """
        CREATE TABLE IF NOT EXISTS ai_usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            task TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('success','failed')),
            provider_type TEXT NOT NULL DEFAULT '',
            provider_instance_id TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 1,
            failovers INTEGER NOT NULL DEFAULT 0,
            tokens_in INTEGER,
            tokens_out INTEGER,
            tokens_cached INTEGER,
            tokens_total INTEGER,
            latency_ms INTEGER,
            cost REAL,
            currency TEXT NOT NULL DEFAULT '',
            pricing_effective_from TEXT,
            error_code TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            correlation_id TEXT NOT NULL DEFAULT '',
            metadata TEXT
        )""",
}

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_ai_providers_type ON ai_provider_instances(provider_type)",
    "CREATE INDEX IF NOT EXISTS ix_ai_providers_enabled ON ai_provider_instances(enabled, provider_type)",
    "CREATE INDEX IF NOT EXISTS ix_ai_models_instance ON ai_provider_models(provider_instance_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_ai_route_targets_order ON ai_route_targets(task, priority)",
    "CREATE INDEX IF NOT EXISTS ix_ai_pricing_lookup ON ai_model_pricing(provider_type, model_id, effective_from DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ai_usage_created ON ai_usage_events(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ai_usage_provider ON ai_usage_events(provider_instance_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ai_usage_task ON ai_usage_events(task, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ai_usage_model ON ai_usage_events(model, created_at DESC)",
)

_KNOWN_TASKS = ("chat", "classify")


def ensure_ai_tables() -> None:
    """Create the control-plane tables on SQLite (test suite / rollback path).

    On PostgreSQL the schema is owned by migrations/ and this is a no-op —
    runtime DDL must never compete with the migration runner.
    """
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        return
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        for ddl in _TABLES.values():
            conn.execute(ddl)
        for ix in _INDEXES:
            conn.execute(ix)
        for task in _KNOWN_TASKS:
            conn.execute(
                "INSERT OR IGNORE INTO ai_routes (task) VALUES (?)", (task,))
        conn.commit()
    except Exception as e:  # noqa: BLE001 — never break boot
        logger.error("[ai] ensure_ai_tables failed: %s", type(e).__name__)
    finally:
        conn.close()


def seed_bootstrap_pricing() -> None:
    """Insert bootstrap pricing rows once (idempotent by source marker)."""
    from app.db.connection import get_db_connection
    from app.db.queries import get_setting, set_setting
    if get_setting("ai_bootstrap_pricing_seeded", "") == "1":
        return
    conn = get_db_connection()
    try:
        for (ptype, mid, cur, in_, cin, out) in bootstrap_mod.pricing_rows():
            conn.execute(
                "INSERT INTO ai_model_pricing (provider_type, model_id, currency,"
                " input_per_million, cached_input_per_million, output_per_million,"
                " effective_from, source) VALUES (?,?,?,?,?,?,?,?)",
                (ptype, mid, cur, in_, cin, out,
                 bootstrap_mod.EFFECTIVE_FROM.isoformat(), bootstrap_mod.SOURCE))
        conn.commit()
        set_setting("ai_bootstrap_pricing_seeded", "1")
    finally:
        conn.close()


# ── Provider instances ──────────────────────────────────────────────────

_PUBLIC_COLS = ("id, provider_type, display_name, enabled, trust_class, config,"
                " has_secret, notes, created_at, updated_at, created_by")


def _load_json(v):
    """Read a config/metadata column on EITHER backend.

    SQLite stores TEXT → json.loads(str). PostgreSQL JSONB comes back from
    psycopg ALREADY PARSED as a dict/list — json.loads(dict) raises TypeError
    and a naive `except → {}` silently wipes the provider's configuration.
    """
    if v is None or v == "":
        return {}
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return {}


def _row_to_instance(row) -> dict:
    cfg = _load_json(row["config"])
    return {
        "id": row["id"], "provider_type": row["provider_type"],
        "display_name": row["display_name"],
        "enabled": bool(row["enabled"]),
        "trust_class": row["trust_class"],
        "config": cfg,
        "has_secret": bool(row["has_secret"]),
        "notes": row["notes"] or "",
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "created_by": row["created_by"] or "",
    }


def list_instances() -> list:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"SELECT {_PUBLIC_COLS} FROM ai_provider_instances ORDER BY display_name"
        ).fetchall()
        return [_row_to_instance(r) for r in rows]
    finally:
        conn.close()


def get_instance(instance_id: str) -> dict | None:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            f"SELECT {_PUBLIC_COLS} FROM ai_provider_instances WHERE id = ?",
            (instance_id,)).fetchone()
        return _row_to_instance(row) if row else None
    finally:
        conn.close()


def _slugify(name: str) -> str:
    out = []
    for ch in (name or "").strip().lower():
        out.append(ch if ch.isalnum() or ch in "-_" else "-")
    slug = "".join(out).strip("-")[:40] or "provider"
    return f"{slug}-{_secrets.token_hex(3)}"


def create_instance(provider_type: str, display_name: str, config: dict,
                    secret: str, enabled: bool = False,
                    trust_class: str = "public", notes: str = "",
                    actor: str = "") -> str:
    """Create a provider instance. New instances are saved DISABLED unless
    explicitly enabled — traffic must never flow to an untested provider by
    accident (admin flow: save → test → enable → route)."""
    if provider_type not in AI_PROVIDER_REGISTRY:
        raise ai_errors.AIError(code="invalid_request",
                                provider_detail=f"unknown provider type {provider_type!r}")
    from app.db.connection import get_db_connection
    from app.services import secure_store, applog
    adapter = adapter_for(provider_type)
    cleaned = adapter.validate_config(config or {}, trust_class)
    # A schema field marked required+password means "this provider cannot be
    # configured without a key" — enforced against the secret column here.
    if not secret and any(f.required and f.type == "password"
                          for f in adapter.configuration_schema()):
        raise ai_errors.AIError(code="invalid_request",
                                provider_detail="this provider requires an API key")
    instance_id = _slugify(display_name or provider_type)
    secret_enc = secure_store.protect(secret) if secret else ""
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO ai_provider_instances (id, provider_type, display_name,"
            " enabled, trust_class, config, secret_enc, has_secret, notes, created_by)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (instance_id, provider_type, display_name or provider_type,
             bool(enabled), trust_class,
             json.dumps(cleaned, ensure_ascii=False), secret_enc,
             bool(secret_enc), notes, actor))
        conn.commit()
    finally:
        conn.close()
    seed_models_for_instance(instance_id, provider_type)
    applog.audit("admin.ai_provider.created", f"سرویس‌دهندهٔ {display_name} ساخته شد",
                 actor=actor or "admin", target=instance_id, outcome="ok")
    return instance_id


def update_instance(instance_id: str, *, display_name=None, config=None,
                    secret=None, enabled=None, trust_class=None, notes=None,
                    actor: str = "") -> None:
    from app.db.connection import get_db_connection
    from app.services import secure_store, applog
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM ai_provider_instances WHERE id = ?",
                           (instance_id,)).fetchone()
        if not row:
            raise ai_errors.AIError(code="invalid_request",
                                    provider_detail="provider instance not found")
        sets, params = ["updated_at = datetime('now')", "updated_by = ?"], [actor]
        ptype = row["provider_type"]
        tclass = trust_class or row["trust_class"]
        if config is not None:
            cleaned = adapter_for(ptype).validate_config(config, tclass)
            sets.append("config = ?")
            params.append(json.dumps(cleaned, ensure_ascii=False))
        if secret is not None:
            enc = secure_store.protect(secret) if secret else ""
            sets += ["secret_enc = ?", "has_secret = ?"]
            params += [enc, bool(enc)]
        if display_name is not None:
            sets.append("display_name = ?")
            params.append(display_name)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(bool(enabled))
        if trust_class is not None:
            sets.append("trust_class = ?")
            params.append(trust_class)
        if notes is not None:
            sets.append("notes = ?")
            params.append(notes)
        params.append(instance_id)
        conn.execute(f"UPDATE ai_provider_instances SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()
    _invalidate_runtime(instance_id)
    applog.audit("admin.ai_provider.updated", f"سرویس‌دهنده به‌روزرسانی شد",
                 actor=actor or "admin", target=instance_id, outcome="ok")
    if secret is not None:
        applog.audit("admin.ai_provider.secret_changed", "کلید سرویس‌دهنده تغییر کرد",
                     actor=actor or "admin", target=instance_id, outcome="ok")


def set_enabled(instance_id: str, enabled: bool, actor: str = "") -> None:
    update_instance(instance_id, enabled=enabled, actor=actor)
    from app.services import applog
    applog.audit("admin.ai_provider.enabled" if enabled else "admin.ai_provider.disabled",
                 "وضعیت سرویس‌دهنده تغییر کرد", actor=actor or "admin",
                 target=instance_id, outcome="ok")


def delete_instance(instance_id: str, actor: str = "") -> None:
    """Delete with route integrity: refuses while route targets reference it.

    Disabling is preferred; deletion is a deliberate act with confirmation,
    and it must not leave dangling route references. Historical usage rows
    are NOT cascade-deleted (no FK from usage → instance by design)."""
    from app.db.connection import get_db_connection
    from app.services import applog
    conn = get_db_connection()
    try:
        refs = conn.execute(
            "SELECT task, model_id FROM ai_route_targets WHERE provider_instance_id = ?",
            (instance_id,)).fetchall()
        if refs:
            raise ai_errors.AIError(
                code="invalid_request",
                provider_detail="provider is referenced by routes: "
                + ", ".join(f"{r['task']}/{r['model_id']}" for r in refs))
        conn.execute("DELETE FROM ai_provider_models WHERE provider_instance_id = ?",
                     (instance_id,))
        conn.execute("DELETE FROM ai_circuit_state WHERE provider_instance_id = ?",
                     (instance_id,))
        deleted = conn.execute("DELETE FROM ai_provider_instances WHERE id = ?",
                               (instance_id,)).rowcount
        conn.commit()
    finally:
        conn.close()
    _invalidate_runtime(instance_id)
    if deleted:
        applog.audit("admin.ai_provider.deleted", "سرویس‌دهنده حذف شد",
                     actor=actor or "admin", target=instance_id, outcome="ok")


# ── Runtime resolution (config + secret) ────────────────────────────────
# Short-lived cache so a chat request does not re-read the provider row per
# call; PostgreSQL stays authoritative and admin mutations invalidate at
# once via updated_at fingerprint + explicit invalidation.

_RT_CACHE: dict = {}
_RT_TTL = 20.0
_RT_LOCK = threading.Lock()


def _now_ts():
    import time as _t
    return _t.monotonic()


def runtime_for(instance_id: str) -> ProviderRuntime | None:
    """Resolve a provider instance into an adapter runtime (secret revealed).

    The secret exists ONLY inside the returned object (process memory, short
    lifetime). It is never logged, never echoed, never stored elsewhere.
    """
    from app.db.connection import get_db_connection
    from app.services import secure_store
    from app.services import applog
    hit = _RT_CACHE.get(instance_id)
    if hit and _now_ts() - hit[0] < _RT_TTL:
        # Register on the cache-hit path too. Registration is per-task
        # (ContextVar), while the cache is process-wide and outlives any one
        # request — so a cached hit would otherwise serve the secret to a task
        # that never declared it, and redaction would silently not apply.
        applog.register_secret(hit[1].secret)
        return hit[1]
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, provider_type, display_name, enabled, trust_class, config,"
            " secret_enc, updated_at FROM ai_provider_instances WHERE id = ?",
            (instance_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        _RT_CACHE.pop(instance_id, None)
        return None
    cfg = _load_json(row["config"])
    rt = ProviderRuntime(
        instance_id=row["id"], provider_type=row["provider_type"],
        display_name=row["display_name"], enabled=bool(row["enabled"]),
        trust_class=row["trust_class"], config=cfg,
        secret=secure_store.reveal(row["secret_enc"] or ""),
    )
    # The moment the plaintext exists, declare it, so anything this provider
    # echoes back at us is stripped from logs, audit rows and API responses
    # whatever shape that vendor's keys take.
    applog.register_secret(rt.secret)
    with _RT_LOCK:
        if len(_RT_CACHE) > 64:
            _RT_CACHE.clear()
        _RT_CACHE[instance_id] = (_now_ts(), rt)
    return rt


def _invalidate_runtime(instance_id: str = "") -> None:
    with _RT_LOCK:
        if instance_id:
            _RT_CACHE.pop(instance_id, None)
        else:
            _RT_CACHE.clear()


# ── Model catalog ───────────────────────────────────────────────────────

def seed_models_for_instance(instance_id: str, provider_type: str) -> None:
    """Seed the bootstrap catalog for a newly created instance."""
    models = bootstrap_mod.BOOTSTRAP_MODELS.get(provider_type, [])
    if not models:
        return
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        for m in models:
            conn.execute(
                "INSERT OR IGNORE INTO ai_provider_models"
                " (provider_instance_id, model_id, display_name, source, status,"
                "  supports_reasoning, supports_tools, supports_structured,"
                "  supports_vision, context_window, max_output_tokens, metadata)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (instance_id, m["id"], m["name"], "bootstrap", m["status"],
                 bool(m.get("reasoning")), bool(m.get("tools")),
                 bool(m.get("structured")), bool(m.get("vision")),
                 m.get("ctx"), m.get("maxout"),
                 json.dumps({"bootstrap": True}, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def list_models(instance_id: str = "") -> list:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        if instance_id:
            rows = conn.execute(
                "SELECT * FROM ai_provider_models WHERE provider_instance_id = ?"
                " ORDER BY status, model_id", (instance_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_provider_models ORDER BY provider_instance_id, model_id"
            ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        meta = _load_json(r["metadata"])
        out.append({
            "id": r["id"], "provider_instance_id": r["provider_instance_id"],
            "model_id": r["model_id"], "display_name": r["display_name"],
            "source": r["source"], "status": r["status"],
            "supports_chat": bool(r["supports_chat"]),
            "supports_reasoning": bool(r["supports_reasoning"]),
            "supports_streaming": bool(r["supports_streaming"]),
            "supports_tools": bool(r["supports_tools"]),
            "supports_structured": bool(r["supports_structured"]),
            "supports_vision": bool(r["supports_vision"]),
            "context_window": r["context_window"],
            "max_output_tokens": r["max_output_tokens"],
            "metadata": meta,
            "metadata_refreshed_at": r["metadata_refreshed_at"],
        })
    return out


def add_manual_model(instance_id: str, model_id: str, display_name: str = "",
                     context_window=None, max_output_tokens=None) -> None:
    """Validated manual entry — mandatory where discovery does not exist
    (Z.AI, Qwen) and always allowed. Marked source=manual, status=manual."""
    model_id = (model_id or "").strip()
    if not model_id or len(model_id) > 200:
        raise ai_errors.AIError(code="invalid_request",
                                provider_detail="invalid model id")
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ai_provider_models"
            " (provider_instance_id, model_id, display_name, source, status,"
            "  context_window, max_output_tokens, metadata)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (instance_id, model_id, display_name or model_id, "manual", "manual",
             context_window, max_output_tokens, json.dumps({"manual": True})))
        conn.commit()
    finally:
        conn.close()


def apply_discovery(instance_id: str, discovered: list) -> dict:
    """Merge a discovery result into the catalog.

    New ids are inserted; known ids are updated; ids that vanished are marked
    status='unavailable' — NEVER deleted, so usage history and logs stay
    interpretable. Manual and bootstrap rows are left untouched by a
    disappearance (only their discovered siblings downgrade).

    A MANUAL row is never modified at all — see `_PRESERVED_SOURCES` below."""
    from datetime import datetime, timezone
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = updated = vanished = preserved = 0
    try:
        existing = {r["model_id"]: r for r in conn.execute(
            "SELECT id, model_id, source FROM ai_provider_models"
            " WHERE provider_instance_id = ?", (instance_id,)).fetchall()}
        seen = set()
        for m in discovered:
            mid = str(m.get("model_id") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            meta = m.get("metadata") or {}
            if mid in existing:
                # A manual row is an OPERATOR ASSERTION, and discovery does not
                # get to overrule it. This matters concretely: the customer's
                # live gateway carries `gpt-4.1` and `gpt-5-nano` as manual rows
                # precisely because nobody knows whether that reseller still
                # serves them. If the gateway happens to list either id, one
                # click of "Refresh" used to rewrite status, display name,
                # context window and every capability flag — silently
                # "upgrading" the deliberate manual marking into a claim we
                # cannot support. `source` survived only because it was absent
                # from the UPDATE list, which made the damage quiet rather than
                # harmless. migrations/0003 already documents this contract
                # ("'manual' rows are never touched by refresh"); the code just
                # did not honour it on the update path.
                if existing[mid]["source"] == "manual":
                    preserved += 1
                    continue
                updated += 1
                conn.execute(
                    "UPDATE ai_provider_models SET display_name=?, status=?,"
                    " supports_reasoning=?, supports_tools=?, supports_structured=?,"
                    " supports_vision=?, context_window=?, max_output_tokens=?,"
                    " metadata=?, metadata_refreshed_at=?,"
                    " updated_at=datetime('now') WHERE provider_instance_id=? AND model_id=?",
                    (m.get("display_name") or mid, m.get("status") or "available",
                     bool(m.get("supports_reasoning")), bool(m.get("supports_tools")),
                     bool(m.get("supports_structured")), bool(m.get("supports_vision")),
                     m.get("context_window"), m.get("max_output_tokens"),
                     json.dumps(meta, ensure_ascii=False), now,
                     instance_id, mid))
            else:
                added += 1
                conn.execute(
                    "INSERT INTO ai_provider_models"
                    " (provider_instance_id, model_id, display_name, source, status,"
                    "  supports_reasoning, supports_tools, supports_structured,"
                    "  supports_vision, context_window, max_output_tokens, metadata,"
                    "  metadata_refreshed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (instance_id, mid, m.get("display_name") or mid, "discovered",
                     m.get("status") or "available",
                     bool(m.get("supports_reasoning")), bool(m.get("supports_tools")),
                     bool(m.get("supports_structured")), bool(m.get("supports_vision")),
                     m.get("context_window"), m.get("max_output_tokens"),
                     json.dumps(meta, ensure_ascii=False), now))
        for mid, row in existing.items():
            if mid not in seen and row["source"] == "discovered":
                vanished += 1
                conn.execute(
                    "UPDATE ai_provider_models SET status='unavailable',"
                    " updated_at=datetime('now')"
                    " WHERE provider_instance_id=? AND model_id=?",
                    (instance_id, mid))
        conn.commit()
    finally:
        conn.close()
    return {"added": added, "updated": updated, "unavailable": vanished,
            "preserved_manual": preserved}


# ── Routes ──────────────────────────────────────────────────────────────

def ordered_targets(task: str) -> list:
    """Route targets in try-order (priority 1 first), with instance joined in
    one query. Only enabled route + enabled instance rows are returned by
    callers' eligibility check; this returns the full ordered picture."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT t.id, t.task, t.provider_instance_id, t.model_id, t.priority,"
            " t.enabled AS target_enabled, t.max_attempts, t.timeout_s,"
            " p.provider_type, p.display_name, p.enabled AS provider_enabled,"
            " p.has_secret"
            " FROM ai_route_targets t JOIN ai_provider_instances p"
            "   ON p.id = t.provider_instance_id"
            " JOIN ai_routes r ON r.task = t.task"
            " WHERE t.task = ? AND r.enabled = TRUE"
            " ORDER BY t.priority", (task,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_routes() -> dict:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        routes = conn.execute("SELECT * FROM ai_routes ORDER BY task").fetchall()
        targets = conn.execute(
            "SELECT t.*, p.display_name AS provider_name, p.provider_type"
            " FROM ai_route_targets t JOIN ai_provider_instances p"
            "   ON p.id = t.provider_instance_id ORDER BY t.task, t.priority"
        ).fetchall()
        return {
            "routes": [dict(r) for r in routes],
            "targets": [{**dict(r),
                         "enabled": bool(r["enabled"])} for r in targets],
        }
    finally:
        conn.close()


def add_target(task: str, provider_instance_id: str, model_id: str,
               max_attempts=None, timeout_s=None, actor: str = "") -> int:
    from app.db.connection import get_db_connection
    from app.services import applog
    if task not in _KNOWN_TASKS:
        raise ai_errors.AIError(code="invalid_request", provider_detail="unknown task")
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT 1 FROM ai_provider_instances WHERE id = ?",
                           (provider_instance_id,)).fetchone()
        if not row:
            raise ai_errors.AIError(code="invalid_request",
                                    provider_detail="provider instance not found")
        # next free priority
        top = conn.execute(
            "SELECT COALESCE(MAX(priority), 0) AS p FROM ai_route_targets WHERE task = ?",
            (task,)).fetchone()
        priority = (top["p"] or 0) + 1
        cur = conn.execute(
            "INSERT INTO ai_route_targets (task, provider_instance_id, model_id,"
            " priority, max_attempts, timeout_s) VALUES (?,?,?,?,?,?)",
            (task, provider_instance_id, model_id, priority, max_attempts, timeout_s))
        target_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    applog.audit("admin.ai_route.updated", f"هدف مسیر اضافه شد: {task}",
                 actor=actor or "admin", target=f"{task}/{provider_instance_id}/{model_id}",
                 outcome="ok")
    return target_id


def remove_target(target_id: int, actor: str = "") -> None:
    from app.db.connection import get_db_connection
    from app.services import applog
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT task, provider_instance_id, model_id, priority"
                           " FROM ai_route_targets WHERE id = ?", (target_id,)).fetchone()
        if not row:
            raise ai_errors.AIError(code="invalid_request", provider_detail="target not found")
        conn.execute("DELETE FROM ai_route_targets WHERE id = ?", (target_id,))
        # close the gap left by the removal, preserving order
        conn.execute(
            "UPDATE ai_route_targets SET priority = priority - 1"
            " WHERE task = ? AND priority > ?", (row["task"], row["priority"]))
        conn.commit()
    finally:
        conn.close()
    applog.audit("admin.ai_route.updated", "هدف مسیر حذف شد",
                 actor=actor or "admin", target=f"target#{target_id}", outcome="ok")


def set_target_enabled(target_id: int, enabled: bool, actor: str = "") -> None:
    from app.db.connection import get_db_connection
    from app.services import applog
    conn = get_db_connection()
    try:
        conn.execute("UPDATE ai_route_targets SET enabled = ?,"
                     " updated_at = datetime('now') WHERE id = ?",
                     (bool(enabled), target_id))
        conn.commit()
    finally:
        conn.close()
    applog.audit("admin.ai_route.updated", "وضعیت هدف مسیر تغییر کرد",
                 actor=actor or "admin", target=f"target#{target_id}", outcome="ok")


def reorder_targets(task: str, ordered_ids: list, actor: str = "") -> None:
    """Atomic reorder: two-phase offset inside ONE transaction.

    Phase 1 moves every target to priority+1000 (outside the CHECK/UNIQUE
    range collision space), phase 2 writes the final 1..n order. Both phases
    commit together — an observer on another connection sees either the old
    order or the new one, never duplicates or gaps."""
    from app.db.connection import get_db_connection
    from app.services import applog
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT id FROM ai_route_targets WHERE task = ?",
                            (task,)).fetchall()
        known = {r["id"] for r in rows}
        if set(ordered_ids) != known or len(ordered_ids) != len(known):
            raise ai_errors.AIError(code="invalid_request",
                                    provider_detail="reorder must list every target exactly once")
        conn.execute("BEGIN") if False else None
        for row in rows:
            conn.execute("UPDATE ai_route_targets SET priority = priority + 1000"
                         " WHERE id = ?", (row["id"],))
        for pos, tid in enumerate(ordered_ids, start=1):
            conn.execute("UPDATE ai_route_targets SET priority = ?,"
                         " updated_at = datetime('now') WHERE id = ?", (pos, tid))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        finally:
            conn.close()
        raise
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    applog.audit("admin.ai_route.updated", f"ترتیب مسیر {task} تغییر کرد",
                 actor=actor or "admin", target=task, outcome="ok")


# ── Pricing ─────────────────────────────────────────────────────────────

def lookup_pricing(provider_type: str, model_id: str) -> dict | None:
    """The price row in effect for (provider_type, model_id) right now.

    The cutoff is generated in Python in the SAME timestamp format the
    writers use (ISO with explicit offset) — on SQLite the comparison is a
    string compare, so mixing formats with datetime('now') would silently
    reject same-day rows; on PostgreSQL the parameter casts cleanly.
    """
    from datetime import datetime, timezone
    from app.db.connection import get_db_connection
    cutoff = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ai_model_pricing"
            " WHERE provider_type = ? AND model_id = ? AND effective_from <= ?"
            " ORDER BY effective_from DESC LIMIT 1",
            (provider_type, model_id, cutoff)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_pricing(provider_type: str, model_id: str, currency: str,
                   input_per_million: float, cached_input_per_million,
                   output_per_million: float, source: str) -> None:
    """Insert a NEW effective-dated row. History is never overwritten — a
    price change is a new row with a later effective_from."""
    from datetime import datetime, timezone
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO ai_model_pricing (provider_type, model_id, currency,"
            " input_per_million, cached_input_per_million, output_per_million,"
            " effective_from, source) VALUES (?,?,?,?,?,?,?,?)",
            (provider_type, model_id, currency, float(input_per_million),
             None if cached_input_per_million is None else float(cached_input_per_million),
             float(output_per_million),
             datetime.now(timezone.utc).isoformat(timespec="seconds"), source))
        conn.commit()
    finally:
        conn.close()


# ── Usage events ────────────────────────────────────────────────────────

def record_usage(row: dict) -> None:
    from app.db.connection import get_db_connection
    from app.config import DB_BACKEND
    meta = row.get("metadata")
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO ai_usage_events (task, status, provider_type,"
            " provider_instance_id, model, attempts, failovers, tokens_in,"
            " tokens_out, tokens_cached, tokens_total, latency_ms, cost,"
            " currency, pricing_effective_from, error_code, request_id,"
            " correlation_id, metadata)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row.get("task", ""), row.get("status", "failed"),
             row.get("provider_type", ""), row.get("provider_instance_id", ""),
             row.get("model", ""), row.get("attempts", 1), row.get("failovers", 0),
             row.get("tokens_in"), row.get("tokens_out"), row.get("tokens_cached"),
             row.get("tokens_total"), row.get("latency_ms"), row.get("cost"),
             row.get("currency", ""), row.get("pricing_effective_from"),
             row.get("error_code", ""), row.get("request_id", ""),
             row.get("correlation_id", ""),
             (None if meta is None else json.dumps(meta, ensure_ascii=False))
             if DB_BACKEND == "postgres" else
             ("" if meta is None else json.dumps(meta, ensure_ascii=False))))
        conn.commit()
    except Exception as e:  # noqa: BLE001 — accounting must not break a reply
        logger.error("[ai] usage row dropped: %s", type(e).__name__)
    finally:
        conn.close()
