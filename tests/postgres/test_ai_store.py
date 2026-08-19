"""The AI control plane against real PostgreSQL.

`app/services/ai/store.py` is where JSONB and BOOLEAN meet code that was
originally written for SQLite's "everything is TEXT or INTEGER" world. Every
assertion here is about a column TYPE the SQLite suite cannot exercise.
"""
import json

import pytest

from app.services.ai import store


def _instance(name="Main", secret="sk-test-value", enabled=False,
              config=None, ptype="openai"):
    return store.create_instance(ptype, name, config or {}, secret,
                                 enabled=enabled, actor="pgtest")


# ── Provider instances ──────────────────────────────────────────────────

def test_create_and_read_back_a_provider_instance(conn):
    iid = _instance("گیت‌وی اصلی")
    got = store.get_instance(iid)
    assert got is not None
    assert got["display_name"] == "گیت‌وی اصلی"
    assert got["provider_type"] == "openai"


def test_enabled_and_has_secret_are_real_booleans_in_the_column(conn):
    """Not `bool(row[...])` — the COLUMN must hold a boolean. A code path that
    wrote `int(enabled)` raises DatatypeMismatch on PostgreSQL, and one that
    read `row["enabled"] == 1` silently returned nothing."""
    iid = _instance("Flags", enabled=True)
    row = conn.execute("SELECT enabled, has_secret FROM ai_provider_instances"
                       " WHERE id = ?", (iid,)).fetchone()
    assert row["enabled"] is True
    assert row["has_secret"] is True

    store.set_enabled(iid, False, actor="pgtest")
    row = conn.execute("SELECT enabled FROM ai_provider_instances WHERE id = ?",
                       (iid,)).fetchone()
    assert row["enabled"] is False
    assert store.get_instance(iid)["enabled"] is False


def test_an_instance_created_without_a_secret_reports_has_secret_false(conn):
    iid = store.create_instance("openai_compatible", "NoKeyGw",
                                {"base_url": "https://93.184.216.34/v1"}, "",
                                actor="pgtest")
    assert store.get_instance(iid)["has_secret"] is False


def test_config_round_trips_through_jsonb_without_being_wiped(conn):
    """The production symptom was an instance whose base_url vanished on read:
    JSONB arrives already parsed and `json.loads(dict)` raised into a
    `except: return {}`."""
    cfg = {"base_url": "https://93.184.216.34/v1", "organization": "org-فارسی"}
    iid = store.create_instance("openai", "GW", cfg, "k", actor="pgtest")

    stored = conn.execute("SELECT config FROM ai_provider_instances WHERE id = ?",
                          (iid,)).fetchone()["config"]
    assert isinstance(stored, dict), "config must be JSONB, not a TEXT blob"

    got = store.get_instance(iid)["config"]
    assert got.get("base_url") == "https://93.184.216.34/v1"
    assert got.get("organization") == "org-فارسی"
    assert store.list_instances()[0]["config"].get("base_url")


def test_updating_the_config_replaces_the_jsonb_value(conn):
    iid = store.create_instance("openai_compatible", "GW2",
                                {"base_url": "https://93.184.216.34/v1"}, "k",
                                actor="pgtest")
    store.update_instance(iid, config={"base_url": "https://93.184.216.35/v1"},
                          actor="pgtest")
    assert store.get_instance(iid)["config"]["base_url"] == \
        "https://93.184.216.35/v1"


def test_updated_at_is_a_timestamptz_that_moves_forward(conn):
    iid = _instance("Times")
    first = conn.execute("SELECT created_at, updated_at FROM ai_provider_instances"
                         " WHERE id = ?", (iid,)).fetchone()
    assert first["created_at"].tzinfo is not None
    store.update_instance(iid, display_name="Times 2", actor="pgtest")
    second = conn.execute("SELECT updated_at FROM ai_provider_instances"
                          " WHERE id = ?", (iid,)).fetchone()
    assert second["updated_at"] >= first["updated_at"]


def test_delete_removes_the_instance_and_its_models(conn):
    iid = _instance("Doomed")
    store.delete_instance(iid, actor="pgtest")
    assert store.get_instance(iid) is None
    assert conn.execute("SELECT count(*) AS n FROM ai_provider_models"
                        " WHERE provider_instance_id = ?", (iid,)).fetchone()["n"] == 0


def test_a_duplicate_instance_id_raises_uniqueviolation(conn):
    """`_slugify` appends random hex so this cannot happen through the admin
    UI, but the constraint is the last line of defence and must be the
    PostgreSQL one, not a SQLite lookalike."""
    from psycopg import errors

    from app.db import dberrors

    conn.execute("INSERT INTO ai_provider_instances (id, provider_type,"
                 " display_name) VALUES (?,?,?)", ("fixed-id", "openai", "A"))
    conn.commit()
    with pytest.raises(errors.UniqueViolation) as caught:
        conn.execute("INSERT INTO ai_provider_instances (id, provider_type,"
                     " display_name) VALUES (?,?,?)", ("fixed-id", "openai", "B"))
    assert dberrors.is_unique_violation(caught.value)
    conn.rollback()


# ── Runtime resolution (secret handling) ────────────────────────────────

def test_runtime_for_decrypts_the_secret_stored_in_postgres(conn):
    sentinel = "sk-pg-harness-0123456789"
    iid = _instance("Secretive", secret=sentinel, enabled=True)
    store._invalidate_runtime()
    rt = store.runtime_for(iid)
    assert rt is not None
    # Never print a secret: identity against a value this test itself chose.
    assert rt.secret == sentinel
    assert len(rt.secret) == len(sentinel)


def test_the_secret_column_never_holds_plaintext(conn):
    sentinel = "sk-pg-harness-plaintext-check"
    iid = _instance("Encrypted", secret=sentinel)
    stored = conn.execute("SELECT secret_enc FROM ai_provider_instances"
                          " WHERE id = ?", (iid,)).fetchone()["secret_enc"]
    assert stored.startswith("enc:")
    assert sentinel not in stored


def test_the_public_column_list_never_selects_the_secret():
    assert "secret_enc" not in store._PUBLIC_COLS


# ── Model rows ──────────────────────────────────────────────────────────

def test_bootstrap_models_are_seeded_with_boolean_capability_columns(conn):
    iid = _instance("Models")
    rows = conn.execute(
        "SELECT supports_chat, supports_tools, supports_vision, context_window"
        " FROM ai_provider_models WHERE provider_instance_id = ?",
        (iid,)).fetchall()
    assert rows, "creating an instance should seed its bootstrap catalog"
    for r in rows:
        assert isinstance(r["supports_chat"], bool)
        assert isinstance(r["supports_tools"], bool)
        assert isinstance(r["supports_vision"], bool)
        assert r["context_window"] is None or isinstance(r["context_window"], int)


def test_a_manual_model_is_stored_and_listed(conn):
    iid = _instance("Manual")
    store.add_manual_model(iid, "custom-model-1", "مدل سفارشی")
    listed = [m for m in store.list_models(iid) if m["model_id"] == "custom-model-1"]
    assert len(listed) == 1
    assert listed[0]["source"] == "manual"


def test_the_same_model_id_twice_on_one_instance_is_a_unique_violation(conn):
    from psycopg import errors

    from app.db import dberrors

    iid = _instance("Dupe")
    conn.execute("INSERT INTO ai_provider_models (provider_instance_id, model_id)"
                 " VALUES (?,?)", (iid, "only-once"))
    conn.commit()
    with pytest.raises(errors.UniqueViolation) as caught:
        conn.execute("INSERT INTO ai_provider_models (provider_instance_id,"
                     " model_id) VALUES (?,?)", (iid, "only-once"))
    assert dberrors.is_unique_violation(caught.value)
    conn.rollback()


def test_model_metadata_jsonb_is_returned_parsed(conn):
    iid = _instance("Meta")
    conn.execute("INSERT INTO ai_provider_models (provider_instance_id, model_id,"
                 " metadata) VALUES (?,?,?)",
                 (iid, "meta-model", json.dumps({"aliases": ["a"], "fa": "شرح"})))
    conn.commit()
    rows = [m for m in store.list_models(iid) if m["model_id"] == "meta-model"]
    assert rows[0]["metadata"]["aliases"] == ["a"]
    assert rows[0]["metadata"]["fa"] == "شرح"


# ── Route targets ───────────────────────────────────────────────────────

def test_route_targets_are_ordered_by_priority(conn):
    a = _instance("A", enabled=True)
    b = _instance("B", enabled=True)
    store.add_target("chat", a, "model-a", actor="pgtest")
    store.add_target("chat", b, "model-b", actor="pgtest")
    ordered = store.ordered_targets("chat")
    assert [t["provider_instance_id"] for t in ordered] == [a, b]
    assert [t["priority"] for t in ordered] == [1, 2]


def test_ordered_targets_reads_enabled_flags_as_booleans(conn):
    """`ordered_targets` filters with `r.enabled = TRUE` — the portable form.
    `= 1` here returned an empty route list and every chat fell back."""
    a = _instance("A2", enabled=True)
    store.add_target("chat", a, "model-a", actor="pgtest")
    target = store.ordered_targets("chat")[0]
    assert target["target_enabled"] is True
    assert target["provider_enabled"] is True
    assert target["has_secret"] is True


def test_a_disabled_route_returns_no_targets(conn):
    a = _instance("A3", enabled=True)
    store.add_target("chat", a, "model-a", actor="pgtest")
    conn.execute("UPDATE ai_routes SET enabled = FALSE WHERE task = ?", ("chat",))
    conn.commit()
    assert store.ordered_targets("chat") == []


def test_toggling_a_target_writes_a_boolean(conn):
    a = _instance("A4", enabled=True)
    tid = store.add_target("chat", a, "model-a", actor="pgtest")
    store.set_target_enabled(tid, False, actor="pgtest")
    row = conn.execute("SELECT enabled FROM ai_route_targets WHERE id = ?",
                       (tid,)).fetchone()
    assert row["enabled"] is False
    assert store.ordered_targets("chat")[0]["target_enabled"] is False


def test_reordering_targets_respects_the_unique_priority_constraint(conn):
    """`UNIQUE (task, priority)` is non-deferrable, so the reorder has to use
    a two-phase offset. If it ever stops doing that, this raises."""
    a = _instance("R1", enabled=True)
    b = _instance("R2", enabled=True)
    c = _instance("R3", enabled=True)
    ids = [store.add_target("chat", i, "m", actor="pgtest") for i in (a, b, c)]
    store.reorder_targets("chat", [ids[2], ids[0], ids[1]], actor="pgtest")
    assert [t["id"] for t in store.ordered_targets("chat")] == \
        [ids[2], ids[0], ids[1]]


def test_removing_a_target_closes_the_priority_gap(conn):
    a = _instance("D1", enabled=True)
    b = _instance("D2", enabled=True)
    ids = [store.add_target("chat", i, "m", actor="pgtest") for i in (a, b)]
    store.remove_target(ids[0], actor="pgtest")
    remaining = store.ordered_targets("chat")
    assert [t["id"] for t in remaining] == [ids[1]]
    assert remaining[0]["priority"] == 1


def test_a_target_pointing_at_a_missing_instance_is_refused_by_the_fk(conn):
    from psycopg import errors

    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute("INSERT INTO ai_route_targets (task, provider_instance_id,"
                     " model_id, priority) VALUES (?,?,?,?)",
                     ("chat", "does-not-exist", "m", 1))
    conn.rollback()


def test_list_routes_returns_both_routes_and_targets(conn):
    a = _instance("L1", enabled=True)
    store.add_target("classify", a, "m", actor="pgtest")
    payload = store.list_routes()
    assert {r["task"] for r in payload["routes"]} >= {"chat", "classify"}
    assert payload["targets"][0]["enabled"] is True


# ── Usage rows ──────────────────────────────────────────────────────────

def test_a_usage_row_stores_numeric_cost_and_jsonb_metadata(conn):
    store.record_usage({
        "task": "chat", "status": "success", "provider_type": "openai",
        "provider_instance_id": "x", "model": "m", "attempts": 1,
        "failovers": 0, "tokens_in": 10, "tokens_out": 20, "tokens_total": 30,
        "latency_ms": 42, "cost": 0.000123, "currency": "USD",
        "metadata": {"note": "یادداشت"},
    })
    row = conn.execute("SELECT created_at, cost, metadata FROM ai_usage_events"
                       " ORDER BY id DESC LIMIT 1").fetchone()
    assert row["created_at"].tzinfo is not None
    assert float(row["cost"]) == pytest.approx(0.000123)
    if row["metadata"] is not None:
        assert isinstance(row["metadata"], dict)
