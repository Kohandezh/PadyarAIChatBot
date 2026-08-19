"""Idempotent import of the legacy single-provider configuration.

The pre-control-plane runtime had exactly one provider, expressed as:

    ai_api_base     (settings row, env OPENAI_API_BASE fallback)
    ai_api_key      (settings row, env OPENAI_API_KEY fallback)
    ai_model_chat   (default gpt-4.1)
    ai_model_classify (default gpt-5-nano)
    openai_enabled  (default true)

This import turns that working configuration into the FIRST provider
instance (type openai_compatible — that is what a GapGPT-style proxy is) and
builds single-target CHAT and CLASSIFICATION routes from it.

Safety rules (phase spec: "existing configuration migration"):
  * Idempotent — the marker setting ai_control_plane_migrated plus the
    "no instances exist" guard means this never duplicates or overwrites.
  * NON-DESTRUCTIVE — the legacy settings rows are left exactly as they
    are. They keep serving the STT path (out of scope this phase) and the
    `openai_enabled` key continues to act as the kill switch.
  * Trust class is DERIVED from the endpoint: https + public host = public;
    a private/local host (on-prem gateway) = internal, so an on-prem install
    imports working rather than rejected.
  * The configured model ids become manual catalog rows for the instance —
    they are the ids the customer's gateway actually serves TODAY, which
    the research says may differ from any vendor's current lineup. Nobody
    gets to "upgrade" them silently.
"""
from app.config import logger


def _endpoint_public(base: str) -> bool:
    from . import endpoint_policy
    try:
        endpoint_policy.validate(base, endpoint_policy.PUBLIC)
        return True
    except endpoint_policy.EndpointRejected:
        return False


def run_import(actor: str = "system") -> str | None:
    """Create the migrated instance + routes if needed. Returns the new
    instance id, or None when there is nothing to do. Never raises."""
    from app.db.queries import get_setting, set_setting
    from . import store

    if get_setting("ai_control_plane_migrated", "") == "1":
        return None
    if store.list_instances():
        # An operator already built the control plane by hand — mark done.
        set_setting("ai_control_plane_migrated", "1")
        return None

    from app.services.openai import provider_config, model_for
    base, key = provider_config()
    if not base or not key:
        # Nothing workable to import (fresh install) — mark done so we do
        # not re-evaluate env fallbacks on every boot.
        set_setting("ai_control_plane_migrated", "1")
        return None

    trust = "public" if _endpoint_public(base) else "internal"
    chat_model = model_for("chat")
    classify_model = model_for("classify")
    enabled = (get_setting("openai_enabled", "true") or "true").lower() == "true"

    instance_id = ""
    targets: list = []
    try:
        instance_id = store.create_instance(
            provider_type="openai_compatible",
            display_name="سرویس فعلی (مهاجرت‌یافته)",
            config={"base_url": base},
            secret=key,
            enabled=enabled,
            trust_class=trust,
            notes="به‌صورت خودکار از تنظیمات قبلی Settings → AI ساخته شد.",
            actor=actor)
        # The configured ids are what the customer's gateway serves today —
        # manual rows, never auto-replaced by any catalog refresh.
        store.add_manual_model(instance_id, chat_model, display_name=chat_model)
        store.add_manual_model(instance_id, classify_model, display_name=classify_model)
        targets.append(store.add_target("chat", instance_id, chat_model, actor=actor))
        targets.append(store.add_target("classify", instance_id, classify_model,
                                        actor=actor))
        set_setting("ai_control_plane_migrated", "1")
        logger.info("[ai] legacy configuration imported as instance %s "
                    "(chat=%s classify=%s trust=%s)", instance_id, chat_model,
                    classify_model, trust)
        return instance_id
    except Exception as e:  # noqa: BLE001 — boot must never die here
        # Each store call commits its own transaction, so a failure partway
        # through leaves a HALF-migrated control plane: an instance with no
        # (or one) route target. That state is worse than no migration —
        # every AI call would end in all_routes_failed, and on the NEXT boot
        # the "an operator already built this by hand" guard above would see
        # the orphan instance, set the marker, and freeze the breakage
        # permanently. Roll the partial state back so the next boot retries.
        logger.error("[ai] legacy import failed: %s — rolling back",
                     type(e).__name__)
        _rollback(instance_id, targets, actor)
        return None


def _rollback(instance_id: str, target_ids: list, actor: str) -> None:
    """Undo a partial import. Targets first — delete_instance deliberately
    refuses while any route still references the instance."""
    from . import store
    for tid in reversed(target_ids):
        try:
            store.remove_target(tid, actor=actor)
        except Exception as e:  # noqa: BLE001
            logger.error("[ai] rollback: target %s not removed (%s)",
                         tid, type(e).__name__)
    if not instance_id:
        return
    try:
        store.delete_instance(instance_id, actor=actor)
    except Exception as e:  # noqa: BLE001
        logger.error("[ai] rollback: instance %s not removed (%s) — the next "
                     "boot will treat it as an operator-built control plane",
                     instance_id, type(e).__name__)
