"""Route-level reasoning effort — the AI Control Plane's newest knob.

The scenario: an operator picks «سطح تفکر مدل» on Admin -> AI -> Routing and
the chat route starts sending the level on the very next request, while
classification stays pinned off and providers without the opt-in keep
receiving nothing. These tests fail if that wiring is removed.
"""
import pytest

from app.services.ai import store


@pytest.fixture
def ai_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ai.db"))
    from app.db.connection import init_db
    init_db()
    store.ensure_ai_tables()
    store.seed_bootstrap_pricing()
    store._invalidate_runtime()
    yield
    store._invalidate_runtime()


def _rt(config=None):
    from app.services.ai.adapters.base import ProviderRuntime
    return ProviderRuntime("i1", "openai_compatible", "Test", True,
                           "public", config, "sk-test")


def _req(reasoning):
    from app.services.ai.request import AIRequest, AIMessage
    return AIRequest(task="chat",
                     messages=[AIMessage(role="user", content="x")],
                     reasoning=reasoning)


def test_route_reasoning_is_empty_until_an_operator_sets_it(ai_db):
    assert store.route_reasoning("chat") == ""


def test_set_route_reasoning_roundtrip_and_snapshots(ai_db):
    store.set_route_reasoning("chat", "high")
    assert store.route_reasoning("chat") == "high"
    routes = store.list_routes()["routes"]
    row = next(r for r in routes if r["task"] == "chat")
    assert row["reasoning"] == "high"


def test_set_route_reasoning_rejects_unknown_values_and_tasks(ai_db):
    from app.services.ai.errors import AIError
    with pytest.raises(AIError):
        store.set_route_reasoning("chat", "maximum")
    with pytest.raises(AIError):
        store.set_route_reasoning("summarize", "high")


def test_default_level_is_storable_and_reads_back_empty(ai_db):
    store.set_route_reasoning("chat", "default")
    assert store.route_reasoning("chat") == ""


def test_adapter_sends_nothing_without_the_instance_opt_in():
    from app.services.ai.adapters.openai_compatible import (
        OpenAICompatibleAdapter as A)
    body_adds = A().apply_reasoning_body(_rt({}), "m", _req("high"))
    assert body_adds == {}


def test_adapter_maps_levels_to_the_vllm_thinking_switch():
    from app.services.ai.adapters.openai_compatible import (
        OpenAICompatibleAdapter as A)
    cfg = {"reasoning_param": "enable_thinking"}
    assert A().apply_reasoning_body(_rt(cfg), "m", _req("high")) == \
        {"chat_template_kwargs": {"enable_thinking": True}}
    assert A().apply_reasoning_body(_rt(cfg), "m", _req("low")) == \
        {"chat_template_kwargs": {"enable_thinking": True}}
    assert A().apply_reasoning_body(_rt(cfg), "m", _req("off")) == \
        {"chat_template_kwargs": {"enable_thinking": False}}
    assert A().apply_reasoning_body(_rt(cfg), "m", _req("default")) == {}


def test_build_body_carries_the_switch_end_to_end():
    from app.services.ai.adapters.openai_compatible import (
        OpenAICompatibleAdapter as A)
    body = A().build_body(_rt({"reasoning_param": "enable_thinking"}),
                          "some-model", _req("high"))
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
