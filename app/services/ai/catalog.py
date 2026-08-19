"""Model catalog refresh via official discovery, where it exists.

`refresh_instance_models` calls the adapter's list_models() (each provider's
documented endpoint and parsing — Mistral/xAI's rich metadata survives
intact) and merges into the catalog via store.apply_discovery.

Providers without a discovery endpoint (Z.AI, Qwen — verified absent from
their specs) return a clear "not supported" result instead of pretending:
the admin path there is bootstrap + manual model entry.

Every refresh is logged (started/completed/failed) with counts.
"""
from . import store
from .adapters import adapter_for


async def refresh_instance_models(instance_id: str, actor: str = "") -> dict:
    from app.services import applog
    inst = store.get_instance(instance_id)
    if not inst:
        return {"ok": False, "status": "not_found", "detail": "instance not found"}
    adapter = adapter_for(inst["provider_type"])
    if not adapter.metadata().supports_discovery:
        applog.audit("admin.ai_model.refresh.failed",
                     "این سرویس‌دهنده API فهرست مدل ندارد",
                     actor=actor or "admin", target=instance_id, outcome="failed",
                     metadata={"provider_type": inst["provider_type"]})
        return {"ok": False, "status": "unsupported",
                "detail": "این سرویس‌دهنده اندپوینت فهرست مدل ندارد؛ از مدل دستی استفاده کنید."}

    applog.audit("admin.ai_model.refresh.started", "به‌روزرسانی فهرست مدل‌ها آغاز شد",
                 actor=actor or "admin", target=instance_id, outcome="ok")
    rt = store.runtime_for(instance_id)
    try:
        discovered = await adapter.list_models(rt)
    except Exception as e:  # noqa: BLE001 — report, never crash the admin
        detail = getattr(e, "provider_detail", None) or type(e).__name__
        if hasattr(e, "redacted_detail"):
            detail = e.redacted_detail()
        applog.audit("admin.ai_model.refresh.failed", "به‌روزرسانی فهرست مدل‌ها ناموفق بود",
                     actor=actor or "admin", target=instance_id, outcome="failed",
                     metadata={"error": str(detail)[:300]})
        return {"ok": False, "status": "provider_error", "detail": str(detail)[:300]}

    # An EMPTY 200 is not evidence that the provider retired everything — it
    # is what a key without model-list permission, a wrong workspace, or a
    # gateway stub returns. Applying it would downgrade the whole discovered
    # catalog to `unavailable` in one click. Nothing is ever deleted, so this
    # is recoverable, but it is still wrong: refuse and say so.
    if not discovered:
        applog.audit("admin.ai_model.refresh.failed",
                     "فهرست مدل خالی بازگشت؛ تغییری اعمال نشد",
                     actor=actor or "admin", target=instance_id, outcome="failed",
                     metadata={"provider_type": inst["provider_type"],
                               "reason": "empty_model_list"})
        return {"ok": False, "status": "empty",
                "detail": "سرویس‌دهنده فهرست خالی برگرداند؛ کاتالوگ دست‌نخورده ماند."}

    counts = store.apply_discovery(instance_id, discovered)
    applog.audit("admin.ai_model.refresh.completed",
                 "فهرست مدل‌ها به‌روزرسانی شد",
                 actor=actor or "admin", target=instance_id, outcome="ok",
                 metadata=counts)
    return {"ok": True, **counts}
