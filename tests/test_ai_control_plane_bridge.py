"""The Settings → AI → control-plane bridge (ensure_panel_provider).

The control-plane migration re-pointed chat/classify at `ai_route_targets`
but left POST /admin/api/ai-connection a legacy-only writer, and made the
only bridge (legacy_import.run_import) a one-shot boot-time migration. On any
install that first boots without a key, saving a key in the panel left Tier 2
dead (ALL_ROUTES_FAILED → 503 for every ambiguous query) while health said
"healthy" and the panel said "has_key: true".

These tests pin the fix: one panel save leaves a routed, working install
without a restart; rotation reaches the routed instance's secret; hand-built
control planes are never silently altered; health and the panel tell the
truth about routing; and the health probe stays config-only (zero network
calls, zero tokens).

Conventions: tmp SQLite DB + real TestClient boot + admin session + CSRF
(see test_ai_admin_ui.py), engine network stubbed via the OkCompatAdapter
pattern (see test_ai_legacy_import.py).
"""
import asyncio
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient

from app.services.ai import store


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A booted app on a fresh install: no key anywhere (env fallbacks
    suppressed so the boot import marks itself done and imports nothing —
    exactly the fresh-install state the bridge exists for)."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "bridge.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr("app.services.openai.OPENAI_API_BASE", "")
    monkeypatch.setattr("app.services.openai.OPENAI_API_KEY", "")
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        store.ensure_ai_tables()
        store.seed_bootstrap_pricing()
        store._invalidate_runtime()
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('bridgeadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "bridgeadmin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        yield c
    store._invalidate_runtime()


def _csrf(client):
    return client.get("/admin/csrf").json()["csrf_token"]


def _post(client, body=None):
    return client.post("/admin/api/ai-connection", json=body or {},
                       headers={"X-CSRF-Token": _csrf(client)})


def _save(client, base="https://api.gapgpt.app/v1", key="sk-panel-111222333",
          model_stt="whisper-1"):
    return _post(client, {
        "api_base": base, "api_key": key, "model_stt": model_stt,
        "feature_tts": True, "feature_stt": True,
        "search_backend": "tfidf", "default_lang": "fa",
    })


def _eligible(task):
    return [t for t in store.ordered_targets(task)
            if t["target_enabled"] and t["provider_enabled"] and t["has_secret"]]


class OkCompatAdapter:
    """Stub compatible adapter — succeeds without touching any network."""

    async def invoke(self, rt, model_id, req):
        from app.services.ai.request import AIResponse
        return AIResponse(content="out_of_domain", task=req.task,
                          provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id,
                          provider_name=rt.display_name, model=model_id,
                          tokens_input=10, tokens_output=5, tokens_total=15,
                          request_id=req.request_id)


def _install(monkeypatch, adapter):
    from app.services.ai import engine
    monkeypatch.setattr(engine, "adapter_for", lambda ptype: adapter)


def _default_iid():
    from app.db.queries import get_setting
    return (get_setting("ai_default_instance_id", "") or "").strip()


# ── 1. Fresh install: one save → live routes, no restart ────────────────

def test_fresh_save_builds_live_routes_without_restart(client, monkeypatch):
    # Before the save the panel must report the honest 0/0, not has_key only.
    d = client.get("/admin/api/ai-connection").json()
    assert d["has_key"] is False
    assert d["routes"] == {"chat": 0, "classify": 0}

    res = _save(client)
    assert res.status_code == 200, res.text

    iid = _default_iid()
    assert iid, "panel save must record the default instance marker"
    for task in ("chat", "classify"):
        eligible = _eligible(task)
        assert eligible, f"{task} must have an eligible route target after save"
        assert eligible[0]["provider_instance_id"] == iid

    # The submitted key reached the routed instance's SECRET (encrypted at
    # rest by the store; revealed only here, inside the runtime).
    assert store.runtime_for(iid).secret == "sk-panel-111222333"

    # End-to-end: with the network stubbed, classify_intent resolves a route
    # and completes — the pre-fix state raised all_routes_failed → (None,0,0).
    _install(monkeypatch, OkCompatAdapter())
    from app.services.openai import classify_intent
    entry, tokens, cost = asyncio.run(classify_intent("یک پرسش دقیقا بی‌ربط"))
    assert entry is None and tokens == 15 and cost >= 0.0

    # Panel GET now reflects routing reality.
    d = client.get("/admin/api/ai-connection").json()
    assert d["has_key"] is True
    assert d["routes"] == {"chat": 1, "classify": 1}


def test_resave_is_idempotent_secret_rotated_not_duplicated(client):
    assert _save(client).status_code == 200
    first_iid = _default_iid()
    # Warm the runtime cache with the OLD secret BEFORE rotating: a save
    # that ever forgets _invalidate_runtime must serve the stale key below
    # and fail this test, not pass by accident on a cold cache.
    assert store.runtime_for(first_iid).secret == "sk-panel-111222333"
    res = _save(client, key="sk-panel-444555666")
    assert res.status_code == 200, res.text
    # Still exactly one instance and two targets — rotation updates, never
    # duplicates (UNIQUE(task, provider_instance_id, model_id) also guards).
    assert _default_iid() == first_iid
    assert len(store.list_instances()) == 1
    assert len(store.list_routes()["targets"]) == 2
    assert store.runtime_for(first_iid).secret == "sk-panel-444555666"


# ── 3. Hand-built routes are never silently altered ─────────────────────

def test_manual_routes_untouched_missing_task_filled_via_default(client):
    from app.db.queries import set_setting
    # A default marker EXISTS (as any panel save leaves behind) plus an
    # operator-built second instance carrying a custom chat target.
    default_iid = store.create_instance(
        "openai_compatible", "سرویس فعلی (مهاجرت‌یافته)",
        {"base_url": "https://api.gapgpt.app/v1"}, "sk-default-old",
        enabled=True)
    set_setting("ai_default_instance_id", default_iid)
    hand_iid = store.create_instance(
        "openai_compatible", "گیت‌وی دستی",
        {"base_url": "https://93.184.216.34/v1"}, "sk-hand",
        enabled=True)
    hand_target_id = store.add_target("chat", hand_iid, "custom-chat-model")
    chat_ids_before = [t["id"] for t in store.ordered_targets("chat")]

    res = _save(client)
    assert res.status_code == 200, res.text

    # The hand-built chat target is untouched (same id, still the custom model).
    chat_after = store.ordered_targets("chat")
    assert [t["id"] for t in chat_after] == chat_ids_before == [hand_target_id]
    assert chat_after[0]["model_id"] == "custom-chat-model"
    assert chat_after[0]["provider_instance_id"] == hand_iid
    # The missing classify task gets filled — via the DEFAULT instance, with
    # the legacy model id, never by guessing the hand-built one.
    classify = store.ordered_targets("classify")
    assert len(classify) == 1
    assert classify[0]["provider_instance_id"] == default_iid
    assert classify[0]["model_id"] == "gpt-5-nano"
    # No second (panel-owned) instance was created alongside the two.
    assert len(store.list_instances()) == 2
    # Rotation still reached the default instance's secret.
    assert store.runtime_for(default_iid).secret == "sk-panel-111222333"


def test_handbuilt_control_plane_with_no_marker_is_never_touched(client):
    from app.db.queries import get_setting
    hand_iid = store.create_instance(
        "openai_compatible", "گیت‌وی دستی",
        {"base_url": "https://93.184.216.34/v1"}, "sk-hand",
        enabled=True)
    store.add_target("chat", hand_iid, "custom-chat-model")

    res = _save(client)
    assert res.status_code == 200, res.text

    # Precedence rule (owner ruling): with operator-built instances and no
    # default marker, the panel creates NOTHING and routes NOTHING. The
    # unrouted classify task is health's job to report, not the panel's to
    # guess at.
    assert len(store.list_instances()) == 1
    assert store.ordered_targets("classify") == []
    assert _default_iid() == ""
    assert (get_setting("ai_default_instance_id", "") or "") == ""


# ── 4. Rotation on a migrated (boot-imported) install ───────────────────

def test_rotation_syncs_instance_secret_on_migrated_install(client):
    from app.db.queries import set_setting, get_setting
    from app.services.ai import legacy_import
    # Simulate the boot-time import: the app booted keyless above (marker
    # already set), so rewind the one-shot marker, seed the legacy config an
    # env-key install would have had, and run the exact boot import.
    set_setting("ai_control_plane_migrated", "")
    set_setting("ai_api_base", "https://api.gapgpt.app/v1")
    set_setting("ai_api_key", "sk-legacy-key-123456")
    set_setting("ai_model_chat", "gpt-4.1")
    set_setting("ai_model_classify", "gpt-5-nano")
    set_setting("openai_enabled", "true")
    imported_iid = legacy_import.run_import()
    assert imported_iid
    assert get_setting("ai_default_instance_id") == imported_iid

    # Warm the runtime cache with the OLD secret BEFORE rotating: on a cold
    # cache the post-save assert below would pass even if update_instance
    # ever forgot _invalidate_runtime — the rotation would be invisible to
    # the engine until an unrelated read refreshed the entry.
    assert store.runtime_for(imported_iid).secret == "sk-legacy-key-123456"

    res = _save(client, key="sk-rotated-987654")
    assert res.status_code == 200, res.text

    # The routed instance's secret rotated IMMEDIATELY (update_instance
    # invalidates the runtime cache — no restart). Pre-fix, only the legacy
    # row changed and the install split-brained on credentials.
    assert store.runtime_for(imported_iid).secret == "sk-rotated-987654"
    # The legacy row (STT fallback path) carries the same new key.
    assert get_setting("ai_api_key") == "sk-rotated-987654"
    # STT still resolves — implicit binding: exactly one enabled,
    # secret-bearing, STT-capable instance, now with the new key.
    from app.services.ai import stt
    base, key, model, source = stt.resolve()
    assert key == "sk-rotated-987654"
    assert base == "https://api.gapgpt.app/v1"
    assert source == "implicit"


# ── 5. Empty-key semantics preserved ────────────────────────────────────

def test_keyless_save_without_stored_key_skips_ensure(client):
    from app.db.queries import get_setting
    res = _save(client, key="")
    assert res.status_code == 200, res.text
    # Legacy row untouched, nothing built, and crucially no 500 from
    # create_instance rejecting a secret-less provider.
    assert (get_setting("ai_api_key", "") or "") == ""
    assert store.list_instances() == []
    assert _default_iid() == ""


def test_keyless_save_with_stored_key_updates_base(client):
    # IP-literal bases keep the test hermetic: the endpoint policy DNS-
    # resolves hostnames, and a made-up name would fail validation for a
    # reason unrelated to what this test pins.
    assert _save(client, base="https://93.184.216.34/v1").status_code == 200
    iid = _default_iid()
    assert store.get_instance(iid)["config"]["base_url"] == "https://93.184.216.34/v1"

    res = _save(client, base="https://93.184.216.34/v2", key="")
    assert res.status_code == 200, res.text
    # The stored key (server-side) drove the ensure: the base change reached
    # the default instance's config and the secret survived untouched.
    assert store.get_instance(iid)["config"]["base_url"] == "https://93.184.216.34/v2"
    assert store.runtime_for(iid).secret == "sk-panel-111222333"


# ── 6-8. Health tells the truth — and spends nothing doing it ────────────

def _probe_ai():
    from app.services import health
    return health.probe_one("ai_provider", force=True)


def test_health_reports_routing_reality(client):
    from app.db.queries import set_setting
    # No key → DISABLED (unchanged message).
    r = _probe_ai()
    assert r["status"] == "disabled"
    assert "تنظیم نشده" in r["detail_fa"]

    # Key + eligible routes → OK.
    assert _save(client).status_code == 200
    r = _probe_ai()
    assert r["status"] == "healthy"
    assert "api.gapgpt.app" in r["detail_fa"]

    # Key present but zero routes (the exact state the old code lied about:
    # targets removed by hand, key still stored) → DEGRADED with the
    # actionable re-save instruction.
    for task in ("chat", "classify"):
        for t in store.ordered_targets(task):
            store.remove_target(t["id"])
    r = _probe_ai()
    assert r["status"] == "degraded"
    assert "مسیر پاسخ‌گویی هوش مصنوعی فعال نیست" in r["detail_fa"]
    assert "دوباره ذخیره کنید" in r["detail_fa"]

    # Kill switch → DISABLED, an honest «خاموش است» — not an error.
    set_setting("openai_enabled", "false")
    r = _probe_ai()
    assert r["status"] == "disabled"
    assert "خاموش" in r["detail_fa"]


def test_health_probe_makes_zero_network_calls(client, monkeypatch):
    """A health check must never spend tokens (health.py probe rule). Pin it:
    the adapter factory explodes on contact in EVERY scenario and the probe
    still answers from configuration alone."""
    from app.db.queries import set_setting

    def tripwire(*a, **kw):
        raise AssertionError("health probe must never resolve a provider adapter")

    # Build every scenario's state FIRST (the panel save resolves real
    # adapters for config validation), then arm the tripwire on every
    # adapter_for reference the process holds.
    assert _save(client).status_code == 200
    for task in ("chat", "classify"):
        for t in store.ordered_targets(task):
            store.remove_target(t["id"])
    set_setting("openai_enabled", "false")

    monkeypatch.setattr("app.services.ai.adapters.adapter_for", tripwire)
    monkeypatch.setattr("app.services.ai.engine.adapter_for", tripwire)
    monkeypatch.setattr("app.services.ai.store.adapter_for", tripwire)

    r = _probe_ai()                          # kill switch → DISABLED
    assert r["status"] == "disabled"
    set_setting("openai_enabled", "true")
    r = _probe_ai()                          # key + zero routes → DEGRADED
    assert r["status"] == "degraded"
    # Re-add the targets straight through the store (SQLite only) so the OK
    # scenario is also proven under the tripwire.
    iid = _default_iid()
    store.add_manual_model(iid, "gpt-4.1")
    store.add_target("chat", iid, "gpt-4.1")
    store.add_target("classify", iid, "gpt-5-nano")
    r = _probe_ai()                          # key + eligible routes → OK
    assert r["status"] == "healthy"


def test_ensure_failure_surfaces_as_500_with_detail(client):
    """Owner ruling: no 200-with-warning soft mode — a save that did not do
    what the screen promised must fail loudly. An SSRF-rejected base makes
    update_instance's config validation raise AIError inside ensure."""
    assert _save(client).status_code == 200
    res = _save(client, base="http://169.254.169.254/v1")
    assert res.status_code == 500
    assert res.json().get("detail")
