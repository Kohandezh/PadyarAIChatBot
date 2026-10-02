"""Prometheus metrics endpoint (docs/features/metrics-endpoint/SPEC.md).

Red-first: every test here exercises the WIRING, not the library — the
counter increments happen because middleware/hooks call them on real
request paths, so removing a hook fails the matching test.
"""
import datetime
import os
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "metrics.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    from app.main import app
    with TestClient(app) as c:
        yield c


def _counter_value(counter, **labels) -> int:
    child = counter.labels(**labels) if labels else counter
    return int(child._value.get())


def _counter_total(counter) -> int:
    # Skip the `_created` sample every Counter also exposes (its value is a
    # timestamp, not a count).
    total = 0
    for metric in counter.collect():
        total += sum(int(s.value) for s in metric.samples
                     if not s.name.endswith("_created"))
    return total


# ── HTTP middleware ────────────────────────────────────────────────────


def test_http_requests_counter_uses_route_template(client):
    from app.services import metrics
    before = _counter_value(metrics.http_requests_total,
                            method="GET", route="/api/health", status="200")
    res = client.get("/api/health")
    assert res.status_code == 200
    after = _counter_value(metrics.http_requests_total,
                           method="GET", route="/api/health", status="200")
    assert after == before + 1


def test_raw_paths_never_become_route_labels(client):
    """Cardinality guard: an unmatched or asset path must collapse to a
    fixed label, never enter the registry as itself."""
    from app.services import metrics
    client.get("/definitely-not-a-route-abc123")
    client.get("/static/no-such-asset-9876.css")
    routes = set()
    for metric in metrics.http_requests_total.collect():
        for sample in metric.samples:
            routes.add(sample.labels["route"])
    assert "/definitely-not-a-route-abc123" not in routes
    assert "/static/no-such-asset-9876.css" not in routes
    assert "unmatched" in routes
    assert "/static" in routes


def test_request_duration_histogram_observes(client):
    from app.services import metrics
    buckets_before = 0
    for metric in metrics.http_request_duration_seconds.collect():
        buckets_before = sum(s.value for s in metric.samples)
    client.get("/api/health")
    buckets_after = 0
    for metric in metrics.http_request_duration_seconds.collect():
        buckets_after = sum(s.value for s in metric.samples)
    assert buckets_after > buckets_before


# ── Endpoint security ──────────────────────────────────────────────────


def test_metrics_forbidden_without_auth(client):
    assert client.get("/metrics").status_code == 403


def test_metrics_bearer_token_auth(client, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "METRICS_TOKEN", "sekret-token")
    wrong = client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 403
    missing = client.get("/metrics")
    assert missing.status_code == 403
    ok = client.get("/metrics", headers={"Authorization": "Bearer sekret-token"})
    assert ok.status_code == 200
    assert "http_requests_total" in ok.text


def test_metrics_admin_session_auth(client):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    token = secrets.token_hex(16)
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                 " security_question, security_answer_hash)"
                 " VALUES ('metrics','x','y','q','z')")
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                 (token, "metrics",
                  (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set("admin_session", token)
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "chat_tier_served_total" in res.text


def test_metrics_bearer_token_rejected_when_only_session_mode(client, monkeypatch):
    """Session mode must not accept a Bearer header as a side door: with no
    METRICS_TOKEN configured, the header is meaningless and the admin
    session is the only way in."""
    import app.config as config
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    res = client.get("/metrics", headers={"Authorization": "Bearer anything"})
    assert res.status_code == 403


# ── Chat tier hook ─────────────────────────────────────────────────────


def test_chat_turn_increments_tier_counter(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "metrics-chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    from app.services import metrics
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        from app.db.queries import set_setting
        set_setting("openai_enabled", "false")
        before = _counter_total(metrics.chat_tier_served_total)
        res = c.post("/chat", json={"message": "سلام نمایشگاه", "lang": "fa"})
        assert res.status_code == 200
        after = _counter_total(metrics.chat_tier_served_total)
        assert after == before + 1
    security._chat_rate_limits.clear()


# ── Backup hook ────────────────────────────────────────────────────────


@pytest.fixture
def pg_backup_env(tmp_path, monkeypatch):
    from app.services import pg_backup
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: f"/bin/{name}")
    yield pg_backup


# create() alone is not a backup attempt any more: the outcome is counted by
# backup._run_backup_now, after verify. tests/test_backup_metrics.py covers
# that path. These two pin that create() itself no longer counts, so a dump
# that is never verified can not show up as a success.


def test_create_alone_does_not_count_a_success(pg_backup_env, monkeypatch):
    from app.services import metrics
    pg_backup = pg_backup_env

    def fake_run(argv, env, what):
        dump = argv[argv.index("--file") + 1]
        os.makedirs(os.path.dirname(dump), exist_ok=True)
        with open(dump, "wb") as f:
            f.write(b"padyar-dump")
    monkeypatch.setattr(pg_backup, "_run", fake_run)
    before = _counter_value(metrics.backup_outcome_total, result="success")
    manifest = pg_backup.create(actor="test")
    assert manifest["bytes"] == len(b"padyar-dump")
    assert _counter_value(metrics.backup_outcome_total, result="success") == before


def test_create_alone_does_not_count_a_failure(pg_backup_env, monkeypatch):
    from app.services import metrics
    pg_backup = pg_backup_env

    def boom(argv, env, what):
        raise pg_backup.BackupError("شکست")
    monkeypatch.setattr(pg_backup, "_run", boom)
    before = _counter_value(metrics.backup_outcome_total, result="failed")
    with pytest.raises(pg_backup.BackupError):
        pg_backup.create(actor="test")
    assert _counter_value(metrics.backup_outcome_total, result="failed") == before


# ── AI + circuit + health hooks ────────────────────────────────────────


def test_ai_calls_counter_records_usage(monkeypatch):
    from app.services.ai import engine
    from app.services.ai.request import AIRequest
    from app.services import metrics
    monkeypatch.setattr(engine.store, "record_usage", lambda row: None)
    req = AIRequest(task="chat", messages=[])
    before_ok = _counter_value(metrics.ai_calls_total,
                               provider="openai", outcome="success")
    engine._record_usage(req, "success",
                         {"provider_type": "openai", "provider_instance_id": "i",
                          "model_id": "m"}, 1, 0, {}, 10, "")
    assert _counter_value(metrics.ai_calls_total,
                          provider="openai", outcome="success") == before_ok + 1


def test_ai_circuit_state_gauge_tracks_transitions(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "metrics-circuit.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    from app.services.ai import circuit, errors as ai_errors, store
    from app.services import metrics
    init_db()
    store.ensure_ai_tables()
    store._invalidate_runtime()
    try:
        iid = "circuit-metrics-test"
        auth = ai_errors.AIError(code=ai_errors.AUTHENTICATION_FAILED,
                                 provider_detail="401")
        circuit.record_failure(iid, auth)
        assert _counter_value(metrics.ai_circuit_state, instance=iid) == 2
        circuit.reset(iid)
        assert _counter_value(metrics.ai_circuit_state, instance=iid) == 0
    finally:
        store._invalidate_runtime()


def test_a_stored_open_circuit_is_on_metrics_after_a_restart(tmp_path, monkeypatch):
    """SC-016 (REQ-077): a fresh app process with an `open` row and no event
    shows `ai_circuit_state{instance=...} 2` on /metrics. Before DEP-3 the
    gauge waited for the next transition, so the scrape had no series."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "metrics-restart.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "METRICS_TOKEN", "restart-token")
    from app.db.connection import get_db_connection, init_db
    from app.services.ai import store
    init_db()
    store.ensure_ai_tables()
    iid = f"restart-{secrets.token_hex(4)}"
    conn = get_db_connection()
    conn.execute("INSERT INTO ai_circuit_state (provider_instance_id, state)"
                 " VALUES (?, 'open')", (iid,))
    conn.commit()
    conn.close()

    from app.main import app
    with TestClient(app) as c:
        res = c.get("/metrics", headers={"Authorization": "Bearer restart-token"})
    assert res.status_code == 200
    assert f'ai_circuit_state{{instance="{iid}"}} 2.0' in res.text


def test_health_score_sets_gauge():
    from app.services import health, metrics
    result = health.health_score([])
    assert _counter_value(metrics.health_score) == result["score"]
