"""Routing engine tests: priority, eligibility, retry, failover, loop
protection, kill switch, usage accounting.

Adapters are faked at the registry boundary (engine.adapter_for is
monkeypatched) — these tests verify the ENGINE's decisions, not any
provider's wire behaviour (that is test_ai_adapters.py's job).
"""
import asyncio

import pytest

from app.services.ai import errors as ai_errors, store
from app.services.ai.request import AIRequest, AIMessage


@pytest.fixture
def ai_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "engine.db"))
    from app.db.connection import init_db
    init_db()
    # Real log tables: the engine writes an applog row immediately BEFORE each
    # provider call, and test_no_db_connection_is_open_while_a_provider_call_is
    # _in_flight can only be honest if that write takes its normal path.
    from app.services import applog
    applog.ensure_tables()
    store.ensure_ai_tables()
    store.seed_bootstrap_pricing()
    store._invalidate_runtime()
    yield
    store._invalidate_runtime()


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    """Shrink the retry backoff for the whole module.

    It changes WAIT LENGTH only — never how many attempts happen, which
    target is chosen, or whether a failover fires. Without it the suite spends
    a minute asleep. `test_retry_backoff_grows_and_is_bounded` asserts the
    real formula separately.
    """
    from app.services.ai import engine
    monkeypatch.setattr(engine, "RETRY_BACKOFF_S", 0.001)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_JITTER_S", 0.0)


class FakeAdapter:
    """Scriptable adapter: per-model list of outcomes (AIError or response
    dict). Records every invoke call."""

    def __init__(self):
        self.script = {}          # model_id -> list of outcomes
        self.calls = []

    def add(self, model, *outcomes):
        self.script.setdefault(model, []).extend(outcomes)

    async def invoke(self, rt, model_id, req):
        # index 3+ are additive: existing assertions index 0..2 only.
        self.calls.append((rt.instance_id, model_id, req.reasoning, req.timeout_s,
                           req.request_id, req.correlation_id))
        queue = self.script.get(model_id)
        outcome = queue.pop(0) if queue else {"content": "default-ok"}
        if isinstance(outcome, Exception):
            outcome.provider_type = rt.provider_type
            outcome.provider_instance_id = rt.instance_id
            outcome.model = model_id
            raise outcome
        from app.services.ai.request import AIResponse
        return AIResponse(
            content=outcome.get("content", "ok"),
            task=req.task, provider_type=rt.provider_type,
            provider_instance_id=rt.instance_id, provider_name=rt.display_name,
            model=model_id,
            tokens_input=outcome.get("tokens_in", 10),
            tokens_output=outcome.get("tokens_out", 5),
            tokens_total=outcome.get("tokens_total", 15),
            request_id=req.request_id, correlation_id=req.correlation_id)


def install_fake(monkeypatch, fake):
    from app.services.ai import engine
    monkeypatch.setattr(engine, "adapter_for", lambda ptype: fake)


def err(code, detail="x"):
    return ai_errors.AIError(code=code, provider_detail=detail)


def req(task="chat"):
    return AIRequest(task=task, messages=[AIMessage(role="user", content="q")],
                     system_prompt="s")


def setup_route(ai_db, monkeypatch, providers):
    """providers: list of (name, model, enabled). Returns the fake adapter."""
    fake = FakeAdapter()
    install_fake(monkeypatch, fake)
    ids = []
    for name, model, enabled in providers:
        iid = store.create_instance("openai", name, {}, "sk-1", enabled=enabled)
        ids.append(iid)
        store.add_manual_model(iid, model)
        store.add_target("chat", iid, model)
    return fake, ids


def usage_rows():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM ai_usage_events ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_setting(key, value):
    from app.db.queries import set_setting
    set_setting(key, value)


# ── Priority & eligibility ──────────────────────────────────────────────

def test_priority_one_is_tried_first(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("Primary", "m-primary", True), ("Backup", "m-backup", True)])
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert resp.provider_name == "Primary"
    assert len(fake.calls) == 1
    # priority 1 answered → the secondary was never contacted at all
    assert [c[1] for c in fake.calls] == ["m-primary"]
    assert resp.route_priority == 1 and resp.failover_count == 0


def test_disabled_provider_is_skipped(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("Primary", "m1", False), ("Backup", "m2", True)])
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert resp.provider_name == "Backup"
    assert [c[1] for c in fake.calls] == ["m2"]
    # the disabled INSTANCE never saw a call, by id — not just by model name
    assert ids[0] not in [c[0] for c in fake.calls]


def test_disabled_target_is_skipped(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("Primary", "m1", True), ("Backup", "m2", True)])
    target = store.list_routes()["targets"][0]
    store.set_target_enabled(target["id"], False)
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m2"]


def test_open_circuit_skips_provider(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                           [("Primary", "m1", True), ("Backup", "m2", True)])
    from app.services.ai import circuit, engine
    circuit.record_failure(ids[0], err("authentication_failed"))   # instant open
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m2"]


def test_unconfigured_provider_is_skipped(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                           [("Primary", "m1", True), ("Backup", "m2", True)])
    # wipe the secret of the primary → not eligible
    store.update_instance(ids[0], secret="", actor="t")
    store._invalidate_runtime()
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m2"]


# ── Retry & failover semantics per error class ──────────────────────────

def test_timeout_is_retried_on_same_target_then_fails_over(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("Primary", "m1", True), ("Backup", "m2", True)])
    fake.add("m1", err("timeout"), err("timeout"))
    fake.add("m2")
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m1", "m1", "m2"]   # retry, then over
    assert resp.model == "m2"
    assert resp.failover_count == 1


def test_rate_limit_fails_over_without_retry_hammering(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("Primary", "m1", True), ("Backup", "m2", True)])
    # rate_limited IS retryable per taxonomy — but classification defaults
    # to a single attempt, so here we assert the chat default (2 attempts)
    # then failover.
    fake.add("m1", err("rate_limited"), err("rate_limited"))
    fake.add("m2")
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m1", "m1", "m2"]


def test_auth_failure_never_retries_same_provider(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("Primary", "m1", True), ("Backup", "m2", True)])
    fake.add("m1", err("authentication_failed"))
    fake.add("m2")
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    # exactly ONE call to m1 — no hammering a broken key
    assert [c[1] for c in fake.calls] == ["m1", "m2"]


def test_invalid_request_does_not_fail_over(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("Primary", "m1", True), ("Backup", "m2", True)])
    fake.add("m1", err("invalid_request"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    # our bug surfaces as itself — hidden behind neither provider cycling
    # nor a generic all_routes_failed
    assert e.value.code == "invalid_request"
    assert [c[1] for c in fake.calls] == ["m1"]


def test_content_rejected_does_not_fail_over(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("Primary", "m1", True), ("Backup", "m2", True)])
    fake.add("m1", err("content_rejected"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    assert e.value.code == "content_rejected"
    assert [c[1] for c in fake.calls] == ["m1"]


def test_context_limit_does_not_fail_over(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("Primary", "m1", True), ("Backup", "m2", True)])
    fake.add("m1", err("context_limit_exceeded"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    assert e.value.code == "context_limit_exceeded"
    assert [c[1] for c in fake.calls] == ["m1"]


def test_all_routes_failed_after_exhaustion(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("timeout"), err("timeout"))
    fake.add("m2", err("server_error"), err("server_error"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    assert e.value.code == "all_routes_failed"
    assert len(e.value.route_failures) == 4           # both targets, all attempts
    assert len(fake.calls) == 4


def test_loop_protection_never_revisits_a_target(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("timeout"), err("timeout"))
    fake.add("m2", err("timeout"), err("timeout"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError):
        asyncio.run(engine.execute_request(req()))
    # 2 attempts × 2 targets = 4 calls, never a fifth (A→B→A is impossible)
    assert len(fake.calls) == 4


# ── Task defaults ───────────────────────────────────────────────────────

def test_classification_defaults_reasoning_off(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch, [("P", "m1", True)])
    store.add_target("classify", ids[0], "m1")
    from app.services.ai import engine
    asyncio.run(engine.execute_request(req(task="classify")))
    assert fake.calls[0][2] == "off"


def test_classification_has_no_retries_by_default(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch, [("P", "m1", True)])
    store.add_target("classify", ids[0], "m1")
    fake.add("m1", err("timeout"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError):
        asyncio.run(engine.execute_request(req(task="classify")))
    assert len(fake.calls) == 1              # audit §3.4: classify never retried


def test_target_max_attempts_override(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch, [("P", "m1", True)])
    target = store.list_routes()["targets"][0]
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_route_targets SET max_attempts = 3 WHERE id = ?", (target["id"],))
    conn.commit()
    conn.close()
    fake.add("m1", err("timeout"), err("timeout"))
    from app.services.ai import engine
    asyncio.run(engine.execute_request(req()))
    assert len(fake.calls) == 3              # third attempt succeeds


# ── Kill switch ─────────────────────────────────────────────────────────

def test_kill_switch_blocks_external_ai(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch, [("P", "m1", True)])
    set_setting("openai_enabled", "false")
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    assert e.value.code == "provider_unavailable"
    assert fake.calls == []                  # zero provider calls


# ── Usage & cost accounting ─────────────────────────────────────────────

def test_usage_row_records_tokens_cost_and_failovers(ai_db, monkeypatch):
    # openai + gpt-4.1 has bootstrap pricing → a real cost figure
    fake, _ = setup_route(ai_db, monkeypatch,
                         [("A", "gpt-4.1", True), ("B", "gpt-4.1", True)])
    fake.add("gpt-4.1", err("timeout"))
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    rows = usage_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "success"
    assert row["attempts"] == 2              # one failed + one success
    assert row["failovers"] == 0             # retry, not failover
    assert row["tokens_total"] == 15
    assert row["cost"] is not None and row["cost"] > 0
    assert row["currency"] == "USD"
    assert row["pricing_effective_from"]     # history preserved


def test_usage_row_on_total_failure(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch, [("A", "m1", True)])
    fake.add("m1", err("timeout"), err("timeout"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError):
        asyncio.run(engine.execute_request(req()))
    rows = usage_rows()
    assert rows[-1]["status"] == "failed"
    assert rows[-1]["error_code"] == "all_routes_failed"


def test_cost_is_none_when_pricing_unknown(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch, [("A", "mystery", True)])
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert resp.cost is None
    assert usage_rows()[-1]["cost"] is None   # N/A, never guessed


# ── Correlation ─────────────────────────────────────────────────────────

def test_request_and_correlation_ids_flow_through(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch, [("A", "m1", True)])
    from app.services.ai import engine
    r = req()
    r.request_id, r.correlation_id = "req-1", "corr-1"
    resp = asyncio.run(engine.execute_request(r))
    assert resp.request_id == "req-1"
    assert usage_rows()[-1]["request_id"] == "req-1"
    assert usage_rows()[-1]["correlation_id"] == "corr-1"


# ── Concurrency ─────────────────────────────────────────────────────────

def test_many_simultaneous_requests(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch, [("A", "m1", True), ("B", "m1", True)])
    from app.services.ai import engine

    async def one(i):
        try:
            return await engine.execute_request(req())
        except ai_errors.AIError:
            return None

    async def main():
        return await asyncio.gather(*[one(i) for i in range(30)])
    results = asyncio.run(main())
    assert sum(1 for r in results if r is not None) == 30
    assert len(fake.calls) == 30


def test_concurrent_circuit_failures_trip_once(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch, [("A", "m1", True), ("B", "m2", True)])
    from app.services.ai import circuit, engine
    fake.add("m1", *[err("server_error")] * 40)
    fake.add("m2")

    async def one(_):
        try:
            return await engine.execute_request(req())
        except ai_errors.AIError:
            return None

    async def main():
        return await asyncio.gather(*[one(i) for i in range(15)])
    asyncio.run(main())
    snap = circuit.snapshot(ids[0])[0]
    assert snap["state"] in ("open", "half_open")     # tripped, coherently
    assert snap["failure_count"] <= 40                # no runaway counting


# ── Adversarial routing review (agent 3) ────────────────────────────────
# Each test below pins ONE locked contract from
# docs/engineering/ai-providers/02-wrapper-contract.md §5.


def test_classify_priority_is_independent_of_chat_priority(ai_db, monkeypatch):
    """The two routes are separate ordered lists, not one shared order."""
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    # classify deliberately prefers the OPPOSITE provider
    store.add_target("classify", ids[1], "m2")
    store.add_target("classify", ids[0], "m1")
    from app.services.ai import engine

    chat = asyncio.run(engine.execute_request(req(task="chat")))
    classify = asyncio.run(engine.execute_request(req(task="classify")))

    assert (chat.provider_instance_id, chat.model) == (ids[0], "m1")
    assert (classify.provider_instance_id, classify.model) == (ids[1], "m2")
    assert [c[1] for c in fake.calls] == ["m1", "m2"]


def test_quota_exceeded_does_not_retry_but_does_fail_over(ai_db, monkeypatch):
    """Out of credit: asking the same account again is pointless, asking a
    different provider is not."""
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("quota_exceeded"))
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m1", "m2"]      # ONE call to m1
    assert resp.provider_instance_id == ids[1]
    assert resp.failover_count == 1


def test_auth_failure_fails_over_and_the_answer_comes_from_the_backup(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("authentication_failed"))
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m1", "m2"]
    assert resp.provider_instance_id == ids[1]             # a real answer
    assert resp.failover_count == 1


def test_provider_unavailable_fails_over_to_a_healthy_provider(ai_db, monkeypatch):
    """5xx-class failures are provider health, so they DO fail over."""
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("provider_unavailable"), err("server_error"))
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m1", "m1", "m2"]
    assert resp.provider_instance_id == ids[1]


def test_structured_output_failure_does_not_fail_over(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                          [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("structured_output_failed"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    assert e.value.code == "structured_output_failed"
    assert [c[1] for c in fake.calls] == ["m1"]


def test_all_routes_failed_detail_names_every_target_and_its_error(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("timeout"), err("timeout"))
    fake.add("m2", err("rate_limited"), err("quota_exceeded"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    failures = e.value.route_failures
    assert e.value.code == "all_routes_failed"
    # every attempted instance is named, with the code it actually returned
    assert {f["provider_instance_id"] for f in failures} == set(ids)
    by_instance = {}
    for f in failures:
        by_instance.setdefault(f["provider_instance_id"], []).append(f["error_code"])
    assert by_instance[ids[0]] == ["timeout", "timeout"]
    assert by_instance[ids[1]] == ["rate_limited", "quota_exceeded"]
    assert all(f["model"] for f in failures)


def test_loop_protection_rejects_a_duplicated_target_in_one_request(ai_db, monkeypatch):
    """The real A→B→A risk: the ordered list contains the same target twice
    (duplicate route row / admin reorder). The attempted-set must swallow the
    second visit — without it this request would spend 4 attempts, not 2."""
    fake, ids = setup_route(ai_db, monkeypatch, [("A", "m1", True)])
    real = store.ordered_targets("chat")
    assert len(real) == 1
    monkeypatch.setattr(store, "ordered_targets", lambda task: [real[0], dict(real[0])])
    fake.add("m1", err("timeout"), err("timeout"), err("timeout"), err("timeout"))
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    assert len(fake.calls) == 2                    # target visited exactly once
    assert e.value.code == "all_routes_failed"


def test_retry_budget_is_the_sum_of_targets_never_the_product(ai_db, monkeypatch):
    """A retry storm is the failure mode where failover multiplies retries.
    3 targets × 2 attempts must be 6 calls, not 8, and never unbounded."""
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True), ("C", "m3", True)])
    for m in ("m1", "m2", "m3"):
        fake.add(m, *[err("timeout")] * 10)
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError):
        asyncio.run(engine.execute_request(req()))
    assert len(fake.calls) == 6
    assert [c[1] for c in fake.calls] == ["m1", "m1", "m2", "m2", "m3", "m3"]


def test_per_target_max_attempts_still_sums_and_does_not_multiply(ai_db, monkeypatch):
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    first = store.list_routes()["targets"][0]
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_route_targets SET max_attempts = 4 WHERE id = ?", (first["id"],))
    conn.commit()
    conn.close()
    for m in ("m1", "m2"):
        fake.add(m, *[err("timeout")] * 10)
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError):
        asyncio.run(engine.execute_request(req()))
    assert len(fake.calls) == 6                    # 4 + 2, not 4 × 2
    assert [c[1] for c in fake.calls] == ["m1"] * 4 + ["m2"] * 2


def test_retry_backoff_grows_and_is_bounded(ai_db, monkeypatch):
    """Retries wait, and the wait GROWS with the attempt number — a fixed
    zero wait would be a hot loop against a struggling provider."""
    fake, _ = setup_route(ai_db, monkeypatch, [("A", "m1", True)])
    first = store.list_routes()["targets"][0]
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_route_targets SET max_attempts = 3 WHERE id = ?", (first["id"],))
    conn.commit()
    conn.close()
    from app.services.ai import engine
    monkeypatch.setattr(engine, "RETRY_BACKOFF_S", 0.02)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_JITTER_S", 0.0)
    waits = []
    real_sleep = asyncio.sleep

    async def spy(d, *a, **kw):
        waits.append(d)
        return await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", spy)
    fake.add("m1", err("timeout"), err("timeout"), err("timeout"))
    with pytest.raises(ai_errors.AIError):
        asyncio.run(engine.execute_request(req()))
    assert len(fake.calls) == 3
    assert waits == [0.02, 0.04]                   # one wait per retry, growing


def test_target_timeout_override_does_not_leak_into_the_next_target(ai_db, monkeypatch):
    """A 5 s cap on the primary must not become a 5 s cap on the backup."""
    fake = FakeAdapter()
    install_fake(monkeypatch, fake)
    a = store.create_instance("openai", "A", {}, "sk-1", enabled=True)
    b = store.create_instance("openai", "B", {}, "sk-1", enabled=True)
    store.add_manual_model(a, "m1")
    store.add_manual_model(b, "m2")
    store.add_target("chat", a, "m1", timeout_s=5)
    store.add_target("chat", b, "m2")
    fake.add("m1", err("authentication_failed"))   # one attempt, then failover
    from app.services.ai import engine
    asyncio.run(engine.execute_request(req()))
    assert [c[1] for c in fake.calls] == ["m1", "m2"]
    assert fake.calls[0][3] == 5
    assert fake.calls[1][3] == engine.TASK_TIMEOUT_S["chat"]


def test_ids_survive_retry_failover_and_land_on_the_final_error(ai_db, monkeypatch):
    """Correlation must hold across every attempt AND be present on the
    exception the caller actually catches — otherwise a failed request cannot
    be traced back to the visitor who made it."""
    fake, _ = setup_route(ai_db, monkeypatch,
                          [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("timeout"), err("timeout"))
    fake.add("m2", err("server_error"), err("server_error"))
    from app.services.ai import engine
    r = req()
    r.request_id, r.correlation_id = "req-9", "corr-9"
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(r))
    assert len(fake.calls) == 4
    assert all(c[4] == "req-9" and c[5] == "corr-9" for c in fake.calls)
    assert e.value.request_id == "req-9"
    assert e.value.correlation_id == "corr-9"
    assert e.value.attempts == 4
    assert usage_rows()[-1]["request_id"] == "req-9"
    assert usage_rows()[-1]["correlation_id"] == "corr-9"


def test_ids_survive_onto_the_response_after_a_failover(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                          [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", err("authentication_failed"))
    from app.services.ai import engine
    r = req()
    r.request_id, r.correlation_id = "req-7", "corr-7"
    resp = asyncio.run(engine.execute_request(r))
    assert resp.request_id == "req-7" and resp.correlation_id == "corr-7"
    assert resp.failover_count == 1


def test_kill_switch_blocks_even_a_fully_healthy_route(ai_db, monkeypatch):
    """The switch must cut external traffic BEFORE routing, and must not be
    survivable by having a working provider."""
    fake, _ = setup_route(ai_db, monkeypatch,
                          [("A", "m1", True), ("B", "m2", True)])
    from app.services.ai import engine
    assert asyncio.run(engine.execute_request(req())).content == "default-ok"
    set_setting("openai_enabled", "false")
    before = len(fake.calls)
    for task in ("chat", "classify"):
        with pytest.raises(ai_errors.AIError) as e:
            asyncio.run(engine.execute_request(req(task=task)))
        assert e.value.code == "provider_unavailable"
    assert len(fake.calls) == before               # zero further provider calls


def test_retry_is_abandoned_when_the_failure_opens_the_circuit(ai_db, monkeypatch):
    """A failure that trips the breaker must cancel the remaining same-target
    attempts — retrying into an open circuit is the hammering the breaker
    exists to prevent."""
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    from app.services.ai import circuit, engine

    real_record = circuit.record_failure

    def trip_then_record(instance_id, error):
        state = real_record(instance_id, error)
        if instance_id == ids[0]:
            # simulate a concurrent worker tripping this breaker mid-request
            real_record(instance_id, err("authentication_failed"))
        return state

    fake.add("m1", err("timeout"), err("timeout"))
    monkeypatch.setattr(circuit, "record_failure", trip_then_record)
    resp = asyncio.run(engine.execute_request(req()))
    # ONE call to m1 — the second attempt was cancelled by the open circuit
    assert [c[1] for c in fake.calls] == ["m1", "m2"]
    assert resp.provider_instance_id == ids[1]


def test_no_db_connection_is_open_while_a_provider_call_is_in_flight(ai_db, monkeypatch):
    """Hard rule: no transaction may span an external provider call. Every
    engine-side DB touch (routes, runtime, circuit, pricing, usage, and the
    applog rows written immediately before `invoke`) must have opened AND
    closed before the provider is awaited."""
    import sqlite3
    fake, _ = setup_route(ai_db, monkeypatch,
                          [("A", "m1", True), ("B", "m2", True)])
    from app.services.ai import engine

    live = set()                      # strong refs: ids are never recycled
    real_connect = sqlite3.connect

    class Tracked(sqlite3.Connection):
        def close(self):
            live.discard(self)
            super().close()

    def tracked_connect(*a, **kw):
        kw["factory"] = Tracked
        conn = real_connect(*a, **kw)
        live.add(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    open_during_invoke = []
    original_invoke = fake.invoke

    async def watching_invoke(rt, model_id, r):
        open_during_invoke.append(len(live))
        return await original_invoke(rt, model_id, r)

    monkeypatch.setattr(fake, "invoke", watching_invoke)
    fake.add("m1", err("timeout"))     # forces retry + failover paths too
    asyncio.run(engine.execute_request(req()))
    assert len(open_during_invoke) >= 2
    assert all(n == 0 for n in open_during_invoke), (
        f"DB connections held across the provider call: {open_during_invoke}")


# ── Empty model output ──────────────────────────────────────────────────

def test_empty_chat_content_is_a_failure_and_fails_over(ai_db, monkeypatch):
    """A blank chat answer must never reach the visitor as a 200. Adapters
    coerce a null content to "", so the ENGINE has to catch it — this is the
    gpt-5-nano incident (whole budget spent on hidden reasoning, empty text)."""
    fake, ids = setup_route(ai_db, monkeypatch,
                            [("A", "m1", True), ("B", "m2", True)])
    fake.add("m1", {"content": "   "}, {"content": ""})
    fake.add("m2", {"content": "یک پاسخ واقعی"})
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req()))
    # retried on m1 (invalid_response is retryable), then failed over
    assert [c[1] for c in fake.calls] == ["m1", "m1", "m2"]
    assert resp.content == "یک پاسخ واقعی"
    assert resp.failover_count == 1


def test_every_provider_returning_empty_chat_content_raises(ai_db, monkeypatch):
    fake, _ = setup_route(ai_db, monkeypatch,
                          [("A", "m1", True), ("B", "m2", True)])
    for m in ("m1", "m2"):
        fake.add(m, *[{"content": ""}] * 4)
    from app.services.ai import engine
    with pytest.raises(ai_errors.AIError) as e:
        asyncio.run(engine.execute_request(req()))
    assert e.value.code == "all_routes_failed"
    assert {f["error_code"] for f in e.value.route_failures} == {"invalid_response"}
    assert usage_rows()[-1]["status"] == "failed"


def test_empty_classify_content_is_still_a_success(ai_db, monkeypatch):
    """An empty CLASSIFICATION is meaningful — app/services/openai.py maps it
    onto the out_of_domain branch, which is a legitimate success. Applying the
    chat rule here would kill the generated-answer path."""
    fake, ids = setup_route(ai_db, monkeypatch, [("A", "m1", True), ("B", "m2", True)])
    store.add_target("classify", ids[0], "m1")
    store.add_target("classify", ids[1], "m2")
    fake.add("m1", {"content": ""})
    from app.services.ai import engine
    resp = asyncio.run(engine.execute_request(req(task="classify")))
    assert resp.content == ""
    assert [c[1] for c in fake.calls] == ["m1"]      # no retry, no failover
    assert resp.failover_count == 0
    assert usage_rows()[-1]["status"] == "success"
