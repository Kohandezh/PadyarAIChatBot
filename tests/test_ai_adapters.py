"""Contract tests for every provider adapter.

No network, no credentials: adapter.http() is monkeypatched to return canned
(status, body, headers). What is verified is the CONTRACT — request
transformation, response parsing that never assumes text, error
normalization from status+body, usage computation, discovery parsing,
capability gating and configuration validation.

These tests encode the research findings: if a provider changes its wire
shape, the fixture here is what breaks — which is the point.
"""
import asyncio

import pytest

from app.services.ai import errors as ai_errors
from app.services.ai.adapters import AI_PROVIDER_REGISTRY, adapter_for
from app.services.ai.adapters.base import ProviderRuntime
from app.services.ai.request import AIRequest, AIMessage


def rt(provider_type, config=None, secret="k-test-123456789", instance_id="inst1",
       trust="public"):
    return ProviderRuntime(instance_id=instance_id, provider_type=provider_type,
                           display_name="Test", enabled=True, trust_class=trust,
                           config=config or {}, secret=secret)


def req(task="chat", reasoning="default", temperature=None, system="SYS"):
    return AIRequest(task=task, messages=[AIMessage(role="user", content="سلام")],
                     system_prompt=system, max_output_tokens=500,
                     temperature=temperature, reasoning=reasoning)


def patch_http(monkeypatch, adapter, responses):
    """responses: list of (status, body, headers) returned in order."""
    state = {"i": 0}

    async def fake_http(_rt, method, url, **kw):
        r = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        return r

    monkeypatch.setattr(adapter, "http", fake_http)
    return state


def captured_body(adapter):
    """The adapter must record what it sent — inject a capture via patch."""
    return getattr(adapter, "_last_body", None)


def capture_body(monkeypatch, adapter):
    capture_with(monkeypatch, adapter, _OK_BODY.get(adapter.PROVIDER_TYPE, {}))
def capture_with(monkeypatch, adapter, response_body, status=200):
    """Patch adapter.http to capture the request AND return a canned body."""
    async def fake_http(_rt, method, url, headers=None, body=None, timeout_s=None):
        adapter._last_body = body
        adapter._last_headers = headers or {}
        adapter._last_url = url
        return status, response_body, {}
    monkeypatch.setattr(adapter, "http", fake_http)



_OK_BODY = {
    "openai_compatible": {"id": "x", "choices": [{"index": 0, "message": {
        "role": "assistant", "content": "پاسخ"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                  "prompt_tokens_details": {"cached_tokens": 4}}},
}


# ── Registry ────────────────────────────────────────────────────────────

def test_registry_has_all_types():
    assert set(AI_PROVIDER_REGISTRY) == {
        "openai", "anthropic", "gemini", "zai", "kimi", "deepseek", "qwen",
        "xai", "mistral", "openai_compatible", "sakoo"}


def test_every_adapter_has_metadata_and_schema():
    for ptype, cls in AI_PROVIDER_REGISTRY.items():
        ad = adapter_for(ptype)
        meta = ad.metadata()
        assert meta.type_key == ptype
        assert meta.display_name
        schema = ad.configuration_schema()
        assert isinstance(schema, list)


def test_unknown_provider_type_rejected():
    with pytest.raises(ai_errors.AIError):
        adapter_for("nope")


# ── Configuration validation ────────────────────────────────────────────

def test_qwen_workspace_domain_requires_workspace_id():
    ad = adapter_for("qwen")
    with pytest.raises(ai_errors.AIError) as e:
        ad.validate_config({"api_key": "x", "region": "ap-southeast-1",
                            "domain": "workspace"}, "public")
    assert e.value.code == "invalid_request"


def test_qwen_builds_region_urls():
    ad = adapter_for("qwen")
    cfg = ad.validate_config({"region": "ap-southeast-1", "domain": "dashscope"}, "public")
    assert "dashscope-intl.aliyuncs.com" in cfg["resolved_base_url"]
    cfg = ad.validate_config({"region": "cn-beijing", "domain": "workspace",
                              "workspace_id": "llm-123"}, "public")
    assert "llm-123.cn-beijing.maas.aliyuncs.com" in cfg["resolved_base_url"]


def test_url_field_rejects_metadata_endpoint():
    ad = adapter_for("openai_compatible")
    with pytest.raises(ai_errors.AIError):
        ad.validate_config({"base_url": "http://169.254.169.254/v1"}, "public")


def test_enum_field_rejects_unknown_choice():
    ad = adapter_for("gemini")
    with pytest.raises(ai_errors.AIError):
        ad.validate_config({"api_version": "v9"}, "public")


def test_private_url_allowed_only_with_internal_trust():
    ad = adapter_for("openai_compatible")
    with pytest.raises(ai_errors.AIError):
        ad.validate_config({"base_url": "http://10.0.0.5:11434/v1"}, "public")
    cleaned = ad.validate_config({"base_url": "http://10.0.0.5:11434/v1"}, "internal")
    assert cleaned["base_url"].startswith("http://10.0.0.5")


# ── Sampling capability gating (the temperature landmine) ───────────────

def test_anthropic_drops_temperature_on_47_plus_models(monkeypatch):
    ad = adapter_for("anthropic")
    anthropic_ok = {"content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 3, "output_tokens": 2}}
    capture_with(monkeypatch, ad, anthropic_ok)
    asyncio.run(ad.invoke(rt("anthropic"), "claude-opus-5",
                          req(temperature=0.66)))
    assert "temperature" not in ad._last_body
    # sonnet-4-6 predates 4.7: temperature IS sent
    asyncio.run(ad.invoke(rt("anthropic"), "claude-sonnet-4-6",
                          req(temperature=0.66)))
    assert ad._last_body.get("temperature") == 0.66


def test_kimi_pinned_sampling_on_k_series(monkeypatch):
    ad = adapter_for("kimi")
    assert ad.sampling_policy("kimi-k3") == {"temperature": False, "top_p": False}
    assert ad.sampling_policy("moonshot-v1-8k") == {"temperature": True, "top_p": True}
    capture_with(monkeypatch, ad, _OK_BODY["openai_compatible"])
    asyncio.run(ad.invoke(rt("kimi"), "kimi-k2.6", req(temperature=0.5)))
    assert "temperature" not in ad._last_body
    assert "max_completion_tokens" in ad._last_body and "max_tokens" not in ad._last_body


def test_deepseek_drops_sampling_when_thinking(monkeypatch):
    ad = adapter_for("deepseek")
    capture_with(monkeypatch, ad, _OK_BODY["openai_compatible"])
    asyncio.run(ad.invoke(rt("deepseek"), "deepseek-v4-pro",
                          req(reasoning="high", temperature=0.3)))
    assert ad._last_body["thinking"] == {"type": "enabled"}
    assert "temperature" not in ad._last_body
    asyncio.run(ad.invoke(rt("deepseek"), "deepseek-v4-flash",
                          req(reasoning="off", temperature=0.3)))
    assert ad._last_body["thinking"] == {"type": "disabled"}
    assert ad._last_body.get("temperature") == 0.3


def test_gemini_never_sends_temperature(monkeypatch):
    ad = adapter_for("gemini")
    capture_with(monkeypatch, ad, {"status": "completed", "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": "ok"}]}],
        "usage": {"total_input_tokens": 5, "total_output_tokens": 3,
                  "total_tokens": 8}})
    asyncio.run(ad.invoke(rt("gemini"), "gemini-3.7-flash", req(temperature=0.66)))
    assert "temperature" not in ad._last_body.get("generation_config", {})
    assert ad._last_body["store"] is False
    assert ad._last_headers.get("x-goog-api-key") == "k-test-123456789"
    assert ad._last_headers.get("Api-Revision")  # pinned


def test_zai_clamps_temperature_and_always_sends_thinking(monkeypatch):
    ad = adapter_for("zai")
    capture_with(monkeypatch, ad, _OK_BODY["openai_compatible"])
    asyncio.run(ad.invoke(rt("zai"), "glm-5.3", req(temperature=1.7)))
    assert ad._last_body["temperature"] == 1.0          # range [0,1]
    assert "thinking" in ad._last_body                   # never inherited default
    asyncio.run(ad.invoke(rt("zai"), "glm-4.7-flash", req(reasoning="off")))
    assert ad._last_body["thinking"] == {"type": "disabled"}


# ── System prompt placement ─────────────────────────────────────────────

def test_anthropic_system_is_top_level_never_message_zero(monkeypatch):
    ad = adapter_for("anthropic")
    capture_with(monkeypatch, ad, {"content": [{"type": "text", "text": "ok"}],
                                   "stop_reason": "end_turn",
                                   "usage": {"input_tokens": 3, "output_tokens": 2}})
    asyncio.run(ad.invoke(rt("anthropic"), "claude-sonnet-5", req()))
    assert ad._last_body["system"] == "SYS"
    assert all(m["role"] != "system" for m in ad._last_body["messages"])
    assert "max_tokens" in ad._last_body                    # required there


def test_openai_system_goes_to_instructions(monkeypatch):
    ad = adapter_for("openai")
    capture_with(monkeypatch, ad, {"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
    asyncio.run(ad.invoke(rt("openai"), "gpt-5.6-terra", req()))
    assert ad._last_body["instructions"] == "SYS"
    assert ad._last_body["store"] is False
    assert all("role" in i for i in ad._last_body["input"])


def test_gemini_system_instruction_is_plain_string(monkeypatch):
    ad = adapter_for("gemini")
    capture_with(monkeypatch, ad, {"status": "completed", "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": "ok"}]}],
        "usage": {}})
    r = asyncio.run(ad.invoke(rt("gemini"), "gemini-3.7-flash",
                              AIRequest(task="chat", messages=[AIMessage("user", "hi"),
                                                               AIMessage("assistant", "prev"),
                                                               AIMessage("user", "?")],
                                        system_prompt="SYS", max_output_tokens=10)))
    body = ad._last_body
    assert body["system_instruction"] == "SYS"
    types = [s["type"] for s in body["input"]]
    assert types == ["user_input", "model_output", "user_input"]  # never "assistant"


# ── Response parsing that never assumes text ────────────────────────────

def test_gemini_safety_block_is_content_rejected(monkeypatch):
    ad = adapter_for("gemini")
    # HTTP 200, valid body, NO text — the documented safety-block signature.
    async def blocked(*a, **kw):
        return 200, {"status": "completed", "steps": []}, {}
    monkeypatch.setattr(ad, "http", blocked)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt("gemini"), "gemini-3.7-flash", req()))
    assert e.value.code == "content_rejected"
    assert not e.value.failover_eligible          # every provider refuses it


def test_gemini_error_array_on_http_200(monkeypatch):
    ad = adapter_for("gemini")
    async def faulty(*a, **kw):
        return 200, {"status": "failed",
                     "error": [{"code": "service_unavailable", "message": "x"}]}, {}
    monkeypatch.setattr(ad, "http", faulty)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt("gemini"), "gemini-3.7-flash", req()))
    assert e.value.code == "provider_unavailable"


def test_openai_200_with_failed_status(monkeypatch):
    ad = adapter_for("openai")
    async def failed(*a, **kw):
        return 200, {"status": "failed",
                     "error": {"code": "server_error", "message": "boom"}}, {}
    monkeypatch.setattr(ad, "http", failed)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt("openai"), "gpt-5.6-terra", req()))
    assert e.value.code in ("server_error", "provider_unavailable")


def test_openai_incomplete_max_tokens_maps_to_length(monkeypatch):
    ad = adapter_for("openai")
    async def incomplete(*a, **kw):
        return 200, {"status": "incomplete",
                     "incomplete_details": {"reason": "max_output_tokens"},
                     "output": [{"type": "message",
                                 "content": [{"type": "output_text", "text": "partial"}]}],
                     "usage": {}}, {}
    monkeypatch.setattr(ad, "http", incomplete)
    resp = asyncio.run(ad.invoke(rt("openai"), "gpt-5.6-terra", req()))
    assert resp.finish_reason == "length"


def test_anthropic_joins_text_blocks_and_maps_refusal(monkeypatch):
    ad = adapter_for("anthropic")
    async def refusal(*a, **kw):
        return 200, {"content": [{"type": "text", "text": "a"}], "stop_reason": "refusal",
                     "stop_details": {"explanation": "policy"}, "usage": {}}, {}
    monkeypatch.setattr(ad, "http", refusal)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt("anthropic"), "claude-opus-5", req()))
    assert e.value.code == "content_rejected"


def test_anthropic_context_window_finish_reason(monkeypatch):
    ad = adapter_for("anthropic")
    async def over(*a, **kw):
        return 200, {"content": [], "stop_reason": "model_context_window_exceeded",
                     "usage": {}}, {}
    monkeypatch.setattr(ad, "http", over)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt("anthropic"), "claude-opus-5", req()))
    assert e.value.code == "context_limit_exceeded"
    assert not e.value.failover_eligible


def test_zai_sensitive_finish_reason(monkeypatch):
    ad = adapter_for("zai")
    async def sensitive(*a, **kw):
        return 200, {"choices": [{"message": {"content": ""},
                                  "finish_reason": "sensitive"}], "usage": {}}, {}
    monkeypatch.setattr(ad, "http", sensitive)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt("zai"), "glm-4.7", req()))
    assert e.value.code == "content_rejected"


def test_zai_flat_error_body_on_http_200(monkeypatch):
    ad = adapter_for("zai")
    async def flat(*a, **kw):
        return 200, {"code": 500, "msg": "404 NOT_FOUND", "success": False}, {}
    monkeypatch.setattr(ad, "http", flat)
    with pytest.raises(ai_errors.AIError):
        asyncio.run(ad.invoke(rt("zai"), "glm-5.3", req()))


# ── Usage extraction (computed, never copied) ───────────────────────────

def test_anthropic_usage_computes_true_input():
    ad = adapter_for("anthropic")
    u = ad.extract_usage({"usage": {
        "input_tokens": 50,            # AFTER the last cache breakpoint only
        "cache_read_input_tokens": 200000,
        "cache_creation_input_tokens": 1000,
        "output_tokens": 80,
        "output_tokens_details": {"thinking_tokens": 12}}})
    assert u["tokens_in"] == 201050     # the naive read would report 50
    assert u["cached"] == 200000
    assert u["tokens_total"] == 201130
    assert u["reasoning"] == 12


def test_deepseek_cache_hit_miss_vocabulary():
    ad = adapter_for("deepseek")
    u = ad.extract_usage({"usage": {"prompt_tokens": 1000,
                                    "prompt_cache_hit_tokens": 900,
                                    "prompt_cache_miss_tokens": 100,
                                    "completion_tokens": 40, "total_tokens": 1040}})
    assert u["tokens_in"] == 1000
    assert u["cached"] == 900


def test_kimi_flat_cached_tokens():
    ad = adapter_for("kimi")
    u = ad.extract_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                    "total_tokens": 15, "cached_tokens": 7}})
    assert u["cached"] == 7


def test_unknown_usage_stays_none():
    ad = adapter_for("openai")
    u = ad.extract_usage({})
    assert u["tokens_in"] is None and u["tokens_out"] is None


# ── Error normalization from status + parsed body ───────────────────────

@pytest.mark.parametrize("status,body,expected", [
    (401, {"error": {"type": "invalid_request_error", "message": "Incorrect API key"}},
     "authentication_failed"),
    (429, {"error": {"code": "credit_balance_exhausted", "message": ""}}, "quota_exceeded"),
    (429, {"error": {"type": "rate_limit_error", "message": ""}}, "rate_limited"),
    (500, {"error": {"type": "server_error"}}, "server_error"),
])
def test_openai_error_map(status, body, expected):
    ad = adapter_for("openai")
    assert ad.error_code_from_body(status, body) == expected


def test_anthropic_overloaded_529():
    ad = adapter_for("anthropic")
    assert ad.error_code_from_body(529, {"error": {"type": "overloaded_error"}}) \
        == "provider_unavailable"


def test_anthropic_billing_402():
    ad = adapter_for("anthropic")
    assert ad.error_code_from_body(402, {"error": {"type": "billing_error"}}) \
        == "quota_exceeded"


def test_kimi_quota_vs_rate_split():
    ad = adapter_for("kimi")
    assert ad.error_code_from_body(429, {"error": {"type": "exceeded_current_quota_error"}}) == "quota_exceeded"
    assert ad.error_code_from_body(429, {"error": {"type": "rate_limit_reached_error"}}) == "rate_limited"
    assert ad.error_code_from_body(400, {"error": {"type": "content_filter"}}) == "content_rejected"


def test_zai_429_billing_codes_do_not_rate_limit():
    ad = adapter_for("zai")
    assert ad.error_code_from_body(429, {"error": {"code": "1113"}}) == "quota_exceeded"
    assert ad.error_code_from_body(429, {"error": {"code": "1302"}}) == "rate_limited"
    assert ad.error_code_from_body(429, {"error": {"code": "1311"}}) == "quota_exceeded"


def test_qwen_dual_named_codes():
    ad = adapter_for("qwen")
    assert ad.error_code_from_body(401, {"error": {"code": "InvalidApiKey"}}) == "authentication_failed"
    assert ad.error_code_from_body(400, {"error": {"code": "Arrearage"}}) == "quota_exceeded"
    assert ad.error_code_from_body(400, {"code": "DataInspectionFailed"}) == "content_rejected"


def test_xai_flat_error_envelope():
    ad = adapter_for("xai")
    assert ad.error_code_from_body(401, {"code": "unauthenticated:no-credentials",
                                         "error": "No credentials."}) == "authentication_failed"


def test_mistral_flat_and_nested_shapes():
    ad = adapter_for("mistral")
    assert ad.error_code_from_body(404, {"object": "error", "type": "",
                                         "code": "unknown_model"}) == "model_not_found"
    assert ad.error_code_from_body(401, {"error": {"type": "authentication_error"}}) == "authentication_failed"


# ── Discovery parsing ───────────────────────────────────────────────────

def test_gemini_discovery_strips_prefix_and_paginates(monkeypatch):
    ad = adapter_for("gemini")
    pages = [
        (200, {"models": [{"name": "models/gemini-3.7-flash",
                           "displayName": "Gemini 3.7 Flash",
                           "inputTokenLimit": 1000000, "outputTokenLimit": 65536,
                           "supportedGenerationMethods": ["generateContent"],
                           "thinking": True}],
               "nextPageToken": "p2"}, {}),
        (200, {"models": [{"name": "models/gemini-2.5-flash-lite",
                           "displayName": "Lite"}]}, {}),
    ]
    state = {"i": 0}

    async def paged(*a, **kw):
        r = pages[min(state["i"], 1)]
        state["i"] += 1
        return r
    monkeypatch.setattr(ad, "http", paged)
    models = asyncio.run(ad.list_models(rt("gemini")))
    assert models[0]["model_id"] == "gemini-3.7-flash"     # prefix stripped
    assert models[0]["context_window"] == 1000000
    assert models[0]["supports_reasoning"] is True
    assert [m["model_id"] for m in models][-1] == "gemini-2.5-flash-lite"


def test_mistral_discovery_keeps_rich_metadata(monkeypatch):
    ad = adapter_for("mistral")
    async def one(*a, **kw):
        return 200, {"data": [{
            "id": "mistral-medium-3-5", "name": "Mistral Medium 3.5",
            "max_context_length": 256000, "aliases": ["mistral-medium-latest"],
            "deprecation": None, "capabilities": {"completion_chat": True,
                                                  "reasoning": True}}]}, {}
    monkeypatch.setattr(ad, "http", one)
    models = asyncio.run(ad.list_models(rt("mistral")))
    assert models[0]["context_window"] == 256000
    assert models[0]["supports_reasoning"] is True
    assert models[0]["metadata"]["aliases"] == ["mistral-medium-latest"]


def test_xai_discovery_converts_price_units(monkeypatch):
    ad = adapter_for("xai")
    async def priced(*a, **kw):
        # "USD cents per 100M tokens": 20000 → $2.00 per 1M
        return 200, {"data": [{"id": "grok-4.6", "aliases": ["grok-4.6-latest"],
                               "context_length": 500000,
                               "prompt_text_token_price": 20000,
                               "completion_text_token_price": 60000}]}, {}
    monkeypatch.setattr(ad, "http", priced)
    models = asyncio.run(ad.list_models(rt("xai")))
    assert models[0]["metadata"]["price_input_usd_per_m"] == 2.0
    assert models[0]["metadata"]["price_output_usd_per_m"] == 6.0


def test_openai_discovery_marks_shutdown_date_deprecated(monkeypatch):
    ad = adapter_for("openai")
    async def listed(*a, **kw):
        return 200, {"data": [
            {"id": "gpt-5.6-terra", "owned_by": "openai", "shutdown_date": None},
            {"id": "gpt-5-nano", "owned_by": "openai", "shutdown_date": "2026-12-11"}]}, {}
    monkeypatch.setattr(ad, "http", listed)
    models = asyncio.run(ad.list_models(rt("openai")))
    by_id = {m["model_id"]: m for m in models}
    assert by_id["gpt-5.6-terra"]["status"] == "available"
    assert by_id["gpt-5-nano"]["status"] == "deprecated"


def test_kimi_discovery_capability_flags(monkeypatch):
    ad = adapter_for("kimi")
    async def listed(*a, **kw):
        return 200, {"data": [{"id": "kimi-k3", "context_length": 1048576,
                               "supports_reasoning": True, "supports_image_in": True}]}, {}
    monkeypatch.setattr(ad, "http", listed)
    models = asyncio.run(ad.list_models(rt("kimi")))
    assert models[0]["supports_reasoning"] and models[0]["supports_vision"]


# ── SAKOO: architecture slot, zero network ──────────────────────────────

def test_sakoo_adapter_cannot_reach_the_network():
    ad = adapter_for("sakoo")
    with pytest.raises(ai_errors.AIError):
        asyncio.run(ad.http(rt("sakoo"), "GET", "https://anything.example"))
    with pytest.raises(ai_errors.AIError):
        asyncio.run(ad.invoke(rt("sakoo"), "any", req()))
    result = asyncio.run(ad.test_connection(rt("sakoo")))
    assert result["status"] == "requires_documentation"
    assert "SAKOO" in ad.metadata().display_name
    # No guessed endpoint/auth in the schema either.
    assert all(f.key != "base_url" and f.type != "url"
               for f in ad.configuration_schema())


# ── Redirect refusal (SSRF) ─────────────────────────────────────────────

def test_transport_refuses_redirects(tmp_path):
    """base.http itself must raise on a 3xx — the SSRF walk-around is a
    permitted host 302-ing to a metadata address."""
    import httpx
    from app.services.ai.adapters.base import BaseAdapter

    class RedirectTransport(httpx.AsyncBaseTransport):
        def handle_request(self, request):
            return httpx.Response(302, headers={
                "Location": "http://169.254.169.254/latest/meta-data"})

    ad = adapter_for("openai")

    class ClientStub:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            class Inner:
                async def request(self, method, url, **kw):
                    return RedirectTransport().handle_request(
                        httpx.Request(method, url))
            return Inner()

        async def __aexit__(self, *exc):
            return False

    import app.services.ai.adapters.base as base_mod
    orig = base_mod.httpx.AsyncClient
    base_mod.httpx.AsyncClient = ClientStub
    try:
        with pytest.raises(ai_errors.AIError) as e:
            asyncio.run(ad.http(rt("openai"), "GET", "https://api.openai.com/v1/models"))
        assert e.value.code == "invalid_response"
        assert "169.254.169.254" not in e.value.provider_detail or "redirect" in e.value.provider_detail
    finally:
        base_mod.httpx.AsyncClient = orig
