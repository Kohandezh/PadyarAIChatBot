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
    # The gap this branch closes: the synonyms router mounts outside the
    # /admin/ prefixes while authenticating with the admin cookie, so its
    # mutations belong in the same 403 matrix as every admin family above.
    ("synonyms add",    "/api/synonyms",                            {"source": "الف", "target": "ب"}),
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


def test_synonyms_delete_without_a_token_is_forbidden(client):
    """The original CSRF gap: DELETE /api/synonyms rides the admin cookie
    from a forged cross-site page just like POST does."""
    r = client.delete("/api/synonyms/x?target=y")
    assert r.status_code == 403


def test_a_valid_token_lets_synonyms_mutations_through(client):
    """Closing the gap must not close out the real caller — fetchAuth()
    already sends the header for both calls, so they keep working."""
    headers = {"X-CSRF-Token": _token(client)}
    assert client.post("/api/synonyms", json={"source": "cs", "target": "ct"},
                       headers=headers).status_code == 200
    assert client.delete("/api/synonyms/cs?target=ct",
                         headers=headers).status_code == 200


def test_synonyms_get_with_cookie_and_no_token_still_works(client):
    """Pins enforce()'s method gate against over-reach: /api/synonyms newly
    sits under a protected prefix, but a read must never need a token —
    GET does not even enter the gate."""
    r = client.get("/api/synonyms")
    assert r.status_code == 200


def test_anonymous_synonyms_post_is_auth_rejected_not_csrf_rejected(client):
    """With no cookie, enforce() steps aside and lets authentication speak:
    401, not 403 — the distinction an operator debugging a failure relies on."""
    client.cookies.clear()
    r = client.post("/api/synonyms", json={"source": "a", "target": "b"})
    assert r.status_code == 401


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


# --- Conformance: the middleware's promise, checked against the route table ---

def _iter_api_routes(routes):
    """Yield every APIRoute reachable from a Starlette route list.

    FastAPI 0.141 nests each included router in a private _IncludedRouter
    (no public .routes), so the walker recurses through original_router.
    A plain .routes fallback keeps it working for Mounts and any future
    wrapper that exposes routes directly; if FastAPI ever changes shape and
    both paths go silent, the non-vacuity guard below fails loudly instead
    of the test passing vacuously.
    """
    from fastapi.routing import APIRoute
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_api_routes(nested.routes)
        elif hasattr(route, "routes"):
            yield from _iter_api_routes(route.routes)


def _depends_on_verify_admin(dependant, depth=0):
    """True if verify_admin appears anywhere in the dependency tree.

    Router-level dependencies (APIRouter(dependencies=[Depends(verify_admin)]))
    are merged into each route's dependant at include time, so one recursive
    walk over dependant.dependencies covers both declaration styles.
    """
    from app.auth.security import verify_admin
    if depth > 8:
        return False
    return any(sub.call is verify_admin or _depends_on_verify_admin(sub, depth + 1)
               for sub in dependant.dependencies)


def test_every_admin_mutation_sits_under_the_csrf_prefixes():
    """The middleware gates on path prefixes (PROTECTED_PREFIXES), so a
    verify_admin-protected mutation mounted outside them would be silently
    unprotected — exactly how the synonyms API shipped. This walks the live
    route table and restores the invariant generally: add a cookie-authed
    mutation anywhere outside the prefixes and CI fails here, not in an
    audit a year later.
    """
    from app.main import app
    from app.auth.csrf import PROTECTED_PREFIXES, PROTECTED_METHODS

    mutating = [r for r in _iter_api_routes(app.routes)
                if r.methods & PROTECTED_METHODS]
    protected = [r for r in mutating if _depends_on_verify_admin(r.dependant)]

    # Non-vacuity: if a FastAPI change makes the walker see almost nothing,
    # the assertions below would pass while checking nothing. The counts are
    # floors measured on the full route table (~95 mutating, ~83 protected),
    # not exact snapshots, so adding routes never breaks the guard.
    assert len(mutating) >= 50, f"route walker went blind: {len(mutating)} mutating"
    assert len(protected) >= 30, f"route walker went blind: {len(protected)} protected"

    unprotected = [f"{sorted(r.methods)} {r.path}" for r in protected
                   if not r.path.startswith(PROTECTED_PREFIXES)]
    assert not unprotected, (
        "verify_admin mutations outside PROTECTED_PREFIXES are CSRF-exempt "
        f"by accident — extend PROTECTED_PREFIXES in app/auth/csrf.py or "
        f"move the route: {unprotected}")
