"""Admin API for the AI provider control plane.

Every endpoint requires an admin session (verify_admin) and every mutation is
covered by the CSRF middleware (app/main.py:csrf_protection protects ALL
POST/PUT/PATCH/DELETE requests carrying an admin session — there is no way
to add an unprotected mutation by accident) and audited via applog.audit
inside the store/service functions.

Secrets: no endpoint ever returns `secret_enc` or the revealed key. Updates
take a new secret value; the response confirms only has_secret.
Provider-originated text (test-connection details, error text) reaches the
client already redacted by AIError.redacted_detail / scrub_text.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.security import verify_admin
from app.services import applog
from app.services.ai import catalog, circuit, errors as ai_errors, health, pricing, store
from app.services.ai.adapters import provider_types

router = APIRouter(dependencies=[Depends(verify_admin)])


def _ok(**kw):
    return {"ok": True, **kw}


def _fail(status: int, detail: str):
    raise HTTPException(status_code=status, detail=detail)


# ── Providers ───────────────────────────────────────────────────────────

@router.get("/admin/api/ai/providers")
async def list_providers():
    return {"providers": health.provider_rows_for_admin()}


@router.get("/admin/api/ai/provider-types")
async def list_provider_types():
    return {"types": provider_types()}


@router.get("/admin/api/ai/providers/{instance_id}")
async def get_provider(instance_id: str):
    inst = store.get_instance(instance_id)
    if not inst:
        _fail(404, "not found")
    models = [m for m in store.list_models(instance_id)]
    return {"provider": {**inst, **({"health": health.instance_health(inst)["health"],
                                     "circuit_state": health.instance_health(inst)["circuit_state"]})},
            "models": models,
            "pricing": {m["model_id"]: pricing.pricing_for(inst, m["model_id"])
                        for m in models}}


@router.post("/admin/api/ai/providers")
async def create_provider(request: Request):
    body = await _json(request)
    try:
        instance_id = store.create_instance(
            provider_type=body.get("provider_type", ""),
            display_name=(body.get("display_name") or "").strip(),
            config=body.get("config") or {},
            secret=body.get("api_key") or "",
            enabled=False,                       # saved disabled — test first
            trust_class=body.get("trust_class") or "public",
            notes=body.get("notes") or "",
            actor=_actor(request))
    except ai_errors.AIError as e:
        _fail(400, e.redacted_detail())
    return _ok(instance_id=instance_id)


@router.post("/admin/api/ai/providers/{instance_id}/update")
async def update_provider(instance_id: str, request: Request):
    body = await _json(request)
    try:
        store.update_instance(
            instance_id,
            display_name=body.get("display_name"),
            config=body.get("config"),
            secret=body.get("api_key") if body.get("api_key") is not None else None,
            trust_class=body.get("trust_class"),
            notes=body.get("notes"),
            actor=_actor(request))
    except ai_errors.AIError as e:
        _fail(400, e.redacted_detail())
    return _ok()


@router.post("/admin/api/ai/providers/{instance_id}/set-enabled")
async def set_provider_enabled(instance_id: str, request: Request):
    body = await _json(request)
    try:
        store.set_enabled(instance_id, bool(body.get("enabled")), actor=_actor(request))
    except ai_errors.AIError as e:
        _fail(400, e.redacted_detail())
    return _ok()


@router.post("/admin/api/ai/providers/{instance_id}/test")
async def test_provider(instance_id: str, request: Request):
    result = await health.test_instance(instance_id, actor=_actor(request))
    return result


@router.post("/admin/api/ai/providers/{instance_id}/reset-circuit")
async def reset_circuit(instance_id: str, request: Request):
    circuit.reset(instance_id)
    applog.audit("admin.ai_circuit.reset", "مدار سرویس‌دهنده بازنشانی شد",
                 actor=_actor(request), target=instance_id, outcome="ok")
    return _ok()


@router.post("/admin/api/ai/providers/{instance_id}/delete")
async def delete_provider(instance_id: str, request: Request):
    body = await _json(request)
    if (body.get("confirm") or "") != instance_id:
        _fail(400, "confirmation must repeat the instance id")
    try:
        store.delete_instance(instance_id, actor=_actor(request))
    except ai_errors.AIError as e:
        _fail(409, e.redacted_detail())
    return _ok()


# ── Models ──────────────────────────────────────────────────────────────

@router.get("/admin/api/ai/models")
async def list_models(instance_id: str = ""):
    return {"models": store.list_models(instance_id or "")}


@router.post("/admin/api/ai/models/manual")
async def add_manual_model(request: Request):
    body = await _json(request)
    try:
        store.add_manual_model(
            body.get("instance_id", ""), body.get("model_id", ""),
            display_name=body.get("display_name") or "",
            context_window=_int_or_none(body.get("context_window")),
            max_output_tokens=_int_or_none(body.get("max_output_tokens")))
    except ai_errors.AIError as e:
        _fail(400, e.redacted_detail())
    applog.audit("admin.ai_model.manual_added", "مدل دستی افزوده شد",
                 actor=_actor(request), target=f"{body.get('instance_id')}/{body.get('model_id')}",
                 outcome="ok")
    return _ok()


@router.post("/admin/api/ai/models/refresh")
async def refresh_models(request: Request):
    body = await _json(request)
    result = await catalog.refresh_instance_models(body.get("instance_id", ""),
                                                   actor=_actor(request))
    return result


@router.post("/admin/api/ai/models/delete")
async def delete_model(request: Request):
    body = await _json(request)
    from app.db.connection import get_db_connection
    model_row_id = _int_or_none(body.get("id"))
    if not model_row_id:
        _fail(400, "model id required")
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM ai_provider_models WHERE id = ?", (model_row_id,))
        conn.commit()
    finally:
        conn.close()
    applog.audit("admin.ai_model.deleted", "مدل حذف شد",
                 actor=_actor(request), target=f"model#{model_row_id}", outcome="ok")
    return _ok()


# ── Routing ─────────────────────────────────────────────────────────────

@router.get("/admin/api/ai/routes")
async def get_routes():
    data = store.list_routes()
    instances = [{"id": i["id"], "display_name": i["display_name"],
                  "provider_type": i["provider_type"], "enabled": i["enabled"],
                  "has_secret": i["has_secret"]}
                 for i in store.list_instances()]
    models = store.list_models()
    return {**data, "instances": instances, "models": models}


@router.post("/admin/api/ai/routes/target")
async def add_route_target(request: Request):
    body = await _json(request)
    try:
        target_id = store.add_target(
            body.get("task", ""), body.get("instance_id", ""),
            (body.get("model_id") or "").strip(),
            max_attempts=_int_or_none(body.get("max_attempts")),
            timeout_s=_float_or_none(body.get("timeout_s")),
            actor=_actor(request))
    except ai_errors.AIError as e:
        _fail(400, e.redacted_detail())
    return _ok(target_id=target_id)


@router.post("/admin/api/ai/routes/target/{target_id}/remove")
async def remove_route_target(target_id: int, request: Request):
    try:
        store.remove_target(target_id, actor=_actor(request))
    except ai_errors.AIError as e:
        _fail(400, e.redacted_detail())
    return _ok()


@router.post("/admin/api/ai/routes/target/{target_id}/set-enabled")
async def set_route_target_enabled(target_id: int, request: Request):
    body = await _json(request)
    store.set_target_enabled(target_id, bool(body.get("enabled")), actor=_actor(request))
    return _ok()


@router.post("/admin/api/ai/routes/reorder")
async def reorder_route(request: Request):
    body = await _json(request)
    try:
        store.reorder_targets(body.get("task", ""),
                              [int(x) for x in body.get("ordered_ids") or []],
                              actor=_actor(request))
    except (ai_errors.AIError, TypeError, ValueError) as e:
        _fail(400, str(getattr(e, "provider_detail", e))[:300])
    return _ok()


# ── Pricing ─────────────────────────────────────────────────────────────

@router.post("/admin/api/ai/pricing")
async def upsert_pricing(request: Request):
    body = await _json(request)
    try:
        store.upsert_pricing(
            body.get("provider_type", ""), body.get("model_id", ""),
            body.get("currency") or "USD",
            float(body.get("input_per_million")),
            _float_or_none(body.get("cached_input_per_million")),
            float(body.get("output_per_million")),
            source=f"admin: {_actor(request) or 'admin'}")
    except (TypeError, ValueError) as e:
        _fail(400, f"bad number: {e}")
    applog.audit("admin.ai_pricing.updated", "قیمت مدل به‌روزرسانی شد",
                 actor=_actor(request),
                 target=f"{body.get('provider_type')}/{body.get('model_id')}",
                 outcome="ok")
    return _ok()


# ── Usage & costs (server-side aggregation) ─────────────────────────────

@router.get("/admin/api/ai/usage")
async def usage_dashboard(days: int = 7, group_by: str = "provider_instance"):
    return usage_aggregation(days, group_by)


def usage_aggregation(days: int = 7, group_by: str = "provider_instance") -> dict:
    from app.db.connection import get_db_connection
    days = max(0, min(int(days), 365))
    column = {"provider_instance": "provider_instance_id",
              "provider_type": "provider_type",
              "model": "model", "task": "task"}.get(group_by, "provider_instance_id")
    # Interval inlined (int-clamped): the pg adapter translates
    # datetime('now', '-N days') → now() - interval; a bound '?' would not
    # survive the translation and PostgreSQL would reject the query.
    window = f"datetime('now', '-{days} days')"
    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"SELECT {column} AS grp,"
            " COUNT(*) AS requests,"
            " SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successful,"
            " SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,"
            " SUM(failovers) AS failovers,"
            " SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out,"
            " SUM(tokens_cached) AS tokens_cached, SUM(tokens_total) AS tokens_total,"
            " AVG(latency_ms) AS avg_latency,"
            " SUM(cost) AS cost,"
            " MIN(currency) AS currency"
            " FROM ai_usage_events"
            f" WHERE created_at >= {window}"
            f" GROUP BY {column} ORDER BY requests DESC").fetchall()
        p95 = conn.execute(
            "SELECT latency_ms FROM ai_usage_events"
            f" WHERE created_at >= {window} AND latency_ms IS NOT NULL"
            " ORDER BY latency_ms").fetchall()
    finally:
        conn.close()
    p95_ms = None
    if p95:
        p95_ms = p95[min(len(p95) - 1, int(len(p95) * 0.95))]["latency_ms"]
    groups = []
    for r in rows:
        groups.append({**dict(r), "avg_latency": round(r["avg_latency"] or 0)})
    return {"days": days, "group_by": group_by, "groups": groups, "p95_latency_ms": p95_ms}


# ── Dashboard summary ───────────────────────────────────────────────────

@router.get("/admin/api/ai/summary")
async def ai_summary():
    from app.db.connection import get_db_connection
    providers = health.provider_rows_for_admin()
    counts = {"active": 0, "healthy": 0, "degraded": 0, "down": 0}
    for p in providers:
        if p["enabled"] and p["has_secret"]:
            counts["active"] += 1
        h = p["health"]
        if h in counts and h != "active":
            counts[h] += 1
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS requests,"
            " SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,"
            " SUM(tokens_total) AS tokens, SUM(cost) AS cost,"
            " SUM(failovers) AS failovers, AVG(latency_ms) AS avg_latency"
            " FROM ai_usage_events WHERE created_at >= datetime('now', '-1 day')"
        ).fetchone()
    finally:
        conn.close()
    total = row["requests"] or 0
    ok = row["ok"] or 0
    return {
        "providers": counts,
        "requests_today": total,
        "error_rate_today": round((total - ok) / total, 4) if total else None,
        "failovers_today": row["failovers"] or 0,
        "avg_latency_ms": round(row["avg_latency"] or 0) if row["avg_latency"] else None,
        "tokens_today": row["tokens"] or 0,
        "cost_today": round(row["cost"] or 0, 6) if row["cost"] else 0.0,
    }


# ── RAG debugger ────────────────────────────────────────────────────────

@router.get("/admin/api/ai/debug")
async def rag_debug(limit: int = 40):
    """Recent wrapper calls with their routing picture — alongside the
    retrieval diagnostics that already live in the log explorer. Read-only:
    the public RAG behaviour is untouched by this view."""
    from app.db.connection import get_db_connection
    limit = max(1, min(limit, 200))
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_usage_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return {"events": [dict(r) for r in reversed(rows)]}


# ── helpers ─────────────────────────────────────────────────────────────

async def _json(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:  # noqa: BLE001 — empty/invalid body treated as empty
        return {}


def _actor(request: Request) -> str:
    username = getattr(request.state, "admin_username", "")
    return username or "admin"


def _int_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
