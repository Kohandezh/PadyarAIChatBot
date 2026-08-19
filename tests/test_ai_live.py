"""Opt-in LIVE provider tests. Skipped unless RUN_LIVE_AI_TESTS=1.

Mocked success is NOT live verification (phase rule). These tests exist so
an operator WITH real credentials can verify a provider in one command:

    RUN_LIVE_AI_TESTS=1 ZAI_API_KEY=... pytest tests/test_ai_live.py -k zai

Rules encoded here:
  * minimal token usage (model-list probes; generation probes capped at a
    few tokens on the cheapest documented model)
  * strict timeouts
  * credentials read from env ONLY, never printed
  * a missing credential SKIPS (distinguishable from a failure)
"""
import asyncio
import os

import pytest

from app.services.ai.adapters import adapter_for
from app.services.ai.adapters.base import ProviderRuntime

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_AI_TESTS", "") != "1",
    reason="live provider tests are opt-in (RUN_LIVE_AI_TESTS=1)")


def _rt(ptype, config, key):
    return ProviderRuntime(instance_id="live", provider_type=ptype,
                           display_name=ptype, enabled=True,
                           trust_class="public", config=config, secret=key)


def _require(key):
    v = os.getenv(key, "")
    if not v:
        pytest.skip(f"{key} not set — credential unavailable, not failed")


# ── Connection + discovery probes (zero tokens) ─────────────────────────

@pytest.mark.parametrize("ptype,key,config", [
    ("openai", "OPENAI_LIVE_API_KEY", {}),
    ("anthropic", "ANTHROPIC_LIVE_API_KEY", {}),
    ("gemini", "GEMINI_LIVE_API_KEY", {}),
    ("zai", "ZAI_LIVE_API_KEY", {"platform": "international"}),
    ("kimi", "KIMI_LIVE_API_KEY", {"platform": "international"}),
    ("deepseek", "DEEPSEEK_LIVE_API_KEY", {}),
    ("xai", "XAI_LIVE_API_KEY", {}),
    ("mistral", "MISTRAL_LIVE_API_KEY", {}),
])
def test_live_connection_and_models(ptype, key, config):
    _require(key)
    ad = adapter_for(ptype)
    rt = _rt(ptype, config, os.getenv(key))
    result = asyncio.run(ad.test_connection(rt))
    assert result["ok"], f"{ptype}: {result['status']}: {result['detail']}"
    if ad.metadata().supports_discovery:
        models = asyncio.run(ad.list_models(rt))
        assert models, f"{ptype}: discovery returned no models"


# ── Minimal generation probes (a few tokens at most) ────────────────────

LIVE_GENERATION = [
    ("openai", "OPENAI_LIVE_API_KEY", {}, "gpt-5.6-luna"),
    ("anthropic", "ANTHROPIC_LIVE_API_KEY", {}, "claude-haiku-4-5-20251001"),
    ("gemini", "GEMINI_LIVE_API_KEY", {}, "gemini-2.5-flash-lite"),
    ("zai", "ZAI_LIVE_API_KEY", {"platform": "international"}, "glm-4.7-flash"),
    ("deepseek", "DEEPSEEK_LIVE_API_KEY", {}, "deepseek-v4-flash"),
    ("mistral", "MISTRAL_LIVE_API_KEY", {}, "ministral-3b-2512"),
]


@pytest.mark.parametrize("ptype,key,config,model", LIVE_GENERATION)
def test_live_minimal_generation(ptype, key, config, model):
    _require(key)
    from app.services.ai.request import AIRequest, AIMessage
    ad = adapter_for(ptype)
    rt = _rt(ptype, config, os.getenv(key))
    resp = asyncio.run(ad.invoke(rt, model, AIRequest(
        task="test", messages=[AIMessage(role="user", content="ping")],
        system_prompt="Reply with the single word: pong",
        max_output_tokens=16, reasoning="off", timeout_s=30.0)))
    assert resp.content, f"{ptype}: empty content"
    assert resp.tokens_total is None or resp.tokens_total < 500
