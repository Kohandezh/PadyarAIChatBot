"""Provider health: derived, cheap, and honest.

Health is COMPUTED on read — never a stored status that can drift:

    Disabled  → provider row disabled (or unconfigured: no key)
    Down      → circuit OPEN
    Degraded  → circuit HALF_OPEN, or a clear recent-failure signal
                (100% error rate on today's traffic), or last error was auth
    Healthy   → circuit CLOSED and a success in the last 24 h (or no traffic)
    Unknown   → enabled + configured, but no traffic and no signal yet

Signals come from real traffic (ai_usage_events) and the circuit table —
NEVER from a paid inference probe. `test_connection` runs the provider's
cheapest documented operation (usually the model-list API) only when an
operator clicks Test.
"""
from . import circuit, store
from .adapters import adapter_for


def _usage_window(provider_instance_id: str, hours: int = 24) -> dict:
    from app.db.connection import get_db_connection
    # The interval is inlined (int-clamped) so the pg adapter's translator
    # rewrites datetime('now', '-N hours') → now() - interval on PostgreSQL;
    # a bound parameter would stay untranslated and fail there.
    hours = max(1, int(hours))
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok"
            " FROM ai_usage_events"
            f" WHERE provider_instance_id = ? AND created_at >= datetime('now', '-{hours} hours')",
            (provider_instance_id,)).fetchone()
        total = row["total"] or 0
        return {"total": total, "ok": row["ok"] or 0,
                "error_rate": (total - (row["ok"] or 0)) / total if total else None}
    finally:
        conn.close()


def instance_health(instance: dict) -> dict:
    pid = instance["id"]
    circuits = {c["provider_instance_id"]: c for c in circuit.snapshot()}
    c = circuits.get(pid, {})
    window = _usage_window(pid)

    state = c.get("state", "closed")
    if not instance["enabled"]:
        health = "disabled"
    elif not instance["has_secret"]:
        health = "disabled"          # enabled but unconfigured cannot serve
    elif state == "open":
        health = "down"
    elif state == "half_open":
        health = "degraded"
    elif c.get("last_failure_code") == "authentication_failed":
        health = "degraded"
    elif window["total"] and window["error_rate"] == 1.0:
        health = "degraded"          # traffic exists, all of it failed
    elif window["ok"]:
        health = "healthy"
    else:
        health = "unknown"

    return {
        "provider_instance_id": pid,
        "health": health,
        "circuit_state": state,
        "circuit_failure_count": c.get("failure_count", 0),
        "cooldown_until": c.get("cooldown_until"),
        "last_success_at": c.get("last_success_at"),
        "last_failure_at": c.get("last_failure_at"),
        "last_failure_code": c.get("last_failure_code", ""),
        "requests_24h": window["total"],
        "error_rate_24h": window["error_rate"],
    }


def provider_rows_for_admin() -> list:
    """The AI → Providers table: instance + health + per-task models."""
    instances = store.list_instances()
    healths = {h["provider_instance_id"]: h for h in
               (instance_health(i) for i in instances)}
    targets = store.list_routes()["targets"]
    models_by_instance: dict = {}
    for m in store.list_models():
        models_by_instance.setdefault(m["provider_instance_id"], []).append(m)

    rows = []
    for inst in instances:
        h = healths[inst["id"]]
        inst_targets = [t for t in targets if t["provider_instance_id"] == inst["id"]]
        rows.append({
            **inst,
            "health": h["health"],
            "circuit_state": h["circuit_state"],
            "last_success_at": h["last_success_at"],
            "requests_24h": h["requests_24h"],
            "error_rate_24h": h["error_rate_24h"],
            "chat_models": [t["model_id"] for t in inst_targets if t["task"] == "chat"],
            "classify_models": [t["model_id"] for t in inst_targets if t["task"] == "classify"],
            "model_count": len(models_by_instance.get(inst["id"], [])),
        })
    return rows


async def test_instance(instance_id: str, actor: str = "") -> dict:
    """Run the provider's cheapest documented connectivity check.

    Never enables the provider; never sends real traffic. Result is audited.
    Provider text reaches the admin ONLY after centralized redaction — the
    detail is scrubbed HERE as well, so no future adapter that forgets to
    redact can leak a key echoed back inside a provider error body.
    """
    from app.services import applog
    inst = store.get_instance(instance_id)
    if not inst:
        return {"ok": False, "status": "invalid_config", "detail": "not found",
                "latency_ms": 0}
    rt = store.runtime_for(instance_id)
    adapter = adapter_for(inst["provider_type"])
    if rt is None:
        return {"ok": False, "status": "invalid_config", "detail": "not found",
                "latency_ms": 0}
    result = await adapter.test_connection(rt)
    result["detail"] = applog.scrub_text(result.get("detail", ""))[:300]
    applog.audit("admin.ai_provider.tested",
                 "آزمون اتصال سرویس‌دهنده",
                 actor=actor or "admin", target=instance_id,
                 outcome="ok" if result["ok"] else "failed",
                 metadata={"status": result["status"],
                           "detail": result["detail"][:300],
                           "latency_ms": result["latency_ms"]})
    return result
