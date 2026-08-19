"""Control-plane store, catalog, pricing and circuit tests (SQLite path)."""
import pytest

from app.services.ai import circuit, errors as ai_errors, pricing, store


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


def _mk(ai_db, name="Test", ptype="openai", enabled=True, secret="sk-xyz-123456"):
    return store.create_instance(ptype, name, {}, secret, enabled=enabled)


# ── Secrets ─────────────────────────────────────────────────────────────

def test_secret_never_leaks_from_any_read(ai_db):
    iid = _mk(ai_db)
    inst = store.get_instance(iid)
    assert inst["has_secret"] is True
    assert "secret_enc" not in inst
    for row in store.list_instances():
        assert "secret_enc" not in row
    # The DB row itself holds only ciphertext.
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    raw = conn.execute("SELECT secret_enc FROM ai_provider_instances WHERE id=?",
                       (iid,)).fetchone()
    conn.close()
    assert raw["secret_enc"].startswith("enc:")


def test_runtime_reveals_secret_only_internally(ai_db):
    iid = _mk(ai_db, secret="sk-live-abcdef123456")
    rt = store.runtime_for(iid)
    assert rt.secret == "sk-live-abcdef123456"
    # Runtime cache is bounded and invalidatable.
    store._invalidate_runtime(iid)
    assert store.runtime_for(iid).secret == "sk-live-abcdef123456"


def test_required_secret_enforced(ai_db):
    with pytest.raises(ai_errors.AIError):
        store.create_instance("openai", "NoKey", {}, "")


def test_unknown_provider_type_rejected(ai_db):
    with pytest.raises(ai_errors.AIError):
        store.create_instance("vendor-x", "X", {}, "k")


# ── Instances ───────────────────────────────────────────────────────────

def test_new_instance_saved_disabled_by_default(ai_db):
    iid = store.create_instance("openai", "Auto", {}, "sk-1")
    assert store.get_instance(iid)["enabled"] is False


def test_enable_disable_and_update(ai_db):
    iid = _mk(ai_db, enabled=False)
    store.set_enabled(iid, True, actor="admin")
    assert store.get_instance(iid)["enabled"] is True
    store.update_instance(iid, display_name="Renamed", actor="admin")
    inst = store.get_instance(iid)
    assert inst["display_name"] == "Renamed"
    assert inst["enabled"] is True


def test_delete_blocked_while_referenced_by_route(ai_db):
    iid = _mk(ai_db)
    store.add_target("chat", iid, "gpt-4.1")
    with pytest.raises(ai_errors.AIError):
        store.delete_instance(iid)
    store.remove_target(store.list_routes()["targets"][0]["id"])
    store.delete_instance(iid)
    assert store.get_instance(iid) is None


def test_disable_preserves_history_and_config(ai_db):
    iid = _mk(ai_db)
    store.add_target("chat", iid, "gpt-4.1")
    store.set_enabled(iid, False)
    inst = store.get_instance(iid)
    assert inst["enabled"] is False
    assert isinstance(inst["config"], dict)      # config preserved, not wiped
    assert store.list_routes()["targets"]        # route untouched


# ── Bootstrap catalog ───────────────────────────────────────────────────

def test_bootstrap_models_seeded_per_instance(ai_db):
    iid = store.create_instance("zai", "Z", {"platform": "international"}, "k")
    ids = [m["model_id"] for m in store.list_models(iid)]
    assert "glm-5.3" in ids and "glm-4.7-flash" in ids
    src = {m["model_id"]: m["source"] for m in store.list_models(iid)}
    assert src["glm-5.3"] == "bootstrap"


def test_openai_compatible_gets_no_bootstrap_models(ai_db):
    iid = store.create_instance("openai_compatible", "GW",
                          {"base_url": "https://93.184.216.34/v1"}, "k")
    assert store.list_models(iid) == []


# ── Backend-neutral JSON reading ────────────────────────────────────────
# The suite runs on SQLite, where `config` is TEXT and `json.loads(str)`
# always works. Production is PostgreSQL, where psycopg hands back JSONB
# ALREADY PARSED as a dict. `json.loads(dict)` raises TypeError, and the
# original `except -> {}` swallowed it — so every provider's configuration
# (base_url, region, workspace) silently became empty in production while
# every test stayed green.
#
# That bug really shipped. These tests are cheap, need no PostgreSQL, and
# make it permanently impossible to reintroduce.

def test_a_preparsed_jsonb_dict_is_returned_unchanged():
    """psycopg returns JSONB as a dict — it must pass straight through."""
    assert store._load_json({"base_url": "https://x/v1", "region": "ap"}) == \
        {"base_url": "https://x/v1", "region": "ap"}


def test_a_preparsed_jsonb_list_is_returned_unchanged():
    assert store._load_json(["a", "b"]) == ["a", "b"]


def test_a_json_text_column_is_still_parsed():
    """SQLite's TEXT path must keep working — this is not an either/or."""
    assert store._load_json('{"base_url": "https://x/v1"}') == \
        {"base_url": "https://x/v1"}


@pytest.mark.parametrize("empty", [None, ""])
def test_an_absent_config_reads_as_an_empty_mapping(empty):
    assert store._load_json(empty) == {}


# ── Discovery merge ─────────────────────────────────────────────────────

def test_discovery_adds_updates_and_preserves_vanished(ai_db):
    iid = _mk(ai_db)
    first = [{"model_id": "m1", "display_name": "M1"},
             {"model_id": "m2", "display_name": "M2"}]
    counts = store.apply_discovery(iid, first)
    assert counts == {"added": 2, "updated": 0, "unavailable": 0,
                      "preserved_manual": 0}
    second = [{"model_id": "m1", "display_name": "M1 renamed"},
              {"model_id": "m3", "display_name": "M3"}]
    counts = store.apply_discovery(iid, second)
    assert counts == {"added": 1, "updated": 1, "unavailable": 1,
                      "preserved_manual": 0}
    models = {m["model_id"]: m for m in store.list_models(iid)}
    assert models["m1"]["display_name"] == "M1 renamed"
    assert models["m1"]["source"] == "discovered"
    # m2 vanished from discovery: preserved, marked unavailable — NOT deleted
    assert models["m2"]["status"] == "unavailable"


def test_discovery_never_overwrites_a_manual_row_that_it_also_returns(ai_db):
    """A manual row is an operator assertion; discovery does not overrule it.

    The previous test covers a manual model that discovery does NOT return.
    This is the harder case, and the one that was actually broken: discovery
    returns the SAME id the operator entered by hand. `source` survived only
    because it was missing from the UPDATE's column list, so the row still
    *looked* manual while its status, display name, context window and every
    capability flag had been silently replaced.

    This is not hypothetical. The customer's live gateway carries `gpt-4.1`
    and `gpt-5-nano` as manual rows precisely because nobody knows whether
    that reseller still serves them. If the gateway lists either id, one
    click of Refresh used to convert a deliberate "we are not sure" into a
    confident claim the catalog cannot support.
    """
    iid = store.create_instance("openai_compatible", "GW",
                                {"base_url": "https://93.184.216.34/v1"}, "k")
    store.add_manual_model(iid, "gpt-4.1")
    before = {m["model_id"]: m for m in store.list_models(iid)}["gpt-4.1"]

    counts = store.apply_discovery(iid, [
        {"model_id": "gpt-4.1", "display_name": "UPGRADED BY DISCOVERY",
         "status": "available", "context_window": 999999,
         "supports_vision": True},
    ])

    assert counts["preserved_manual"] == 1
    assert counts["updated"] == 0          # it must not be counted as updated

    after = {m["model_id"]: m for m in store.list_models(iid)}["gpt-4.1"]
    assert after["source"] == "manual"
    assert after["status"] == "manual", "discovery flipped a manual row to available"
    assert after["display_name"] == before["display_name"], "display name overwritten"
    assert after["context_window"] == before["context_window"], "context overwritten"


def test_vanished_manual_and_bootstrap_rows_are_never_downgraded(ai_db):
    iid = _mk(ai_db)   # openai instance → bootstrap models exist
    store.add_manual_model(iid, "my-tuned-model")
    # Discovery returns a list that lacks every known model:
    store.apply_discovery(iid, [{"model_id": "brand-new"}])
    models = {m["model_id"]: m for m in store.list_models(iid)}
    assert models["my-tuned-model"]["status"] == "manual"
    assert models["gpt-5.6-terra"]["status"] == "available"      # untouched


def test_provider_model_mismatch_is_possible_and_visible(ai_db):
    """Two instances of the same type may serve different catalogs."""
    a = store.create_instance("openai", "A", {}, "k1")
    b = store.create_instance("openai", "B", {}, "k2")
    store.add_manual_model(b, "private-gateway-model")
    assert "private-gateway-model" not in [m["model_id"] for m in store.list_models(a)]


# ── Routes ──────────────────────────────────────────────────────────────

def test_targets_ordered_by_priority(ai_db):
    a, b = _mk(ai_db, name="A"), _mk(ai_db, name="B")
    store.add_target("chat", a, "m-a")
    store.add_target("chat", b, "m-b")
    order = [t["model_id"] for t in store.ordered_targets("chat")]
    assert order == ["m-a", "m-b"]


def test_reorder_is_atomic_and_complete(ai_db):
    a, b, c = (_mk(ai_db, name="A"), _mk(ai_db, name="B"), _mk(ai_db, name="C"))
    for inst, m in ((a, "m1"), (b, "m2"), (c, "m3")):
        store.add_target("chat", inst, m)
    targets = store.list_routes()["targets"]
    ids = [t["id"] for t in targets]
    # reverse the order
    store.reorder_targets("chat", list(reversed(ids)))
    order = [t["model_id"] for t in store.ordered_targets("chat")]
    assert order == ["m3", "m2", "m1"]
    # priorities are gap-free 1..n
    priorities = sorted(t["priority"] for t in store.ordered_targets("chat"))
    assert priorities == [1, 2, 3]


def test_reorder_rejects_partial_lists(ai_db):
    a, b = _mk(ai_db, name="A"), _mk(ai_db, name="B")
    store.add_target("chat", a, "m1")
    store.add_target("chat", b, "m2")
    ids = [t["id"] for t in store.list_routes()["targets"]]
    with pytest.raises(ai_errors.AIError):
        store.reorder_targets("chat", ids[:1])
    # unchanged after the rejection
    assert [t["model_id"] for t in store.ordered_targets("chat")] == ["m1", "m2"]


def test_remove_target_closes_the_gap(ai_db):
    a, b, c = (_mk(ai_db, name="A"), _mk(ai_db, name="B"), _mk(ai_db, name="C"))
    for inst, m in ((a, "m1"), (b, "m2"), (c, "m3")):
        store.add_target("chat", inst, m)
    targets = {t["model_id"]: t for t in store.list_routes()["targets"]}
    store.remove_target(targets["m2"]["id"])
    order = [(t["priority"], t["model_id"]) for t in store.ordered_targets("chat")]
    assert order == [(1, "m1"), (2, "m3")]


def test_unknown_task_rejected(ai_db):
    iid = _mk(ai_db)
    with pytest.raises(ai_errors.AIError):
        store.add_target("translate", iid, "m")


# ── Pricing ─────────────────────────────────────────────────────────────

def test_pricing_lookup_uses_latest_effective_row(ai_db):
    store.upsert_pricing("openai", "gpt-4.1", "USD", 99.0, None, 999.0, "test")
    # The new row is effective NOW, so it wins over the bootstrap row.
    row = store.lookup_pricing("openai", "gpt-4.1")
    assert float(row["input_per_million"]) == 99.0


def test_unknown_pricing_is_none_never_guessed(ai_db):
    assert store.lookup_pricing("openai", "mystery-model") is None
    assert pricing.estimate("openai", "mystery-model", 1000, 100) == (None, "")


def test_estimate_uses_cached_rate_when_known(ai_db):
    cost, currency = pricing.estimate("openai", "gpt-4.1", 1_000_000, 1_000_000,
                                      cached_tokens=500_000)
    # in: 500k*2 + 500k*0.5 = 1.25M·$ → $1.25 ; out: 1M*8 = $8
    assert round(cost, 6) == 9.25
    assert currency == "USD"


def test_historical_cost_is_stored_on_usage_row(ai_db):
    """A later price change must not rewrite what was already recorded."""
    store.record_usage({"task": "chat", "status": "success", "provider_type": "openai",
                        "model": "gpt-4.1", "tokens_in": 1000, "tokens_out": 100,
                        "cost": 0.0031, "currency": "USD",
                        "pricing_effective_from": "2026-08-18T00:00:00+00:00"})
    store.upsert_pricing("openai", "gpt-4.1", "USD", 50.0, None, 200.0, "price change")
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    row = conn.execute("SELECT cost, currency, pricing_effective_from FROM ai_usage_events"
                       " ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["cost"] == 0.0031                    # unchanged by the new price
    assert row["currency"] == "USD"


# ── Usage ───────────────────────────────────────────────────────────────

def test_usage_row_written_and_aggregatable(ai_db):
    for _ in range(3):
        store.record_usage({"task": "chat", "status": "success", "provider_type": "openai",
                            "provider_instance_id": "p1", "model": "gpt-4.1",
                            "tokens_in": 10, "tokens_out": 5, "tokens_total": 15,
                            "latency_ms": 100, "cost": 0.001})
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    row = conn.execute("SELECT COUNT(*) c, SUM(tokens_total) t, SUM(cost) k"
                       " FROM ai_usage_events WHERE status='success'").fetchone()
    conn.close()
    assert row["c"] == 3 and row["t"] == 45 and round(row["k"], 6) == 0.003


# ── Circuit breaker ─────────────────────────────────────────────────────

def _fail(code="server_error"):
    return ai_errors.AIError(code=code, provider_detail="x")


def _trip_open(iid):
    """One auth failure opens the circuit immediately (documented policy)."""
    circuit.record_failure(iid, ai_errors.AIError(code="authentication_failed"))
    assert circuit.snapshot(iid)[0]["state"] == "open"


def test_circuit_trips_after_threshold(ai_db):
    iid = _mk(ai_db)
    assert circuit.allows(iid) == (True, "")
    for i in range(circuit.DEFAULT_THRESHOLD - 1):
        assert circuit.record_failure(iid, _fail()) == "closed"
    assert circuit.record_failure(iid, _fail()) == "open"
    allowed, why = circuit.allows(iid)
    assert not allowed and "open until" in why


def test_auth_failure_trips_immediately_with_long_cooldown(ai_db):
    iid = _mk(ai_db)
    state = circuit.record_failure(iid, ai_errors.AIError(code="authentication_failed"))
    assert state == "open"
    allowed, _ = circuit.allows(iid)
    assert not allowed


def test_non_failover_errors_never_trip(ai_db):
    iid = _mk(ai_db)
    for _ in range(circuit.DEFAULT_THRESHOLD * 3):
        circuit.record_failure(iid, ai_errors.AIError(code="invalid_request"))
    assert circuit.allows(iid)[0] is True


def test_cooldown_leads_to_single_half_open_probe(ai_db):
    iid = _mk(ai_db)
    _trip_open(iid)
    # Force the cooldown into the past.
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_circuit_state SET cooldown_until = ?", (past,))
    conn.commit()
    conn.close()
    first = circuit.allows(iid)
    second = circuit.allows(iid)
    assert first[0] is True                     # the probe winner
    assert second[0] is False                   # everyone else waits


def test_probe_success_closes_everywhere(ai_db):
    iid = _mk(ai_db)
    _trip_open(iid)
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_circuit_state SET cooldown_until = ?", (past,))
    conn.commit()
    conn.close()
    circuit.allows(iid)                          # win the probe
    circuit.record_success(iid)
    assert circuit.allows(iid) == (True, "")
    assert circuit.snapshot(iid)[0]["state"] == "closed"


def test_probe_failure_reopens(ai_db):
    iid = _mk(ai_db)
    _trip_open(iid)
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE ai_circuit_state SET cooldown_until = ?", (past,))
    conn.commit()
    conn.close()
    circuit.allows(iid)                          # win the probe
    assert circuit.record_failure(iid, _fail()) == "open"
    assert circuit.allows(iid)[0] is False


def test_manual_reset_forces_closed(ai_db):
    iid = _mk(ai_db)
    circuit.record_failure(iid, ai_errors.AIError(code="authentication_failed"))
    circuit.reset(iid)
    assert circuit.allows(iid) == (True, "")
    assert circuit.snapshot(iid)[0]["failure_count"] == 0
