"""CSRF protection on admin mutations.

The cookie is already SameSite=Lax, which is NOT sufficient on its own: Lax
still allows top-level GET navigation, older browsers ignore it, and a
same-site page is unaffected. These tests hold the server-side check.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "csrf.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('csrf','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "csrf",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        c.session_token = token
        yield c


def _token(client) -> str:
    return client.get("/admin/csrf").json()["csrf_token"]


def test_token_is_a_full_length_mac(client):
    assert len(_token(client)) == 64


def test_token_endpoint_requires_authentication(tmp_path, monkeypatch):
    """Otherwise any page could harvest a token cross-origin."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/csrf").status_code == 401


def test_reads_do_not_require_a_token(client):
    assert client.get("/admin/api/ops/maintenance").status_code == 200


# Every family of admin mutation named in the requirement.
MUTATIONS = [
    ("maintenance",     "/admin/api/ops/maintenance",              {"enabled": True}),
    ("log truncate",    "/admin/api/logs/truncate",                {"category": "system"}),
    ("log settings",    "/admin/api/logs/settings",                {"retention_days": 30,
                                                                    "audit_retention_days": 365,
                                                                    "security_retention_days": 365,
                                                                    "debug_enabled": False,
                                                                    "min_level": "info",
                                                                    "content_policy": "redacted"}),
    ("service action",  "/admin/api/ops/services/action",          {"action": "health_check"}),
    ("session revoke",  "/admin/api/security/sessions/revoke",     {"fingerprint": "abcdefgh"}),
    ("db maintenance",  "/admin/api/infra/database/pg/maintenance", {"action": "check_connectivity"}),
    ("backup create",   "/admin/api/infra/backups",                 {}),
    ("sms settings",    "/admin/api/sms",                           {"enabled": False, "provider": "dev"}),
]


@pytest.mark.parametrize("name,path,body", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutation_without_a_token_is_forbidden(client, name, path, body):
    assert client.post(path, json=body).status_code == 403, name


@pytest.mark.parametrize("name,path,body", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutation_with_a_forged_token_is_forbidden(client, name, path, body):
    r = client.post(path, json=body, headers={"X-CSRF-Token": "de" * 32})
    assert r.status_code == 403, name


def test_a_valid_token_lets_the_mutation_through(client):
    r = client.post("/admin/api/ops/maintenance", json={"enabled": True},
                    headers={"X-CSRF-Token": _token(client)})
    assert r.status_code == 200
    client.post("/admin/api/ops/maintenance", json={"enabled": False},
                headers={"X-CSRF-Token": _token(client)})


def test_a_token_from_another_session_is_rejected(client):
    """The token is bound to ONE session, so a leaked token from a different
    admin cannot be replayed."""
    from app.auth.csrf import token_for_session
    r = client.post("/admin/api/ops/maintenance", json={"enabled": True},
                    headers={"X-CSRF-Token": token_for_session("a-different-session")})
    assert r.status_code == 403


def test_the_token_changes_when_the_session_changes(client):
    from app.auth.csrf import token_for_session
    assert token_for_session("session-a") != token_for_session("session-b")


def test_delete_is_protected_too(client):
    """Not only POST — DELETE mutates as well."""
    r = client.delete("/admin/api/infra/backups/pg_20260101_120000_abc123")
    assert r.status_code == 403


def test_login_stays_exempt_because_there_is_no_session_yet(client):
    """A session-bound token cannot exist before authentication; login is
    protected by credentials and the brute-force lockout instead."""
    client.cookies.clear()
    r = client.post("/admin/login",
                    json={"username": "nobody", "password": "x", "sec_answer": "y"})
    assert r.status_code in (401, 429)


def test_a_rejected_request_is_recorded_as_a_security_event(client):
    from app.services import applog
    applog.truncate()
    client.post("/admin/api/ops/maintenance", json={"enabled": True})
    rows, _ = applog.query(category="security", q="csrf", limit=5)
    assert any(r["event_name"] == "security.csrf.rejected" for r in rows)


def test_the_public_chat_endpoint_is_not_csrf_protected(client):
    """The chat is embedded on customer sites by design; requiring an admin
    CSRF token there would break every embed. It has its own defences —
    HMAC chat token, origin allowlist and rate limiting."""
    r = client.post("/chat", json={"message": "سلام", "lang": "fa"})
    assert r.status_code != 403 or "توکن امنیتی" not in r.text
