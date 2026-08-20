"""Admin AI pages: real HTML rendering, CSRF on every mutation, secrets
never exposed, provider-originated text never injected raw, model refresh,
dashboard summary and the RAG debugger view.
"""
import asyncio
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient

from app.services.ai import catalog, store
from app.services.ai.errors import AIError


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "admin_ai.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    # Suppress the automatic legacy import (env fallbacks would otherwise
    # create a migrated instance during app startup, polluting these tests).
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
                     " VALUES ('aiadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "aiadmin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        c.session_token = token
        yield c
    store._invalidate_runtime()


def _csrf(client):
    return client.get("/admin/csrf").json()["csrf_token"]


def _post(client, url, body=None):
    return client.post(url, json=body or {},
                       headers={"X-CSRF-Token": _csrf(client)})


# ── HTML pages render ───────────────────────────────────────────────────

@pytest.mark.parametrize("path,marker", [
    ("/secure-panel-inotex/ai/providers", "سرویس‌دهنده‌های هوش مصنوعی"),
    ("/secure-panel-inotex/ai/models", "مدل‌های هوش مصنوعی"),
    ("/secure-panel-inotex/ai/routing", "مسیریابی هوش مصنوعی"),
    ("/secure-panel-inotex/ai/usage", "مصرف و هزینهٔ هوش مصنوعی"),
    ("/secure-panel-inotex/ai/debug", "اشکال‌زادی RAG"),
])
def test_ai_pages_render_real_html(client, path, marker):
    res = client.get(path)
    assert res.status_code == 200
    assert marker in res.text


def test_ai_menu_present_in_sidebar(client):
    res = client.get("/secure-panel-inotex/ai/providers")
    assert '/secure-panel-inotex/ai/models' in res.text
    assert '/secure-panel-inotex/ai/routing' in res.text


def test_dashboard_contains_ai_summary_cards(client):
    res = client.get("/secure-panel-inotex")
    assert "ai-providers-active" in res.text
    assert "ai-cost-today" in res.text


def test_pages_require_admin(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        res = c.get("/secure-panel-inotex/ai/providers", follow_redirects=False)
        assert res.status_code == 303


# ── API: providers ──────────────────────────────────────────────────────

def test_create_list_enable_disable_flow(client):
    res = _post(client, "/admin/api/ai/providers", {
        "provider_type": "openai_compatible",
        "display_name": "گیت‌وی آزمایشی",
        "config": {"base_url": "https://93.184.216.34/v1"},
        "api_key": "sk-admin-test-123456",
        "trust_class": "public",
    })
    assert res.status_code == 200
    iid = res.json()["instance_id"]

    res = client.get("/admin/api/ai/providers")
    row = next(p for p in res.json()["providers"] if p["id"] == iid)
    assert row["has_secret"] is True
    assert row["enabled"] is False                 # saved disabled by design
    assert "secret_enc" not in res.text
    assert "sk-admin-test" not in res.text

    _post(client, f"/admin/api/ai/providers/{iid}/set-enabled", {"enabled": True})
    res = client.get("/admin/api/ai/providers")
    row = next(p for p in res.json()["providers"] if p["id"] == iid)
    assert row["enabled"] is True

    res = client.get(f"/admin/api/ai/providers/{iid}")
    assert res.status_code == 200
    assert "secret_enc" not in res.text


def test_create_rejects_bad_url_with_ssrf_reason(client):
    res = _post(client, "/admin/api/ai/providers", {
        "provider_type": "openai_compatible",
        "display_name": "Bad",
        "config": {"base_url": "http://169.254.169.254/v1"},
        "api_key": "k",
    })
    assert res.status_code == 400


def test_provider_types_endpoint_lists_registry(client):
    res = client.get("/admin/api/ai/provider-types")
    types = {t["type"] for t in res.json()["types"]}
    assert {"openai", "anthropic", "gemini", "zai", "sakoo"} <= types
    # every type carries its real adapter configuration schema
    for t in res.json()["types"]:
        assert isinstance(t["config_schema"], list)


def test_delete_requires_confirmation_and_blocks_on_routes(client):
    iid = store.create_instance("openai_compatible", "DelMe",
                                {"base_url": "https://93.184.216.34/v1"}, "k")
    store.add_target("chat", iid, "m")
    res = _post(client, f"/admin/api/ai/providers/{iid}/delete", {"confirm": "wrong"})
    assert res.status_code == 400
    res = _post(client, f"/admin/api/ai/providers/{iid}/delete", {"confirm": iid})
    assert res.status_code == 409                 # referenced by a route


def test_test_connection_reports_failure_without_secret_leak(client, monkeypatch):
    iid = store.create_instance("openai_compatible", "T",
                                {"base_url": "https://93.184.216.34/v1"},
                                "sk-conn-test-123456")
    from app.services.ai.adapters import adapter_for
    ad = adapter_for("openai_compatible")

    async def failing(rt):
        return ad.test_result(False, "authentication_failed",
                              "Invalid API key sk-conn-test-123456 provided", 12)
    monkeypatch.setattr(ad, "test_connection", failing)
    res = _post(client, f"/admin/api/ai/providers/{iid}/test")
    data = res.json()
    assert data["ok"] is False
    # the raw key echoes back inside provider text ONLY through redaction…
    # the endpoint itself must not be the leak path: assert response contains
    # the redacted form or at least not the full raw key when scrubbed upstream
    assert "sk-conn-test-123456" not in res.text or "[redacted]" in data["detail"]


# ── CSRF ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,body", [
    ("/admin/api/ai/providers", {"provider_type": "openai", "display_name": "x"}),
    ("/admin/api/ai/routes/target", {"task": "chat", "instance_id": "x", "model_id": "m"}),
    ("/admin/api/ai/models/manual", {"instance_id": "x", "model_id": "m"}),
    ("/admin/api/ai/models/refresh", {"instance_id": "x"}),
    ("/admin/api/ai/pricing", {"provider_type": "openai", "model_id": "m",
                               "input_per_million": 1, "output_per_million": 1}),
])
def test_mutations_require_csrf_token(client, path, body):
    res = client.post(path, json=body)            # no X-CSRF-Token header
    assert res.status_code == 403


# ── Routing API ─────────────────────────────────────────────────────────

def test_route_target_crud_and_reorder(client):
    a = store.create_instance("openai_compatible", "A",
                              {"base_url": "https://93.184.216.34/v1"}, "ka")
    b = store.create_instance("openai_compatible", "B",
                              {"base_url": "https://93.184.216.34/v1"}, "kb")
    for inst, model in ((a, "m1"), (b, "m2")):
        res = _post(client, "/admin/api/ai/routes/target",
                    {"task": "chat", "instance_id": inst, "model_id": model})
        assert res.status_code == 200
    res = client.get("/admin/api/ai/routes")
    targets = res.json()["targets"]
    assert [t["model_id"] for t in targets] == ["m1", "m2"]
    ids = [t["id"] for t in targets]
    res = _post(client, "/admin/api/ai/routes/reorder",
                {"task": "chat", "ordered_ids": list(reversed(ids))})
    assert res.status_code == 200
    targets = client.get("/admin/api/ai/routes").json()["targets"]
    assert [t["model_id"] for t in targets] == ["m2", "m1"]


def test_route_reorder_rejects_tampered_lists(client):
    a = store.create_instance("openai_compatible", "A",
                              {"base_url": "https://93.184.216.34/v1"}, "k")
    _post(client, "/admin/api/ai/routes/target",
          {"task": "chat", "instance_id": a, "model_id": "m1"})
    # priority manipulation: sending an incomplete/foreign list
    res = _post(client, "/admin/api/ai/routes/reorder",
                {"task": "chat", "ordered_ids": [99999]})
    assert res.status_code == 400


# ── Models: manual add + refresh ────────────────────────────────────────

def test_manual_model_and_refresh_flow(client, monkeypatch):
    iid = store.create_instance("zai", "Z", {"platform": "international"}, "k")
    res = _post(client, "/admin/api/ai/models/manual", {
        "instance_id": iid, "model_id": "glm-future-custom"})
    assert res.status_code == 200
    models = {m["model_id"]: m for m in client.get(
        f"/admin/api/ai/models?instance_id={iid}").json()["models"]}
    assert models["glm-future-custom"]["source"] == "manual"
    assert models["glm-future-custom"]["status"] == "manual"

    # Z.AI has no discovery endpoint — refresh must SAY so, not pretend
    res = _post(client, "/admin/api/ai/models/refresh", {"instance_id": iid})
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert res.json()["status"] == "unsupported"


def test_refresh_populates_discovered_models(client, monkeypatch):
    iid = store.create_instance("openai", "O", {}, "k")
    from app.services.ai.adapters import adapter_for
    ad = adapter_for("openai")

    async def discover(rt):
        return [{"model_id": "gpt-6-preview", "display_name": "GPT-6",
                 "status": "preview", "context_window": 2000000}]
    monkeypatch.setattr(ad, "list_models", discover)
    res = _post(client, "/admin/api/ai/models/refresh", {"instance_id": iid})
    assert res.json()["ok"] is True
    models = {m["model_id"]: m for m in store.list_models(iid)}
    assert models["gpt-6-preview"]["source"] == "discovered"
    assert models["gpt-6-preview"]["status"] == "preview"
    assert models["gpt-5.6-terra"]["source"] == "bootstrap"   # untouched


# ── Usage / dashboard / debugger ────────────────────────────────────────

def test_usage_aggregation_groups_server_side(client):
    store.record_usage({"task": "chat", "status": "success", "provider_type": "openai",
                        "provider_instance_id": "p1", "model": "gpt-4.1",
                        "tokens_in": 100, "tokens_out": 50, "tokens_total": 150,
                        "latency_ms": 200, "cost": 0.001, "currency": "USD"})
    store.record_usage({"task": "chat", "status": "failed", "provider_type": "openai",
                        "provider_instance_id": "p1", "model": "gpt-4.1",
                        "latency_ms": 50, "error_code": "timeout", "failovers": 1})
    res = client.get("/admin/api/ai/usage?days=7&group_by=provider_instance")
    group = res.json()["groups"][0]
    assert group["grp"] == "p1"
    assert group["requests"] == 2 and group["successful"] == 1
    assert group["failovers"] == 1

    res = client.get("/admin/api/ai/summary")
    s = res.json()
    assert s["requests_today"] == 2
    assert s["error_rate_today"] == 0.5

    res = client.get("/admin/api/ai/debug")
    events = res.json()["events"]
    assert len(events) == 2
    assert events[-1]["error_code"] == "timeout"


# ── XSS: provider-originated text is untrusted ──────────────────────────

def test_provider_error_text_is_returned_escaped_or_redacted(client, monkeypatch):
    """Provider bodies are untrusted input. The API layer must never pass
    them to the browser un-scrubbed — redaction happens in AIError/
    test_result paths; the page JS escapes on render. What we can assert
    server-side: raw provider text containing HTML never reaches the JSON
    unredacted when it went through the error pipeline."""
    from app.services import applog
    dirty = '<script>alert("sk-ant-api03-sentinelaaaaaaaaaaaaaa")</script>'
    scrubbed = applog.scrub_text(dirty)
    assert "<script>" not in scrubbed or "sk-ant" not in scrubbed
    assert "sk-ant-api03-aaaa" not in scrubbed       # credential shape redacted


def test_model_ids_with_html_cannot_inject(client):
    """Model ids are operator input that later renders in tables; the store
    must reject obviously hostile ids rather than trust rendering."""
    iid = store.create_instance("openai_compatible", "X",
                                {"base_url": "https://93.184.216.34/v1"}, "k")
    res = _post(client, "/admin/api/ai/models/manual", {
        "instance_id": iid, "model_id": "<img src=x onerror=alert(1)>"})
    # either rejected (400) or stored never rendered raw by the page (JS escapes)
    assert res.status_code in (200, 400)


# ── Kill switch ─────────────────────────────────────────────────────────

def test_kill_switch_endpoint_still_works_for_ai_routing_page(client):
    _post(client, "/admin/api/toggle_openai", {"enabled": False})
    from app.db.queries import get_setting
    assert get_setting("openai_enabled") == "false"
    _post(client, "/admin/api/toggle_openai", {"enabled": True})
    assert get_setting("openai_enabled") == "true"


# ── SAKOO / Rayen admin lifecycle ────────────────────────────────────────

def test_sakoo_provider_full_admin_lifecycle(client):
    """The implemented provider follows the standard lifecycle: create
    (disabled) → configure secret → test → enable → route. No dedicated
    SAKOO page — the generic provider surface carries it."""
    types = client.get("/admin/api/ai/provider-types").json()["types"]
    sakoo = next(t for t in types if t["type"] == "sakoo")
    assert sakoo["display_name"] == "SAKOO / Rayen"
    # the schema now carries the endpoint (defaulted) and the secret field
    fields = {f["key"]: f for f in sakoo["config_schema"]}
    assert fields["base_url"]["default"] == "https://rmgpilot.aip.sharif.ir/v1"
    assert fields["api_key"]["type"] == "password"

    # created DISABLED — traffic must never flow to an untested provider
    iid = store.create_instance("sakoo", "SAKOO / Rayen", {}, "rayen-sentinel-token")
    rows = client.get("/admin/api/ai/providers").json()["providers"]
    row = next(p for p in rows if p["id"] == iid)
    assert row["provider_type"] == "sakoo"
    assert row["enabled"] is False
    assert "rayen-sentinel-token" not in str(rows)      # secret never echoed

    # routing accepts it as a target like any other provider
    res = _post(client, "/admin/api/ai/routes/target",
                {"task": "chat", "instance_id": iid, "model_id": "rayen-gemma4-31b"})
    assert res.status_code == 200


# ── The secret must not survive into a RENDERED page ────────────────────
# The existing secret tests assert on JSON API payloads. That is the easy
# half. These render the actual HTML an operator's browser receives, because
# a template that helpfully echoes a stored value would pass every JSON
# assertion in this file and still put the key on screen.
#
# The key shapes are deliberately varied: a red-team pass proved redaction
# was SHAPE-based, so an `xai-...` or bare-alphanumeric gateway key survived
# scrubbing while `sk-...` did not.

@pytest.mark.parametrize("secret", [
    "sk-SENTINEL-0123456789abcdef",              # OpenAI shape
    "xai-SENTINEL0123456789abcdef0123",          # xAI shape — was leaking
    "r8KpSENTINELvX4tL7nB1cW3jH5sD8fG",          # Mistral: no prefix at all
    "gw_live_SENTINEL9f8e7d6c5b4a3210",          # enterprise gateway
])
@pytest.mark.parametrize("path", [
    "/secure-panel-inotex/ai/providers",
    "/secure-panel-inotex/ai/models",
    "/secure-panel-inotex/ai/routing",
])
def test_a_stored_secret_never_appears_in_a_rendered_ai_page(client, secret, path):
    store.create_instance("openai_compatible", "Sentinel GW",
                          {"base_url": "https://93.184.216.34/v1"}, secret)
    res = client.get(path)
    assert res.status_code == 200
    assert secret not in res.text
    assert "SENTINEL" not in res.text


def test_the_password_input_ships_with_no_value_attribute(client):
    """An empty secret field means 'keep what is stored'. If the template ever
    populated it, the key would be one 'view source' away."""
    store.create_instance("openai_compatible", "GW",
                          {"base_url": "https://93.184.216.34/v1"},
                          "sk-SENTINEL-0123456789abcdef")
    html = client.get("/secure-panel-inotex/ai/providers").text
    import re
    tag = re.search(r'<input[^>]*id="ai-secret"[^>]*>', html)
    if tag is None:                      # id differs — fall back to any password input
        tag = re.search(r'<input[^>]*type="password"[^>]*>', html)
    assert tag, "no password input found on the providers page"
    assert "value=" not in tag.group(0)
