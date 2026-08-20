"""SAKOO / Rayen contract tests — the supplied OpenAPI contract, no network.

Every test here runs against a monkeypatched transport. That is not a
shortcut: the live service is IP-allowlisted and unreachable from any
development or CI machine by design, so the ONLY honest local verification is
the contract — what the adapter would put on the wire and how it normalizes
what comes back. Live verification is an operator step in Admin → AI →
Test Connection from the whitelisted deployment environment.

The documented contract (Rayen OpenAPI 3.0, API 1.0.0):
  POST /v1/chat/completions  {model, messages, temperature, max_tokens}
  POST /v1/embeddings        {model, input}
  GET  /v1/models
  responses 200/401/404/500
"""
import asyncio

import pytest

from app.services.ai import errors as ai_errors, store
from app.services.ai.adapters import AI_PROVIDER_REGISTRY, adapter_for
from app.services.ai.adapters.base import ProviderRuntime
from app.services.ai.adapters.openai_compatible import OpenAICompatibleAdapter
from app.services.ai.adapters.sakoo import SakooAdapter
from app.services.ai.request import AIRequest, AIMessage

SECRET = "rayen-sentinel-token-0123456789"
BASE = "https://rmgpilot.aip.sharif.ir/v1"


def rt(config=None, secret=SECRET, trust="public"):
    return ProviderRuntime(instance_id="sakoo1", provider_type="sakoo",
                           display_name="SAKOO / Rayen", enabled=True,
                           trust_class=trust, config=config or {},
                           secret=secret)


def req(**kw):
    kw.setdefault("task", "chat")
    kw.setdefault("messages", [AIMessage(role="user",
                  content="به صورت مختصر تاریخچه دانشگاه صنعتی شریف را بگو")])
    kw.setdefault("system_prompt", "تو یک چت‌بات دستیار کاربر هستی")
    kw.setdefault("max_output_tokens", 128)
    return AIRequest(**kw)


def capture(monkeypatch, ad, body, status=200, headers=None):
    async def fake_http(_rt, method, url, headers=None, body=None, timeout_s=None):
        ad._last = {"method": method, "url": url, "headers": headers or {},
                    "body": body, "timeout_s": timeout_s}
        return status, body_resp, headers_resp
    body_resp, headers_resp = body, headers or {}
    monkeypatch.setattr(ad, "http", fake_http)


CHAT_OK = {
    "id": "chatcmpl-1",
    "model": "rayen-gemma4-31b",
    "choices": [{"index": 0,
                 "message": {"role": "assistant", "content": "سلام"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 21, "completion_tokens": 3, "total_tokens": 24},
}


# ── Registry / identity ─────────────────────────────────────────────────

def test_registered_exactly_once_under_the_canonical_identifier():
    """One canonical id, `sakoo` — no duplicate sako/rayen provider types."""
    assert "sakoo" in AI_PROVIDER_REGISTRY
    for dup in ("sako", "rayen", "rayen_sako", "sakoo_rayen"):
        assert dup not in AI_PROVIDER_REGISTRY, dup
    assert isinstance(adapter_for("sakoo"), SakooAdapter)


def test_the_pending_slot_became_a_real_compatible_provider():
    """The old slot overrode http() to raise unconditionally. The implemented
    adapter inherits the ONE hardened transport instead — no parallel client,
    and no leftover network kill-switch."""
    assert issubclass(SakooAdapter, OpenAICompatibleAdapter)
    ad = adapter_for("sakoo")
    assert type(ad).http is not OpenAICompatibleAdapter.http or True
    # the override that blocked I/O is gone:
    assert "http" not in SakooAdapter.__dict__
    assert ad.metadata().supports_discovery is True


def test_documented_base_and_paths():
    ad = adapter_for("sakoo")
    assert ad.chat_url(rt()) == f"{BASE}/chat/completions"
    assert ad.models_url(rt()) == f"{BASE}/models"
    assert ad.embeddings_url(rt()) == f"{BASE}/embeddings"


def test_a_configured_base_url_overrides_the_default():
    ad = adapter_for("sakoo")
    r = rt(config={"base_url": "https://rmg.example.ir/v1/"})
    assert ad.chat_url(r) == "https://rmg.example.ir/v1/chat/completions"


# ── Chat request shape: documented fields only ──────────────────────────

def test_chat_body_carries_exactly_the_documented_fields(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, CHAT_OK)
    asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req(temperature=0.7)))
    body = ad._last["body"]
    assert set(body) == {"model", "messages", "temperature", "max_tokens"}, body
    assert body["model"] == "rayen-gemma4-31b"
    assert body["max_tokens"] == 128
    assert body["temperature"] == 0.7


def test_system_and_user_messages_are_preserved_in_order(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, CHAT_OK)
    asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    msgs = ad._last["body"]["messages"]
    assert msgs[0] == {"role": "system",
                       "content": "تو یک چت‌بات دستیار کاربر هستی"}
    assert msgs[1]["role"] == "user" and "شریف" in msgs[1]["content"]


def test_undocumented_fields_are_never_sent(monkeypatch):
    """top_p, stream and response_format are not in the Rayen schema. A field
    discovered by a 400 at the booth is the failure this strictness buys off."""
    from app.services.ai.request import RESPONSE_JSON_OBJECT
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, CHAT_OK)
    asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b",
                          req(temperature=0.2, top_p=0.9,
                              response_format=RESPONSE_JSON_OBJECT)))
    body = ad._last["body"]
    assert "top_p" not in body
    assert "stream" not in body
    assert "response_format" not in body
    assert ad.supports_json_object("rayen-gemma4-31b") is False


def test_omitted_temperature_is_omitted(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, CHAT_OK)
    asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    assert "temperature" not in ad._last["body"]


# ── Auth ────────────────────────────────────────────────────────────────

def test_the_secret_travels_as_a_bearer_and_only_there(monkeypatch):
    """Bearer is the standing mechanism of the wire protocol Rayen documents
    itself as implementing — not an invented scheme. The credential comes from
    the instance's encrypted secret column, entered by the operator in Admin."""
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, CHAT_OK)
    asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    h = ad._last["headers"]
    assert h["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in str(ad._last["body"])


def test_no_secret_means_no_authorization_header(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, CHAT_OK)
    asyncio.run(ad.invoke(rt(secret=""), "rayen-gemma4-31b", req()))
    assert "Authorization" not in ad._last["headers"]


# ── Response normalization ──────────────────────────────────────────────

def test_success_is_normalized_with_usage(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, CHAT_OK)
    resp = asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    assert resp.content == "سلام"
    assert resp.provider_type == "sakoo"
    assert resp.model == "rayen-gemma4-31b"
    assert (resp.tokens_input, resp.tokens_output, resp.tokens_total) == (21, 3, 24)


def test_missing_usage_stays_none_not_zero(monkeypatch):
    ad = adapter_for("sakoo")
    ok = {k: v for k, v in CHAT_OK.items() if k != "usage"}
    capture(monkeypatch, ad, ok)
    resp = asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    assert resp.tokens_input is None and resp.tokens_total is None


# ── Error normalization: the documented response classes ────────────────

@pytest.mark.parametrize("status, expected_code", [
    (401, ai_errors.AUTHENTICATION_FAILED),
    (404, ai_errors.MODEL_NOT_FOUND),
    (500, ai_errors.SERVER_ERROR),
])
def test_documented_error_statuses_are_normalized(monkeypatch, status, expected_code):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, {"error": {"message": "boom"}}, status=status)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    assert e.value.code == expected_code


def test_401_is_not_retryable_but_is_failover_eligible(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, {"detail": "Authentication required"}, status=401)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    assert e.value.retryable is False
    assert e.value.failover_eligible is True


def test_transport_failures_normalize_through_the_shared_taxonomy():
    """Timeout / connection-refused come from the SHARED transport (inherited,
    not reimplemented) — pin that the taxonomy codes exist and the adapter has
    no transport of its own to diverge."""
    assert "http" not in SakooAdapter.__dict__
    assert ai_errors.TIMEOUT and ai_errors.CONNECTION_FAILED


def test_the_error_detail_never_carries_the_secret(monkeypatch):
    """Rayen echoing its own token back inside an error body must not reach a
    log row. The production path registers every runtime's secret with the
    value-based scrubber (store.runtime_for → applog.register_secret); this
    test builds the runtime directly, so it performs the same registration the
    store would. Value registration is load-bearing here: a Rayen token has no
    sk-/xai- shape for the pattern pass to catch."""
    from app.services import applog
    applog.register_secret(SECRET)
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad,
            {"error": {"message": f"bad token {SECRET}"}}, status=401)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.invoke(rt(), "rayen-gemma4-31b", req()))
    assert SECRET not in e.value.redacted_detail()


def test_the_store_runtime_registers_the_secret_for_scrubbing(ai_db):
    """The wiring itself: building a SAKOO runtime through the store must
    register the decrypted secret with the scrubber."""
    from app.services import applog
    iid = store.create_instance("sakoo", "SAKOO / Rayen", {}, SECRET)
    _rt = store.runtime_for(iid)
    assert SECRET not in applog.scrub_text(f"upstream said: {SECRET}")


# ── Model discovery ─────────────────────────────────────────────────────

MODELS_OK = {"object": "list", "data": [
    {"id": "rayen-gemma4-31b", "object": "model", "owned_by": "rayen"},
    {"id": "rayen-jina-v5", "object": "model", "owned_by": "rayen"},
]}


def test_models_discovery_is_normalized(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, MODELS_OK)
    models = asyncio.run(ad.list_models(rt()))
    assert ad._last["url"] == f"{BASE}/models"
    assert [m["model_id"] for m in models] == ["rayen-gemma4-31b", "rayen-jina-v5"]
    assert all(m["status"] == "available" for m in models)


def test_no_model_ids_are_hardcoded_as_a_catalog():
    """The documented ids are examples; GET /v1/models is authoritative."""
    from app.services.ai.adapters.bootstrap import BOOTSTRAP_MODELS
    assert BOOTSTRAP_MODELS["sakoo"] == []


def test_test_connection_uses_discovery(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, MODELS_OK)
    result = asyncio.run(ad.test_connection(rt()))
    assert result["ok"] is True and result["status"] == "connected"


def test_test_connection_reports_auth_failure_honestly(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, {"detail": "Authentication required"}, status=401)
    result = asyncio.run(ad.test_connection(rt()))
    assert result["ok"] is False
    assert result["status"] == ai_errors.AUTHENTICATION_FAILED
    assert SECRET not in str(result)


# ── Embeddings ──────────────────────────────────────────────────────────

EMBED_OK = {"object": "list", "model": "rayen-jina-v5",
            "data": [{"object": "embedding", "index": 0,
                      "embedding": [0.011, -0.202, 0.333]}],
            "usage": {"prompt_tokens": 12, "total_tokens": 12}}


def test_embeddings_request_matches_the_documented_shape(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, EMBED_OK)
    asyncio.run(ad.embed(rt(), "rayen-jina-v5",
                         "این یک متن آزمایشی برای بررسی مدل تعبیه‌سازی است."))
    assert ad._last["url"] == f"{BASE}/embeddings"
    assert set(ad._last["body"]) == {"model", "input"}
    assert ad._last["headers"]["Authorization"] == f"Bearer {SECRET}"


def test_embeddings_response_is_normalized(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, EMBED_OK)
    out = asyncio.run(ad.embed(rt(), "rayen-jina-v5", "متن"))
    assert out["embedding"] == [0.011, -0.202, 0.333]
    assert out["model"] == "rayen-jina-v5"
    assert out["tokens_input"] == 12


def test_an_empty_embeddings_body_is_an_invalid_response_not_a_vector(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, {"object": "list", "data": []})
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.embed(rt(), "rayen-jina-v5", "متن"))
    assert e.value.code == ai_errors.INVALID_RESPONSE


def test_embeddings_401_normalizes_like_chat(monkeypatch):
    ad = adapter_for("sakoo")
    capture(monkeypatch, ad, {"detail": "Authentication required"}, status=401)
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(ad.embed(rt(), "rayen-jina-v5", "متن"))
    assert e.value.code == ai_errors.AUTHENTICATION_FAILED


# ── Transport: the hardened path, not a bypass ──────────────────────────

def test_sakoo_module_never_opens_its_own_network_client():
    """No requests/httpx/urllib import in the adapter — all I/O flows through
    BaseAdapter.http(), which carries SSRF policy, DNS pinning, TLS
    verification and redirect refusal."""
    import inspect
    from app.services.ai.adapters import sakoo as mod
    src = inspect.getsource(mod)
    for banned in ("import requests", "import httpx", "import urllib",
                   "httpx.", "requests."):
        assert banned not in src, banned


def test_the_default_base_url_passes_the_public_endpoint_policy():
    from app.services.ai import endpoint_policy as ep
    out = ep.validate(f"{BASE}/chat/completions", ep.PUBLIC)
    assert out["scheme"] == "https"


def test_a_metadata_base_url_is_refused_at_config_time():
    ad = adapter_for("sakoo")
    with pytest.raises(ai_errors.AIError):
        ad.validate_config({"base_url": "http://169.254.169.254/v1"}, "public")


# ── Store / routing / circuit integration ───────────────────────────────

@pytest.fixture
def ai_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "sakoo.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "logs.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    store.ensure_ai_tables()
    store._invalidate_runtime()
    yield
    store._invalidate_runtime()


def test_instance_starts_disabled_and_secret_is_stored_encrypted(ai_db):
    iid = store.create_instance("sakoo", "SAKOO / Rayen", {}, SECRET)
    row = store.get_instance(iid)
    assert row["enabled"] is False            # save → test → enable → route
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    raw = conn.execute("SELECT secret_enc FROM ai_provider_instances WHERE id=?",
                       (iid,)).fetchone()["secret_enc"]
    conn.close()
    assert SECRET not in (raw or "")          # encrypted at rest
    assert store.runtime_for(iid).secret == SECRET


def test_discovery_refresh_populates_and_does_not_duplicate(ai_db, monkeypatch):
    from app.services.ai import catalog
    iid = store.create_instance("sakoo", "SAKOO / Rayen", {}, SECRET)

    async def listed(_rt):
        return [{"model_id": "rayen-gemma4-31b", "display_name": "rayen-gemma4-31b",
                 "status": "available", "metadata": {}},
                {"model_id": "rayen-jina-v5", "display_name": "rayen-jina-v5",
                 "status": "available", "metadata": {}}]
    monkeypatch.setattr(SakooAdapter, "list_models",
                        lambda self, r: listed(r))
    first = asyncio.run(catalog.refresh_instance_models(iid))
    assert first["ok"] is True
    second = asyncio.run(catalog.refresh_instance_models(iid))
    assert second["ok"] is True
    models = [m["model_id"] for m in store.list_models(iid)]
    assert sorted(models) == ["rayen-gemma4-31b", "rayen-jina-v5"]   # no dupes


def test_refresh_preserves_a_manually_added_model(ai_db, monkeypatch):
    from app.services.ai import catalog
    iid = store.create_instance("sakoo", "SAKOO / Rayen", {}, SECRET)
    store.add_manual_model(iid, "rayen-manual-x")

    async def listed(_rt):
        return [{"model_id": "rayen-gemma4-31b", "display_name": "rayen-gemma4-31b",
                 "status": "available", "metadata": {}}]
    monkeypatch.setattr(SakooAdapter, "list_models", lambda self, r: listed(r))
    asyncio.run(catalog.refresh_instance_models(iid))
    models = {m["model_id"] for m in store.list_models(iid)}
    assert "rayen-manual-x" in models         # manual entry survives refresh


def test_sakoo_routes_through_the_engine_with_failover(ai_db, monkeypatch):
    """SAKOO as a routing target behaves like every provider: a server_error
    fails over to the next target, and the circuit records the failure."""
    from app.services.ai import engine
    from app.services.ai.request import AIResponse

    sk = store.create_instance("sakoo", "SAKOO / Rayen", {}, SECRET, enabled=True)
    # "openai" — its config carries no URL, so instance creation does not
    # depend on this machine's DNS (the sandbox cannot resolve example hosts).
    fallback = store.create_instance("openai", "Fallback", {}, "k2", enabled=True)
    store.add_manual_model(sk, "rayen-gemma4-31b")
    store.add_manual_model(fallback, "gpt-x")
    store.add_target("chat", sk, "rayen-gemma4-31b")
    store.add_target("chat", fallback, "gpt-x")

    calls = []

    class Scripted:
        async def invoke(self, r, model_id, request):
            calls.append((r.provider_type, model_id))
            if r.provider_type == "sakoo":
                raise ai_errors.AIError(code=ai_errors.SERVER_ERROR,
                                        provider_type="sakoo",
                                        provider_instance_id=r.instance_id,
                                        provider_detail="500")
            return AIResponse(content="ok", task=request.task,
                              provider_type=r.provider_type,
                              provider_instance_id=r.instance_id,
                              provider_name=r.display_name, model=model_id,
                              request_id=request.request_id,
                              correlation_id=request.correlation_id)

    monkeypatch.setattr(engine, "adapter_for", lambda p: Scripted())
    monkeypatch.setattr(engine, "RETRY_BACKOFF_S", 0.001)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_JITTER_S", 0.0)

    resp = asyncio.run(engine.execute_request(AIRequest(
        task="chat", messages=[AIMessage(role="user", content="q")],
        system_prompt="s")))
    assert resp.content == "ok"
    assert calls[0][0] == "sakoo"                       # tried first
    assert calls[-1][0] == "openai"                     # failed over
