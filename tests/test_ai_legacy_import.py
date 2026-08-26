"""Legacy configuration migration + wrapper compatibility tests.

The import must be idempotent, non-destructive, and produce a working
single-provider control plane from the legacy settings. The wrapper tests
prove chat.py's audited behaviours (00-current-state-audit.md §3) survive
the re-pointing: classify's three-outcome contract, generate's raise-on-
total-failure, and the kill switch key.
"""
import asyncio

import pytest

from app.services.ai import store
from app.services.ai.errors import AIError


@pytest.fixture
def ai_env(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "legacy.db"))
    from app.db.connection import init_db
    init_db()
    store.ensure_ai_tables()
    store.seed_bootstrap_pricing()
    store._invalidate_runtime()
    from app.db.queries import set_setting
    set_setting("ai_api_base", "https://api.gapgpt.app/v1")
    set_setting("ai_api_key", "sk-legacy-key-123456")
    set_setting("ai_model_chat", "gpt-4.1")
    set_setting("ai_model_classify", "gpt-5-nano")
    set_setting("openai_enabled", "true")
    yield
    store._invalidate_runtime()


def test_import_creates_instance_and_routes(ai_env):
    from app.services.ai import legacy_import
    iid = legacy_import.run_import()
    assert iid
    inst = store.get_instance(iid)
    assert inst["provider_type"] == "openai_compatible"
    assert inst["enabled"] is True
    assert inst["has_secret"] is True
    assert inst["trust_class"] == "public"
    assert inst["config"]["base_url"] == "https://api.gapgpt.app/v1"
    # both routes exist with the legacy models
    targets = {(t["task"], t["model_id"]) for t in store.list_routes()["targets"]}
    assert ("chat", "gpt-4.1") in targets
    assert ("classify", "gpt-5-nano") in targets
    # the configured model ids are MANUAL catalog rows — never auto-replaced
    models = {m["model_id"]: m for m in store.list_models(iid)}
    assert models["gpt-4.1"]["source"] == "manual"
    assert models["gpt-5-nano"]["source"] == "manual"


def test_import_is_idempotent(ai_env):
    from app.services.ai import legacy_import
    first = legacy_import.run_import()
    second = legacy_import.run_import()
    assert second is None                          # nothing more to do
    assert len(store.list_instances()) == 1        # no duplicates


def test_import_preserves_legacy_settings(ai_env):
    """Rollback to the pre-control-plane runtime must stay possible, so every
    legacy key has to survive the import as a real settings ROW — not as a
    default that get_setting happens to hand back for a missing row."""
    from app.db.queries import get_setting
    from app.db.connection import get_db_connection
    from app.services.ai import legacy_import
    legacy_import.run_import()
    assert get_setting("ai_api_base") == "https://api.gapgpt.app/v1"
    assert get_setting("ai_api_key") == "sk-legacy-key-123456"
    assert get_setting("ai_model_chat") == "gpt-4.1"
    assert get_setting("ai_model_classify") == "gpt-5-nano"
    assert get_setting("openai_enabled") == "true"
    conn = get_db_connection()
    try:
        rows = {r["key"] for r in conn.execute(
            "SELECT key FROM settings WHERE key IN"
            " ('ai_api_base','ai_api_key','ai_model_chat','ai_model_classify',"
            "  'openai_enabled')").fetchall()}
    finally:
        conn.close()
    assert rows == {"ai_api_base", "ai_api_key", "ai_model_chat",
                    "ai_model_classify", "openai_enabled"}
    # ...and nothing was blanked.
    assert all(get_setting(k) for k in rows)


def test_a_partial_import_is_rolled_back_not_frozen(ai_env):
    """Each store call commits separately. A failure after create_instance
    used to leave an instance with no route targets — and the NEXT boot's
    "an operator built this by hand" guard would mark migration done and
    freeze that dead control plane forever. Found on real PostgreSQL.

    Restores `add_target` by hand rather than via monkeypatch: monkeypatch is
    function-scoped and shared with the ai_env fixture, so `undo()` here would
    also unwind the fixture's DB_PATH redirect onto the developer's real
    database mid-test."""
    from app.services.ai import legacy_import
    from app.db.queries import get_setting

    real_add_target = store.add_target
    calls = {"n": 0}

    def flaky_add_target(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:                       # the classify target
            raise RuntimeError("simulated DB fault mid-migration")
        return real_add_target(*a, **kw)

    store.add_target = flaky_add_target
    try:
        assert legacy_import.run_import() is None
        # No orphan instance, no orphan target, no marker: the next boot retries.
        assert store.list_instances() == []
        assert store.list_routes()["targets"] == []
        assert get_setting("ai_control_plane_migrated", "") != "1"
    finally:
        store.add_target = real_add_target

    iid = legacy_import.run_import()
    assert iid
    assert len(store.list_routes()["targets"]) == 2


def test_import_respects_disabled_state(ai_env):
    from app.db.queries import set_setting
    from app.services.ai import legacy_import
    set_setting("openai_enabled", "false")
    iid = legacy_import.run_import()
    assert store.get_instance(iid)["enabled"] is False


def test_import_without_config_marks_done_and_boots(ai_env, monkeypatch):
    """Nothing importable = fresh install: mark done, import nothing, and do
    not re-evaluate on every boot."""
    from app.db.queries import set_setting
    from app.services.ai import legacy_import
    set_setting("ai_api_base", "")
    set_setting("ai_api_key", "")
    # env fallbacks must ALSO be empty — env keeps a fresh install bootable,
    # so a workable env config IS importable by design
    monkeypatch.setattr("app.services.openai.OPENAI_API_BASE", "")
    monkeypatch.setattr("app.services.openai.OPENAI_API_KEY", "")
    assert legacy_import.run_import() is None
    assert legacy_import.run_import() is None


def test_import_private_endpoint_becomes_internal(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "legacy2.db"))
    from app.db.connection import init_db
    init_db()
    store.ensure_ai_tables()
    store.seed_bootstrap_pricing()
    from app.db.queries import set_setting
    set_setting("ai_api_base", "http://127.0.0.1:11434/v1")   # local Ollama
    set_setting("ai_api_key", "ollama")
    from app.services.ai import legacy_import
    iid = legacy_import.run_import()
    assert iid
    assert store.get_instance(iid)["trust_class"] == "internal"


# ── Wrapper behaviour compatibility (chat.py contracts) ─────────────────

@pytest.fixture
def wrapper_env(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "wrapper.db"))
    from app.db.connection import init_db
    init_db()
    store.ensure_ai_tables()
    store.seed_bootstrap_pricing()
    store._invalidate_runtime()
    from app.db.queries import set_setting
    set_setting("ai_api_base", "https://api.gapgpt.app/v1")
    set_setting("ai_api_key", "sk-legacy-key-123456")
    set_setting("ai_model_chat", "gpt-4.1")
    set_setting("ai_model_classify", "gpt-5-nano")
    from app.services.search import load_dataset_internal
    load_dataset_internal()               # populate the intent list
    from app.services.ai import legacy_import
    legacy_import.run_import()
    yield
    store._invalidate_runtime()


class OkCompatAdapter:
    """Stub compatible adapter: succeeds with fixed content."""

    async def invoke(self, rt, model_id, req):
        from app.services.ai.request import AIResponse
        return AIResponse(content="inotex-overview", task=req.task,
                          provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id,
                          provider_name=rt.display_name, model=model_id,
                          tokens_input=10, tokens_output=5, tokens_total=15,
                          request_id=req.request_id)


def _install(monkeypatch, adapter):
    from app.services.ai import engine
    monkeypatch.setattr(engine, "adapter_for", lambda ptype: adapter)


def test_classify_intent_contract_preserved(wrapper_env, monkeypatch):
    """(entry|None, tokens, cost); None-on-out_of_domain is a SUCCESS branch
    and total failure returns (None, 0, 0.0) WITHOUT raising."""
    _install(monkeypatch, OkCompatAdapter())
    from app.services.openai import classify_intent

    # matched id
    entry, tokens, cost = asyncio.run(classify_intent("درباره نمایشگاه"))
    assert entry and entry["id"] == "inotex-overview"
    assert tokens == 15
    assert cost >= 0

    # out_of_domain → (None, tokens, cost) — NOT an exception
    class OOD(OkCompatAdapter):
        async def invoke(self, rt, model_id, req):
            from app.services.ai.request import AIResponse
            return AIResponse(content="out_of_domain", task=req.task,
                              provider_type=rt.provider_type,
                              provider_instance_id=rt.instance_id,
                              model=model_id, tokens_input=1, tokens_output=1,
                              tokens_total=2)
    _install(monkeypatch, OOD())
    entry, tokens, cost = asyncio.run(classify_intent("anything"))
    assert entry is None and tokens == 2

    # total failure → (None, 0, 0.0), still no exception
    class Dead:
        async def invoke(self, rt, model_id, req):
            raise AIError(code="all_routes_failed", provider_detail="down")
    _install(monkeypatch, Dead())
    entry, tokens, cost = asyncio.run(classify_intent("anything"))
    assert (entry, tokens, cost) == (None, 0, 0.0)


def test_get_openai_response_raises_on_total_failure(wrapper_env, monkeypatch):
    """chat.py:167 depends on a RAISED exception to fall back to local match."""
    class Dead:
        async def invoke(self, rt, model_id, req):
            raise AIError(code="all_routes_failed", provider_detail="down")
    _install(monkeypatch, Dead())
    from app.services.openai import get_openai_response
    with pytest.raises(Exception):
        asyncio.run(get_openai_response("سلام", lang="fa"))


def test_get_openai_response_returns_content(wrapper_env, monkeypatch):
    _install(monkeypatch, OkCompatAdapter())
    from app.services.openai import get_openai_response
    content, tokens, cost = asyncio.run(get_openai_response("سلام"))
    assert content == "inotex-overview"
    assert tokens == 15


def test_no_vendor_sdk_imports_outside_the_ai_package():
    """The locked architectural boundary: nothing outside app/services/ai
    (and the STT path's own module) imports a vendor SDK. The audit found
    the boundary already collapsed into app/services/openai.py; after this
    phase even that file only uses the SDK for Whisper STT (imported inside
    the function, out of routing scope)."""
    import subprocess, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(
        ["grep", "-rln", "-E", "from openai import|import openai|from anthropic|import anthropic|import google.genai|from google",
         "app/", "--include=*.py"],
        capture_output=True, text=True, cwd=root).stdout.strip().splitlines()
    allowed = {"app/services/openai.py"}          # STT only (function-local import)
    assert set(out) <= allowed, f"vendor SDK leaked into: {set(out) - allowed}"


# ── Kill switch (audit §2) ──────────────────────────────────────────────

def test_kill_switch_is_one_key_shared_by_both_admin_pages(wrapper_env, monkeypatch):
    """The legacy Settings page and the new Routing page must not fight.
    Both write `openai_enabled` through the SAME endpoint, and the engine
    reads that one key — so there is no second, competing flag."""
    from app.db.queries import set_setting, get_setting
    from app.services.ai import engine
    from app.services.ai.wrapper import padyar_ai

    invoked = {"n": 0}

    class CountingAdapter(OkCompatAdapter):
        async def invoke(self, rt, model_id, req):
            invoked["n"] += 1
            return await OkCompatAdapter.invoke(self, rt, model_id, req)

    _install(monkeypatch, CountingAdapter())

    # OFF (what /admin/api/toggle_openai writes — used by BOTH pages)
    set_setting("openai_enabled", "false")
    assert engine._kill_switch_on() is True
    assert padyar_ai.external_ai_enabled() is False
    with pytest.raises(AIError) as ei:
        asyncio.run(padyar_ai.classify("x", system_prompt="y"))
    assert ei.value.code == "provider_unavailable"
    assert invoked["n"] == 0, "kill switch must stop the call BEFORE the provider"

    # Back ON
    set_setting("openai_enabled", "true")
    assert engine._kill_switch_on() is False
    assert asyncio.run(padyar_ai.classify("x", system_prompt="y")).content
    assert invoked["n"] == 1
    assert get_setting("openai_enabled") == "true"


def test_both_admin_pages_post_to_the_same_toggle_endpoint():
    """Source-level guard: if someone ever gives the routing page its own
    kill-switch key, these two files stop agreeing and this test fails."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    routing = open(os.path.join(root, "static/admin/js/ai_routing.js")).read()
    dashboard = open(os.path.join(root, "static/admin/js/dashboard.js")).read()
    assert "/toggle_openai" in routing and "/toggle_openai" in dashboard
    engine_src = open(os.path.join(root, "app/services/ai/engine.py")).read()
    assert 'get_setting("openai_enabled"' in engine_src


# ── Manual models are never silently upgraded ───────────────────────────

def test_manual_models_survive_a_catalog_refresh(ai_env):
    """gpt-4.1 / gpt-5-nano were imported as MANUAL on purpose: nobody knows
    whether the customer's gateway still serves them. A discovery refresh
    that does not list them must NOT delete them, downgrade them, or swap
    the route onto something the gateway advertises instead."""
    from app.services.ai import legacy_import
    iid = legacy_import.run_import()

    before = {(t["task"], t["model_id"]) for t in store.list_routes()["targets"]}
    store.apply_discovery(iid, [
        {"model_id": "gpt-4o-mini", "display_name": "GPT-4o mini"},
        {"model_id": "some-new-model"},
    ])
    models = {m["model_id"]: m for m in store.list_models(iid)}
    assert models["gpt-4.1"]["source"] == "manual"
    assert models["gpt-4.1"]["status"] == "manual"      # not 'unavailable'
    assert models["gpt-5-nano"]["source"] == "manual"
    assert models["gpt-5-nano"]["status"] == "manual"
    assert "gpt-4o-mini" in models                       # discovery still works
    # and the ROUTE still points at the legacy ids
    assert {(t["task"], t["model_id"]) for t in store.list_routes()["targets"]} == before


# ── Token / cost accounting (audit §4.1 — the wrong hardcoded rate) ─────

def test_tokens_and_cost_are_accounted_per_call_from_the_pricing_table(
        wrapper_env, monkeypatch):
    _install(monkeypatch, OkCompatAdapter())
    from app.services.openai import classify_intent
    from app.services.ai import pricing

    entry, tokens, cost = asyncio.run(classify_intent("درباره نمایشگاه"))
    assert tokens == 15                                   # 10 in + 5 out
    assert cost == (pricing.estimate("openai_compatible", "gpt-5-nano", 10, 5)[0]
                    or 0.0)
    # The OLD code charged 5/15 USD per 1M for EVERY model regardless of which
    # one ran (audit §4.1). The pricing table must not reproduce that number.
    legacy_wrong = 10 * 5.0 / 1_000_000 + 5 * 15.0 / 1_000_000
    assert cost != legacy_wrong
    assert 0 <= cost < 0.01                               # sane, not fiction

    # ...and the usage row carries the same figures.
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT task, status, tokens_in, tokens_out, tokens_total, cost,"
            " currency, model FROM ai_usage_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row["task"] == "classify" and row["status"] == "success"
    assert (row["tokens_in"], row["tokens_out"], row["tokens_total"]) == (10, 5, 15)
    assert row["model"] == "gpt-5-nano"
    assert float(row["cost"] or 0.0) == cost


def test_unknown_pricing_reports_none_not_a_guess(wrapper_env):
    from app.services.ai import pricing
    cost, currency = pricing.estimate("openai_compatible",
                                      "model-nobody-has-priced", 100, 100)
    assert cost is None and currency == ""


def test_the_migrated_instance_has_no_bootstrap_price_and_says_so(wrapper_env):
    """DOCUMENTS A REAL GAP, so it cannot change silently.

    Bootstrap pricing is keyed by provider_type. gpt-4.1 / gpt-5-nano are
    priced under provider_type `openai`; the legacy import creates the
    customer's gateway as `openai_compatible`, for which there are ZERO
    bootstrap rows. So after migration cost is honestly UNKNOWN (None → N/A),
    not zero and not the old hardcoded fiction. Verified identical on the
    production PostgreSQL database.

    That is the right default — a reselling proxy does not charge OpenAI list
    price — but it means the operator MUST enter their own rates on the
    pricing admin surface before any cost figure means anything."""
    from app.services.ai import pricing, store
    assert store.lookup_pricing("openai", "gpt-4.1") is not None
    assert store.lookup_pricing("openai_compatible", "gpt-4.1") is None
    assert pricing.estimate("openai_compatible", "gpt-4.1", 100, 100) == (None, "")

    # An operator-entered rate makes it real, with no code change.
    store.upsert_pricing("openai_compatible", "gpt-4.1", "USD", 2.0, 0.5, 8.0,
                         source="operator")
    cost, currency = pricing.estimate("openai_compatible", "gpt-4.1", 1000, 1000)
    assert currency == "USD"
    assert cost == round((1000 * 2.0 + 1000 * 8.0) / 1_000_000, 8)


def test_log_chat_still_receives_tokens_and_cost(wrapper_env, monkeypatch):
    """The accounting contract chat.py depends on: whatever classify/generate
    return is what lands in chat_logs.tokens / chat_logs.cost."""
    seen = {}
    from app.db import queries

    real_log_chat = queries.log_chat

    def spy(query, response, r_type, source, confidence, tokens=0, cost=0.0):
        seen.update({"source": source, "tokens": tokens, "cost": cost})
        return real_log_chat(query, response, r_type, source, confidence,
                             tokens, cost)

    monkeypatch.setattr("app.routers.chat.log_chat", spy)
    _install(monkeypatch, OkCompatAdapter())

    from app.routers.chat import _answer_from_entry
    from app.services.openai import classify_intent
    entry, tokens, cost = asyncio.run(classify_intent("درباره نمایشگاه"))
    _answer_from_entry(entry, 0.3, "openai_classified", "q", tokens, cost)
    assert seen == {"source": "openai_classified", "tokens": 15, "cost": cost}


# ── Correlation ids (audit §5) ──────────────────────────────────────────

def test_correlation_ids_flow_through_the_wrapper_and_the_usage_row(
        wrapper_env, monkeypatch):
    """The engine — not the adapter — guarantees the trace. An adapter that
    forgets to copy the ids must not be able to break it."""
    from app.services import applog
    from app.services.ai.wrapper import padyar_ai

    class ForgetfulAdapter:
        async def invoke(self, rt, model_id, req):
            from app.services.ai.request import AIResponse
            return AIResponse(content="ok", task=req.task, model=model_id,
                              provider_type=rt.provider_type,
                              provider_instance_id=rt.instance_id,
                              tokens_input=3, tokens_output=2, tokens_total=5)

    _install(monkeypatch, ForgetfulAdapter())
    corr = applog.new_id()
    applog.set_request_context(correlation_id=corr)
    resp = asyncio.run(padyar_ai.classify("q", system_prompt="s"))
    assert resp.correlation_id == corr
    assert resp.request_id

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT request_id, correlation_id FROM ai_usage_events"
                           " ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["correlation_id"] == corr
    assert row["request_id"] == resp.request_id


# ── STT is explicitly OUT of routing scope and must still work ──────────

def test_transcribe_still_uses_the_legacy_provider_config_not_the_wrapper(
        ai_env, monkeypatch):
    """Whisper stays on its own path (audit §6.6). It must read the legacy
    settings directly and never enter the routing engine."""
    from app.services.ai import legacy_import
    legacy_import.run_import()

    seen = {}

    class FakeTranscriptions:
        def create(self, model, file):
            seen["model"] = model
            seen["filename"] = file.name
            return type("R", (), {"text": "سلام دنیا"})()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeOpenAI:
        def __init__(self, base_url, api_key, max_retries, http_client):
            seen["base_url"] = base_url
            seen["api_key"] = api_key
            seen["max_retries"] = max_retries
            self.audio = FakeAudio()

        def close(self):
            seen["closed"] = True

    import openai as openai_sdk
    monkeypatch.setattr(openai_sdk, "OpenAI", FakeOpenAI)

    def explode(*a, **kw):
        raise AssertionError("STT must not go through the routing engine")

    from app.services.ai import engine
    monkeypatch.setattr(engine, "execute_request", explode)

    from app.services.openai import _transcribe_sync
    assert _transcribe_sync(b"\x00\x01audio", "note.webm") == "سلام دنیا"
    assert seen["base_url"] == "https://api.gapgpt.app/v1"
    assert seen["api_key"] == "sk-legacy-key-123456"
    assert seen["model"] == "whisper-1"          # ai_model_stt default
    assert seen["max_retries"] == 0              # transport hardening (audit §3.5)
    assert seen["closed"] is True


def test_kill_switch_does_not_reach_into_the_voice_path(ai_env):
    """`openai_enabled` gates the CHAT/CLASSIFY routing engine. Voice has its
    own `voice_enabled` toggle; conflating them would silently kill the mic."""
    from app.db.queries import set_setting, get_setting
    set_setting("openai_enabled", "false")
    assert get_setting("voice_enabled", "true") == "true"
    import inspect
    from app.services.openai import _transcribe_sync
    src = inspect.getsource(_transcribe_sync)
    assert "openai_enabled" not in src and "padyar_ai" not in src


# ── The RAG fallback ladder, end to end through chat.py ─────────────────

@pytest.fixture
def chat_client(tmp_path, monkeypatch):
    """A booted app: lifespan seeds the dataset, creates the AI tables and
    runs the legacy import — the exact production boot sequence."""
    from fastapi.testclient import TestClient
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    from app.auth import security
    security._chat_rate_limits.clear()
    from app.main import app
    with TestClient(app) as c:
        store._invalidate_runtime()
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "User-Agent": "Mozilla/5.0 (gate-h-test)",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()
    store._invalidate_runtime()


def _ask(client, message="هوا امروز چند درجه است زززز"):
    """The message must clear EVERY local tier — T0/T1 questions, hybrid
    retrieval, AND the trained intent classifier (p < INTENT_TRUST_THRESHOLD)
    — so the ladder tests exercise the AI branch they exist for.

    The previous filler («یک پرسش کاملا بی‌ربط زززز») did that before the
    2026-08-26 expansion dedup; with healthy queries reaching the classifier,
    it confidently maps that filler to inotex-targeted-visit at p=0.66 and the
    tests started asserting against local_intent instead of the ladder. This
    weather filler is out-of-domain by topic, not just by noise words — the
    golden set carries a sibling of it under `unsupported`."""
    return client.post("/chat", json={"message": message, "lang": "fa"})


def test_ladder_ai_classifier_match_is_served(chat_client, monkeypatch):
    _install(monkeypatch, OkCompatAdapter())          # answers "inotex-overview"
    r = _ask(chat_client)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "openai_classified"


def test_ladder_out_of_domain_still_produces_a_generated_answer(chat_client, monkeypatch):
    """THE regression this gate exists for: if the wrapper collapsed
    out_of_domain into a failure, this whole branch would be dead."""
    class OODThenChat:
        async def invoke(self, rt, model_id, req):
            from app.services.ai.request import AIResponse
            text = "out_of_domain" if req.task == "classify" else "یک پاسخ تولیدشده"
            return AIResponse(content=text, task=req.task, model=model_id,
                              provider_type=rt.provider_type,
                              provider_instance_id=rt.instance_id,
                              tokens_input=7, tokens_output=3, tokens_total=10)
    _install(monkeypatch, OODThenChat())
    r = _ask(chat_client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "openai"
    assert body["text"] == "یک پاسخ تولیدشده"

    # log_chat received the SUMMED tokens/cost of both calls.
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT source, tokens, cost FROM chat_logs"
                           " ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["source"] == "openai"
    assert row["tokens"] == 20                        # 10 classify + 10 chat
    assert row["cost"] >= 0


def test_ladder_ai_failure_with_no_strong_local_match_is_503(chat_client, monkeypatch):
    class Dead:
        async def invoke(self, rt, model_id, req):
            raise AIError(code="all_routes_failed", provider_detail="down")
    _install(monkeypatch, Dead())
    r = _ask(chat_client)
    assert r.status_code == 503


def test_ladder_ai_failure_falls_back_to_a_strong_local_match(chat_client, monkeypatch):
    """Below TRUSTED (0.70) but at/above LOCAL_FALLBACK (0.45): the AI is
    asked first, and when it dies the local match is served rather than 503."""
    class Dead:
        async def invoke(self, rt, model_id, req):
            raise AIError(code="all_routes_failed", provider_detail="down")
    _install(monkeypatch, Dead())
    entry = {"id": "inotex-overview", "title": "t", "text": "پاسخ محلی",
             "video_url": ""}
    from app.routers import chat as chat_router
    monkeypatch.setattr(chat_router, "find_best_match", lambda q: (entry, 0.55))
    monkeypatch.setattr(chat_router, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat_router, "classify_intent_local", lambda q: (None, 0.0))
    r = _ask(chat_client)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local"
    assert r.json()["text"] == "پاسخ محلی"


def test_ladder_kill_switch_skips_the_ai_branch_entirely(chat_client, monkeypatch):
    """openai_enabled=false must stop the external call at chat.py:145 — the
    adapter is never reached, and a strong local match still answers."""
    from app.db.queries import set_setting
    set_setting("openai_enabled", "false")
    reached = {"n": 0}

    class NeverCalled:
        async def invoke(self, rt, model_id, req):
            reached["n"] += 1
            raise AssertionError("external AI called while the kill switch is on")
    _install(monkeypatch, NeverCalled())
    entry = {"id": "inotex-overview", "title": "t", "text": "پاسخ محلی",
             "video_url": ""}
    from app.routers import chat as chat_router
    monkeypatch.setattr(chat_router, "find_best_match", lambda q: (entry, 0.55))
    monkeypatch.setattr(chat_router, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat_router, "classify_intent_local", lambda q: (None, 0.0))
    r = _ask(chat_client)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local"
    assert reached["n"] == 0
