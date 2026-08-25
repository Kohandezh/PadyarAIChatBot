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

This module is the legacy↔control-plane BRIDGE in both directions:
  * `run_import` — the one-shot, boot-time migration above;
  * `ensure_panel_provider` — the live bridge a Settings → AI save calls, so
    a key saved in the panel reaches the routed instance's secret and any
    missing chat/classify route is built immediately (the boot import is a
    one-shot marker and never runs again, so without this the panel save
    only wrote legacy rows and Tier 2 stayed dead).

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


def _create_default_instance(base: str, key: str, chat_model: str,
                             classify_model: str, enabled: bool,
                             actor: str) -> tuple:
    """Create the default openai_compatible instance + its two routes.

    Extracted verbatim from run_import so the boot-time migration and the
    panel-save bridge (ensure_panel_provider, below) build the EXACT same
    object — one builder, no drift between the two entry points. Trust
    class is derived from the endpoint exactly as the import always did.
    On partial failure the half-built state is rolled back (same reasoning
    as run_import's) and the exception is re-raised: the boot import
    swallows it, the panel surfaces it. Returns (instance_id, target_ids)
    so a caller whose OWN follow-up writes fail can roll the fully-created
    instance back too — the creation commits, so surviving it is now the
    caller's rollback window."""
    from . import store

    trust = "public" if _endpoint_public(base) else "internal"
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
        return instance_id, targets
    except Exception as e:  # noqa: BLE001 — callers decide swallow vs surface
        # Each store call commits its own transaction, so a failure partway
        # through leaves a HALF-built control plane: an instance with no
        # (or one) route target. That state is worse than no creation —
        # every AI call would end in all_routes_failed, and on the NEXT boot
        # the "an operator already built this by hand" guard in run_import
        # would see the orphan instance, set the marker, and freeze the
        # breakage permanently. Roll the partial state back so a retry
        # starts clean.
        logger.error("[ai] default instance creation failed: %s — rolling back",
                     type(e).__name__)
        _rollback(instance_id, targets, actor)
        raise


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

    chat_model = model_for("chat")
    classify_model = model_for("classify")
    enabled = (get_setting("openai_enabled", "true") or "true").lower() == "true"

    instance_id = ""
    created_targets: list = []
    try:
        instance_id, created_targets = _create_default_instance(
            base, key, chat_model, classify_model, enabled, actor)
        # Record WHICH instance is the panel-owned default BEFORE the one-shot
        # marker: if the marker write dies mid-pair, the next boot sees the
        # instance via the list_instances guard above and marks itself done —
        # with the default pointer already in place, ensure_panel_provider
        # keeps rotating that install's key instead of mistaking the imported
        # instance for a hand-built one and refusing to touch it.
        set_setting("ai_default_instance_id", instance_id)
        set_setting("ai_control_plane_migrated", "1")
    except Exception as e:  # noqa: BLE001 — boot must never die here
        # The creation itself already rolled its own partial state back; the
        # window left is the marker writes AFTER a fully-committed creation.
        # Leaving that orphan alive is not neutral: the next boot's
        # "an operator already built this by hand" guard would see it, set
        # the one-shot marker, and freeze a possibly-unmarked dead control
        # plane permanently. Roll the created instance back here too (the
        # pre-extraction run_import covered this window in one try block)
        # so the next boot retries instead of freezing.
        logger.error("[ai] legacy import failed: %s — rolling back",
                     type(e).__name__)
        _rollback(instance_id, created_targets, actor)
        return None
    logger.info("[ai] legacy configuration imported as instance %s "
                "(chat=%s classify=%s)", instance_id, chat_model, classify_model)
    return instance_id


def ensure_panel_provider(base: str, key: str, actor: str = "admin") -> None:
    """Bridge a Settings → AI save into the control plane. Idempotent.

    The panel save used to write ONLY the legacy rows, while chat/classify
    route exclusively off control-plane tables — so a key saved in the panel
    left Tier 2 dead (every ambiguous query 503) while health and the panel
    both reported everything fine. One save through this bridge leaves a
    routed, working install — no restart, and no operator knowledge of
    instances or route targets (AGENTS.md simplicity principle).

    Precedence (owner rulings 2026-08-25): the panel only fills MISSING
    pieces. Existing targets are never modified, removed or reordered; a new
    default instance is created only when the install has ZERO instances; on
    a hand-built control plane with no default marker nothing is created or
    routed at all — guessing operator intent is exactly how manual setups
    break silently. Health and the panel warn about any unrouted task
    instead, in plain Persian.

    Raises on failure (after rolling back whatever it created mid-flight)
    so the panel surfaces the save as an error instead of masking it."""
    from app.db.queries import get_setting, set_setting
    from app.services.openai import model_for
    from app.services.ai.errors import AIError
    from . import store

    base = (base or "").strip()
    key = (key or "").strip()
    if not base:
        # The caller pins the base to the legacy row / env default, so an
        # empty base here means the install has a key but no endpoint
        # anywhere. An empty string must NEVER reach create_instance /
        # update_instance: the adapter's config validation would reject it
        # or store a broken base_url on an otherwise working instance.
        raise AIError(code="invalid_request",
                      provider_detail="no AI endpoint configured")

    # A stale marker (default instance deleted) counts as absent — the
    # recreate path below rewrites the marker to the new id.
    iid = (get_setting("ai_default_instance_id", "") or "").strip()
    default_instance = store.get_instance(iid) if iid else None

    created_id = ""            # only what THIS call built may be rolled back
    created_targets: list = []
    new_targets: list = []
    try:
        if default_instance is None:
            if store.list_instances():
                # An operator hand-built the control plane: never create a
                # competing panel-owned instance, never guess at their
                # routes. The legacy settings rows (written by the router
                # before this call) still keep STT in sync.
                return
            enabled = (get_setting("openai_enabled", "true")
                       or "true").lower() == "true"
            created_id, created_targets = _create_default_instance(
                base, key, model_for("chat"), model_for("classify"),
                enabled, actor)
            set_setting("ai_default_instance_id", created_id)
            iid = created_id
        elif key:
            # Rotation / base change on the existing default — covers both
            # panel-created and boot-imported installs (the imported case
            # used to split-brain: panel updated only the legacy row, chat
            # kept answering with the old secret while STT failed over to
            # the new one). `enabled` is deliberately never touched here:
            # the kill switch (`openai_enabled`) and the AI page's toggle
            # own that flag. update_instance encrypts the secret
            # (secure_store.protect) and invalidates the runtime cache, so
            # the new key is live WITHOUT a restart.
            store.update_instance(iid, config={"base_url": base},
                                  secret=key, actor=actor)
        else:
            store.update_instance(iid, config={"base_url": base}, actor=actor)

        # Fill MISSING routes only: a task with zero targets at all gets the
        # default instance + the legacy model id. Any existing target (any
        # instance) means the operator owns that task — never added to,
        # reordered, disabled or removed.
        for task in ("chat", "classify"):
            if store.ordered_targets(task):
                continue
            store.add_manual_model(iid, model_for(task))
            new_targets.append(store.add_target(task, iid, model_for(task),
                                                actor=actor))
    except Exception:
        # Both target lists are needed: delete_instance refuses while any
        # route still references the instance, so rolling back only the
        # fill targets would leave a creation-path orphan behind whenever
        # the failure happens AFTER _create_default_instance returned.
        _rollback(created_id, created_targets + new_targets, actor)
        raise


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
