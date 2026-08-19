"""Authenticated admin surfaces, rendered against real PostgreSQL.

Every page here 500'd at some point after cutover — the admin session lookup
called `fromisoformat` on a TIMESTAMPTZ, the provider list compared
`enabled = 1`, and the AI settings page read a JSONB config through
`json.loads`. Rendering them for real is the cheapest way to keep that class
of failure visible.
"""
import pytest

from app.services.ai import store


def _csrf(client):
    return client.get("/admin/csrf").json()["csrf_token"]


def _post(client, url, body=None):
    return client.post(url, json=body or {},
                       headers={"X-CSRF-Token": _csrf(client)})


# ── The session itself ──────────────────────────────────────────────────

def test_the_admin_session_is_accepted_from_a_timestamptz_expiry(client):
    """The session row's `expiry` is a real TIMESTAMPTZ here. If the auth path
    ever goes back to string comparison, every one of these turns into a 303."""
    res = client.get("/secure-panel-inotex", follow_redirects=False)
    assert res.status_code == 200


def test_an_expired_session_is_still_rejected(client, conn):
    import datetime

    conn.execute("UPDATE admin_sessions SET expiry = ? WHERE username = ?",
                 (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=2), "pgadmin"))
    conn.commit()
    res = client.get("/secure-panel-inotex", follow_redirects=False)
    assert res.status_code == 303


# ── Pages render ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,marker", [
    ("/secure-panel-inotex/settings/ai", "محتوای دستیار هوشمند"),
    ("/secure-panel-inotex/ai/routing", "مسیریابی هوش مصنوعی"),
    ("/secure-panel-inotex/ai/providers", "سرویس‌دهنده‌های هوش مصنوعی"),
    ("/secure-panel-inotex/ai/models", "مدل‌های هوش مصنوعی"),
    ("/secure-panel-inotex/ai/usage", "مصرف و هزینهٔ هوش مصنوعی"),
])
def test_admin_pages_return_200_on_postgresql(client, path, marker):
    res = client.get(path)
    assert res.status_code == 200, res.text[:400]
    assert marker in res.text


def test_the_settings_ai_page_renders_with_a_configured_provider(client):
    store.create_instance("openai", "Gateway",
                          {"base_url": "https://93.184.216.34/v1"},
                          "sk-page-render-0001", enabled=True, actor="pgtest")
    store._invalidate_runtime()
    res = client.get("/secure-panel-inotex/settings/ai")
    assert res.status_code == 200
    assert "sk-page-render-0001" not in res.text


def test_the_routing_page_renders_with_targets_present(client):
    iid = store.create_instance("openai", "Gateway",
                                {"base_url": "https://93.184.216.34/v1"},
                                "sk-routing-0002", enabled=True, actor="pgtest")
    store.add_target("chat", iid, "gpt-4.1", actor="pgtest")
    res = client.get("/secure-panel-inotex/ai/routing")
    assert res.status_code == 200
    assert "sk-routing-0002" not in res.text


# ── Settings -> AI API ──────────────────────────────────────────────────

def test_get_ai_connection_returns_200_and_a_stt_status_block(client):
    res = client.get("/admin/api/ai-connection")
    assert res.status_code == 200, res.text[:400]
    body = res.json()
    assert "stt" in body and "configured" in body["stt"]
    assert body["routing_url"] == "/secure-panel-inotex/ai/routing"


def test_get_ai_connection_never_returns_the_key(client):
    from app.db.queries import set_setting

    set_setting("ai_api_key", "sk-legacy-must-not-appear-0003")
    res = client.get("/admin/api/ai-connection")
    assert res.status_code == 200
    assert res.json()["has_key"] is True
    assert "sk-legacy-must-not-appear-0003" not in res.text


def test_saving_ai_connection_persists_to_postgres(client, conn):
    res = _post(client, "/admin/api/ai-connection", {
        "api_base": "https://93.184.216.34/v1",
        "api_key": "sk-saved-through-api-0004",
        "model_stt": "whisper-1",
        "feature_tts": False, "feature_stt": True,
        "search_backend": "tfidf", "default_lang": "fa",
    })
    assert res.status_code == 200, res.text[:400]
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       ("ai_model_stt",)).fetchone()
    assert row["value"] == "whisper-1"
    # Booleans reach `settings` as the strings this app has always used —
    # `settings.value` is TEXT, so a real bool would be a DatatypeMismatch.
    tts = conn.execute("SELECT value FROM settings WHERE key = ?",
                       ("tts_enabled",)).fetchone()
    assert tts["value"] == "false"


def test_the_settings_upsert_survives_being_written_twice(client, conn):
    """`set_setting` needed an ON CONFLICT clause on PostgreSQL; without it the
    second save was a UniqueViolation and a 500."""
    for lang in ("fa", "en", "fa"):
        res = _post(client, "/admin/api/ai-connection",
                    {"api_base": "", "api_key": "", "model_stt": "whisper-1",
                     "default_lang": lang, "search_backend": "tfidf"})
        assert res.status_code == 200, res.text[:400]
    n = conn.execute("SELECT count(*) AS n FROM settings WHERE key = ?",
                     ("ai_model_stt",)).fetchone()["n"]
    assert n == 1


# ── AI Routing API ──────────────────────────────────────────────────────

def test_get_routes_returns_200_with_the_seeded_tasks(client):
    res = client.get("/admin/api/ai/routes")
    assert res.status_code == 200, res.text[:400]
    body = res.json()
    assert {r["task"] for r in body["routes"]} >= {"chat", "classify"}
    assert isinstance(body["instances"], list)
    assert isinstance(body["models"], list)


def test_adding_and_removing_a_route_target_through_the_api(client):
    iid = store.create_instance("openai", "RouteGw",
                                {"base_url": "https://93.184.216.34/v1"},
                                "sk-route-api-0005", enabled=True, actor="pgtest")
    res = _post(client, "/admin/api/ai/routes/target",
                {"task": "chat", "instance_id": iid, "model_id": "gpt-4.1"})
    assert res.status_code == 200, res.text[:400]
    target_id = res.json()["target_id"]

    listed = client.get("/admin/api/ai/routes").json()["targets"]
    assert [t["id"] for t in listed] == [target_id]
    assert listed[0]["enabled"] is True

    assert _post(client, f"/admin/api/ai/routes/target/{target_id}/set-enabled",
                 {"enabled": False}).status_code == 200
    assert client.get("/admin/api/ai/routes").json()["targets"][0]["enabled"] is False

    assert _post(client, f"/admin/api/ai/routes/target/{target_id}/remove"
                 ).status_code == 200
    assert client.get("/admin/api/ai/routes").json()["targets"] == []


def test_reordering_targets_through_the_api_returns_200(client):
    ids = []
    for n in range(3):
        iid = store.create_instance("openai", f"Gw{n}",
                                    {"base_url": "https://93.184.216.34/v1"},
                                    f"sk-reorder-000{n}", enabled=True,
                                    actor="pgtest")
        ids.append(_post(client, "/admin/api/ai/routes/target",
                         {"task": "chat", "instance_id": iid,
                          "model_id": "gpt-4.1"}).json()["target_id"])
    res = _post(client, "/admin/api/ai/routes/reorder",
                {"task": "chat", "ordered_ids": [ids[2], ids[1], ids[0]]})
    assert res.status_code == 200, res.text[:400]
    listed = client.get("/admin/api/ai/routes").json()["targets"]
    assert [t["id"] for t in listed] == [ids[2], ids[1], ids[0]]


def test_a_target_for_an_unknown_instance_is_a_400_not_a_500(client):
    res = _post(client, "/admin/api/ai/routes/target",
                {"task": "chat", "instance_id": "ghost", "model_id": "m"})
    assert res.status_code == 400


# ── Provider API ────────────────────────────────────────────────────────

def test_the_provider_list_endpoint_renders_boolean_columns(client):
    store.create_instance("openai", "Listed",
                          {"base_url": "https://93.184.216.34/v1"},
                          "sk-listed-0006", enabled=True, actor="pgtest")
    res = client.get("/admin/api/ai/providers")
    assert res.status_code == 200, res.text[:400]
    rows = res.json()["providers"]
    assert len(rows) == 1
    assert rows[0]["enabled"] is True
    assert "secret_enc" not in rows[0]


def test_creating_a_provider_through_the_api_saves_it_disabled(client, conn):
    res = _post(client, "/admin/api/ai/providers", {
        "provider_type": "openai", "display_name": "Created via API",
        "config": {"base_url": "https://93.184.216.34/v1"},
        "api_key": "sk-created-via-api-0007",
    })
    assert res.status_code == 200, res.text[:400]
    row = conn.execute("SELECT enabled, has_secret, config FROM"
                       " ai_provider_instances").fetchone()
    assert row["enabled"] is False
    assert row["has_secret"] is True
    assert row["config"]["base_url"] == "https://93.184.216.34/v1"


def test_the_ai_summary_and_usage_endpoints_return_200(client):
    assert client.get("/admin/api/ai/summary").status_code == 200
    assert client.get("/admin/api/ai/usage").status_code == 200
    assert client.get("/admin/api/ai/models").status_code == 200
