"""Operating the selection tier: is JSON mode alive, and which tier answered?

TWO BLIND SPOTS THIS CLOSES.

1. JSON MODE CAN BE SILENTLY DEAD. `adapters/sakoo.py` reports
   `supports_json_object() == False`, so the field is dropped from the request
   body; `AnthropicAdapter.invoke` never reads `req.response_format` at all
   while inheriting `base.py`'s `return True`. On either route the model
   answers in PROSE with HTTP 200, `select_records` returns None, and the
   install quietly behaves exactly like today's chatbot — no error, no log
   line, nobody knows. A deployment-time probe with a red banner is the
   difference between "the feature is off" and "nobody knows it is off".

   It is kept SEPARATE from `test_instance` because `test_instance` is
   documented as never sending paid inference; this one deliberately does send
   one tiny request, so it is its own operator action.

2. NOBODY CAN SEE WHICH TIER ANSWERED. `GET /admin/api/stats` reports
   SUM(tokens), SUM(cost) and COUNT(*) and nothing else, so "read the
   ai_options rows on day one" means opening a psql shell during an
   exhibition. A per-source breakdown makes the options-eagerness risk
   measurable within an hour of opening — and it finally exposes today's tier
   distribution, which nobody can currently see either.
"""
import asyncio
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient

from app.services.ai import store


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def ai_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    store.ensure_ai_tables()
    store.seed_bootstrap_pricing()
    store._invalidate_runtime()
    yield
    store._invalidate_runtime()


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ops_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
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
                     " VALUES ('opsadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "opsadmin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        yield c
    store._invalidate_runtime()


class _FakeAdapter:
    """A provider that either honours response_format or silently drops it —
    the two live behaviours the probe has to tell apart."""

    def __init__(self, honours: bool):
        self.honours = honours
        self.requests = []

    def supports_json_object(self, model_id):
        return self.honours

    async def invoke(self, rt, model_id, req):
        from app.services.ai.request import AIResponse
        self.requests.append(req)
        content = ('{"ok": true}' if self.honours
                   else "Sure — ok is true. Let me know if you need anything else!")
        return AIResponse(content=content, task=req.task,
                          provider_type=getattr(rt, "provider_type", "fake"),
                          provider_instance_id=getattr(rt, "instance_id", ""),
                          model=model_id, tokens_total=8)


def _provider(model="m-json"):
    iid = store.create_instance("openai", "Probe", {}, "sk-1", enabled=True)
    store.add_manual_model(iid, model)
    store.add_target("chat", iid, model)
    return iid


def _audit_rows():
    from app.services import applog
    conn = applog.get_logs_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_logs")]
    finally:
        conn.close()


# ── The JSON-mode probe ──────────────────────────────────────────────────

def test_the_probe_reports_parsed_true_for_a_provider_that_honours_json_mode(ai_db, monkeypatch):
    """The happy path is the baseline the red banner is judged against."""
    from app.services.ai import health
    iid = _provider()
    fake = _FakeAdapter(honours=True)
    monkeypatch.setattr(health, "adapter_for", lambda ptype: fake)

    result = asyncio.run(health.test_json_mode(iid, actor="opsadmin"))
    assert result["ok"] is True, result
    assert result["parsed"] is True, result
    assert "latency_ms" in result, result

    assert fake.requests, "the probe must actually send a request"
    from app.services.ai.request import RESPONSE_JSON_OBJECT
    assert fake.requests[0].response_format == RESPONSE_JSON_OBJECT, fake.requests[0]


def test_the_probe_reports_parsed_false_when_the_adapter_drops_the_field(ai_db, monkeypatch):
    """THE case this exists for. The call SUCCEEDS — HTTP 200, real content,
    tokens billed — and the body is prose. Without the probe the selection
    tier is permanently off on this install and produces no error anywhere."""
    from app.services.ai import health
    iid = _provider()
    fake = _FakeAdapter(honours=False)
    monkeypatch.setattr(health, "adapter_for", lambda ptype: fake)

    result = asyncio.run(health.test_json_mode(iid, actor="opsadmin"))
    assert result["parsed"] is False, result


def test_the_probe_writes_an_audit_row(ai_db, monkeypatch):
    """An operator sent paid traffic on purpose. That is an action, and
    actions are audited — same rule the existing connectivity test follows."""
    from app.services.ai import health
    iid = _provider()
    monkeypatch.setattr(health, "adapter_for", lambda ptype: _FakeAdapter(True))

    asyncio.run(health.test_json_mode(iid, actor="opsadmin"))
    rows = _audit_rows()
    assert rows, "the probe must be audited"
    assert any("json" in (r["event_name"] or "").lower() for r in rows), \
        [r["event_name"] for r in rows]


def test_the_probe_reports_a_failure_instead_of_raising(ai_db, monkeypatch):
    """It runs from an admin page. A provider outage during the probe must be
    a red result, not a 500."""
    from app.services.ai import health
    from app.services.ai.errors import AIError
    iid = _provider()

    class _Down:
        def supports_json_object(self, model_id):
            return True

        async def invoke(self, rt, model_id, req):
            raise AIError(code="provider_unavailable", provider_detail="down")

    monkeypatch.setattr(health, "adapter_for", lambda ptype: _Down())
    result = asyncio.run(health.test_json_mode(iid, actor="opsadmin"))
    assert result["ok"] is False, result
    assert result["parsed"] is False, result


def test_the_probe_never_echoes_raw_provider_text(ai_db, monkeypatch):
    """Providers have been observed echoing the Authorization header back
    inside an error body. `detail` reaches an admin page, so it is scrubbed
    here as well as centrally."""
    from app.services.ai import health
    from app.services.ai.errors import AIError
    iid = _provider()
    # Key-shaped on purpose, with the scanner's not-real marker in it. See
    # PLACEHOLDER_WORDS in scripts/make-handover-zip.py.
    leaked = "Authorization: Bearer sk-livedeadbeefcafe-notreal-0000"

    class _Leaky:
        def supports_json_object(self, model_id):
            return True

        async def invoke(self, rt, model_id, req):
            raise AIError(code="authentication_failed", provider_detail=leaked)

    monkeypatch.setattr(health, "adapter_for", lambda ptype: _Leaky())
    result = asyncio.run(health.test_json_mode(iid, actor="opsadmin"))
    assert "sk-live-LEAKED-KEY" not in str(result), result


def test_an_unknown_provider_instance_is_reported_not_raised(ai_db):
    from app.services.ai import health
    result = asyncio.run(health.test_json_mode("no-such-instance"))
    assert result["ok"] is False, result


# ── The admin route in front of it ───────────────────────────────────────

def test_the_admin_panel_can_run_the_json_mode_probe(admin_client, monkeypatch):
    """One button next to the existing Test button. An operator who cannot
    read Python must still be able to find out that multi-choice answers do
    not work on this provider."""
    from app.services.ai import health
    iid = _provider()
    monkeypatch.setattr(health, "adapter_for", lambda ptype: _FakeAdapter(True))

    csrf = admin_client.get("/admin/csrf").json()["csrf_token"]
    r = admin_client.post(f"/admin/api/ai/providers/{iid}/test-json", json={},
                          headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["parsed"] is True, r.json()


def test_the_json_mode_probe_needs_an_admin_session(admin_client, monkeypatch):
    """It sends paid traffic. It is not a public endpoint."""
    iid = _provider()
    admin_client.cookies.clear()
    r = admin_client.post(f"/admin/api/ai/providers/{iid}/test-json", json={})
    assert r.status_code in (401, 403), r.text


# ── The tier breakdown on the dashboard ──────────────────────────────────

def test_the_stats_endpoint_reports_how_many_answers_each_tier_gave(admin_client):
    """The options-eagerness risk — a bot that asks "which one?" about
    questions it could have answered — is invisible until the tiers are
    counted. `by_source` is what makes it a number an operator can watch
    during the first hour of an exhibition."""
    from app.db.queries import log_chat

    log_chat("q1", "r", "text", "local_pick", 0.9)
    log_chat("q2", "r", "text", "local_pick", 0.9)
    log_chat("q3", "r", "text", "ai_options", 0.5)
    log_chat("q4", "r", "text", "local_company_search", 0.9)

    r = admin_client.get("/admin/api/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "by_source" in body, sorted(body)

    counts = {row["source"]: row["count"] for row in body["by_source"]}
    assert counts.get("local_pick") == 2, counts
    assert counts.get("ai_options") == 1, counts
    assert counts.get("local_company_search") == 1, counts


def test_the_tier_breakdown_only_counts_the_last_day(admin_client):
    """A 24h window, like every other operational number on that page: an
    exhibition is judged by today, not by the whole install's history."""
    from app.db.queries import log_chat
    from app.db.connection import get_db_connection

    log_chat("old", "r", "text", "ai_options", 0.5)
    conn = get_db_connection()
    conn.execute("UPDATE chat_logs SET created_at = '2020-01-01 00:00:00'")
    conn.commit()
    conn.close()
    log_chat("new", "r", "text", "local_pick", 0.9)

    body = admin_client.get("/admin/api/stats").json()
    counts = {row["source"]: row["count"] for row in body["by_source"]}
    assert counts.get("local_pick") == 1, counts
    assert "ai_options" not in counts, counts
