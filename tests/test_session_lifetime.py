"""Session & token lifetime wiring — tests for plans/session-lifetime.md.

Three "reader half shipped, writer half never did" gaps, one behaviour per
test:

  * the admin sliding session: verify_admin slides the DB row but the cookie
    kept its original 1h max_age and died mid-session. A request.state flag
    + middleware now re-issue the cookie on every authenticated response —
    and by construction never on a logout or an error;
  * chat token refresh: POST /api/chat-token mints a fresh v2 token for a
    caller who PROVES possession of a still-valid or recently-expired one
    (grace), fenced by its own per-IP bucket so a refresh+retry pair never
    eats the visitor's chat budget;
  * the padyar_conv correlation cookie: read on every /chat since the log
    explorer shipped, but never written — every message got a fresh random
    conversation id. Now set (and echoed) on every successful /chat.
"""
import hashlib
import secrets
import time

import pytest
from fastapi.testclient import TestClient


ORIGIN_HEADERS = {"Origin": "http://localhost", "User-Agent": "pytest-agent/1.0"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "lifetime.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed_admin(username="admin", password="pw-test-123", answer="blue"):
    from app.db.connection import get_db_connection
    from app.auth.security import hash_password
    conn = get_db_connection()
    conn.execute(
        "INSERT OR IGNORE INTO admins (username, password_hash, salt,"
        " security_question, security_answer_hash) VALUES (?,?,?,?,?)",
        (username, hash_password(password), "", "color?",
         hashlib.sha256(answer.encode()).hexdigest()))
    conn.commit()
    conn.close()


def _login(client, username="admin", password="pw-test-123", answer="blue"):
    return client.post("/admin/login", json={
        "username": username, "password": password, "sec_answer": answer})


def _make_chat_answer_ok(monkeypatch):
    """Force /chat to a deterministic 200 from Tier 1 — the conv-cookie and
    applog assertions need a served answer, not the 503 an empty dataset
    would produce."""
    from app.routers import chat as chat_router
    monkeypatch.setattr(
        chat_router, "find_best_match",
        lambda q: ({"id": "t1", "text": "پاسخ آزمایشی", "video_url": ""}, 0.95))


def _admin_cookie_headers(r) -> list:
    return [c for c in r.headers.get_list("set-cookie")
            if c.startswith("admin_session=")]


# ── (a) Admin cookie slide ──────────────────────────────────────────────


def test_admin_cookie_reissued_on_authenticated_request(client):
    """The flag→middleware pair: any authenticated request re-issues the
    cookie with the same attributes login used and a full max_age, so the
    browser's copy tracks the DB slide instead of dying at login + 1h."""
    _seed_admin()
    assert _login(client).status_code == 200
    r = client.get("/admin/check_auth")
    assert r.status_code == 200
    cookies = _admin_cookie_headers(r)
    assert cookies, "authenticated response must re-issue the session cookie"
    c = cookies[0]
    assert "Max-Age=3600" in c
    assert "HttpOnly" in c
    assert "SameSite=lax" in c


def test_admin_cookie_reissue_near_expiry(client):
    """The reported symptom, named: a session 2 minutes from its DB expiry
    still gets a FRESH full-length cookie. Under always-re-issue this is the
    same mechanism as above — kept as the regression for the bug report."""
    import datetime as _dt
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                 " VALUES (?,?,?)",
                 (token, "admin",
                  (_dt.datetime.now() + _dt.timedelta(minutes=2)).isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set("admin_session", token)
    r = client.get("/admin/check_auth")
    assert r.status_code == 200
    cookies = _admin_cookie_headers(r)
    assert cookies and "Max-Age=3600" in cookies[0]


def test_logout_clears_cookie_without_reissue(client):
    """Logout deletes the cookie and the middleware must NOT resurrect it:
    admin_logout never runs verify_admin, so the slide flag is never set on
    a logout request. The only admin_session Set-Cookie on the response is
    the deletion."""
    _seed_admin()
    assert _login(client).status_code == 200
    csrf = client.get("/admin/csrf").json()["csrf_token"]
    r = client.post("/admin/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    cookies = _admin_cookie_headers(r)
    assert cookies, "logout must carry the deletion"
    assert all("Max-Age=0" in c for c in cookies), cookies
    assert not any("Max-Age=3600" in c for c in cookies)


def test_unauthenticated_requests_get_no_set_cookie(client):
    """No valid session → verify_admin 401s before the slide, so no flag,
    no renewal — and the status guard keeps error responses cookie-free."""
    r = client.get("/admin/check_auth")
    assert r.status_code == 401
    assert "set-cookie" not in [k.lower() for k in r.headers.keys()]


# ── (b) Token refresh ───────────────────────────────────────────────────


class _Req:
    """Just enough Request for validate_chat_token (headers only)."""

    def __init__(self, token):
        self.headers = {"X-Chat-Token": token} if token else {}


def _refresh(client, token, headers=None):
    return client.post("/api/chat-token",
                       headers={**ORIGIN_HEADERS, "X-Chat-Token": token, **(headers or {})})


def test_refresh_returns_valid_token(client):
    """The happy path round-trip: the minted token validates (and carries a
    nonce) — no hand-verification of the signature; the format is owned by
    the rate-limit plan."""
    from app.auth.security import generate_chat_token, validate_chat_token
    r = _refresh(client, generate_chat_token())
    assert r.status_code == 200
    token = r.json()["token"]
    assert token
    assert validate_chat_token(_Req(token)) == token.split(".")[1]


def test_refresh_mints_v2_token(client):
    """Pins the interaction with the rate-limit plan: every refresh must be
    a v2 mint (3 dot-parts) with a FRESH nonce — never a regression to the
    nonce-less v1 shape or a constant identity."""
    from app.auth.security import generate_chat_token
    held = generate_chat_token()
    t1 = _refresh(client, held).json()["token"]
    t2 = _refresh(client, held).json()["token"]
    assert len(t1.split(".")) == 3
    assert len(t2.split(".")) == 3
    assert t1.split(".")[1] != t2.split(".")[1]


def test_refresh_rejects_bad_origin(client):
    """Guard 1 fires first: no Origin/Referer and a short UA → 403 before
    the token is even looked at."""
    r = client.post("/api/chat-token", headers={"User-Agent": "x"})
    assert r.status_code == 403


def test_refresh_rejects_missing_or_garbage_token(client):
    """Guard 2: possession of a token this server signed is the only thing
    keeping the endpoint from being an open minting oracle."""
    from app.auth.security import generate_chat_token
    r = client.post("/api/chat-token", headers=ORIGIN_HEADERS)
    assert r.status_code == 403
    r = _refresh(client, "123.deadbeef")
    assert r.status_code == 403
    # A well-shaped but unsigned token is equally dead.
    r = _refresh(client, f"{int(time.time())}.aaaaaaaaaaaaaaaa."
                         f"{secrets.token_hex(16)}")
    assert r.status_code == 403
    # Sanity: the control — a real token on the SAME origin passes.
    assert _refresh(client, generate_chat_token()).status_code == 200


def test_refresh_accepts_recently_expired_token(client, monkeypatch):
    """The grace window: a token past its strict TTL (dead on /chat) is
    still accepted here for one more mint — the mid-conversation rescue."""
    from app.auth import security
    from app.auth.security import generate_chat_token
    monkeypatch.setattr(security, "CHAT_TOKEN_TTL", 1)  # enforcing binding
    held = generate_chat_token()
    time.sleep(1.1)  # strictly past TTL=1s, far inside the 900s grace
    assert _refresh(client, held).status_code == 200


def test_refresh_rejects_long_expired_token(client, monkeypatch):
    """Grace is bounded: older than TTL + CHAT_TOKEN_REFRESH_GRACE is a
    plain expired token, mint refused. The old token is hand-signed with the
    app's real key so the 403 comes from the TTL check, not a bad signature
    (and no test sleeps a quarter hour)."""
    from app import config
    from app.auth import security
    monkeypatch.setattr(security, "CHAT_TOKEN_TTL", 1)
    ts = str(int(time.time()) - (1 + config.CHAT_TOKEN_REFRESH_GRACE + 60))
    nonce = secrets.token_hex(8)
    sig = hashlib.sha256(
        f"{ts}.{nonce}.{security._get_hmac_key()}".encode()).hexdigest()[:32]
    assert _refresh(client, f"{ts}.{nonce}.{sig}").status_code == 403


def test_refresh_rate_limited(client):
    """Guard 3: the endpoint's OWN per-IP bucket (separate from the chat
    budget) — CHAT_RATE_LIMIT refreshes pass, the next one 429s."""
    from app.auth import security
    from app.auth.security import generate_chat_token
    held = generate_chat_token()
    limit = security.CHAT_RATE_LIMIT
    for _ in range(limit):
        assert _refresh(client, held).status_code == 200
    assert _refresh(client, held).status_code == 429


# ── (c) Conversation cookie ─────────────────────────────────────────────


def _conv_cookie_header(r) -> str:
    matches = [c for c in r.headers.get_list("set-cookie")
               if c.startswith("padyar_conv=")]
    assert matches, "successful /chat must set the conversation cookie"
    return matches[0]


def test_chat_sets_conv_cookie_when_absent(client, monkeypatch):
    """First message: no cookie in the jar → a fresh id is minted AND
    persisted, with the same attribute set as the leads visitor cookie."""
    _make_chat_answer_ok(monkeypatch)
    from app.auth.security import generate_chat_token
    r = client.post("/chat", json={"message": "سوال اول"},
                    headers={**ORIGIN_HEADERS,
                             "X-Chat-Token": generate_chat_token()})
    assert r.status_code == 200
    c = _conv_cookie_header(r)
    value = c.split("=", 1)[1].split(";", 1)[0]
    assert len(value) == 16 and all(ch in "0123456789abcdef" for ch in value)
    assert "HttpOnly" in c
    assert "SameSite=lax" in c
    assert "Max-Age=86400" in c  # CONV_COOKIE_MAX_AGE = 24h


def test_chat_reuses_conv_cookie_across_messages(client, monkeypatch):
    """Second message: the jar now carries the cookie → the SAME id is
    echoed on the response and both applog `conversation.message.received`
    rows share it. That single value is the log explorer's reconstruction
    handle — the whole point of the cookie."""
    _make_chat_answer_ok(monkeypatch)
    from app.auth.security import generate_chat_token
    headers = {**ORIGIN_HEADERS, "X-Chat-Token": generate_chat_token()}

    r1 = client.post("/chat", json={"message": "سوال اول"}, headers=headers)
    assert r1.status_code == 200
    conv = client.cookies.get("padyar_conv")
    assert conv

    r2 = client.post("/chat", json={"message": "سوال دوم"}, headers=headers)
    assert r2.status_code == 200
    assert f"padyar_conv={conv}" in _conv_cookie_header(r2)  # echoed, slid

    from app.services import applog
    conn = applog.get_logs_connection()
    rows = conn.execute(
        "SELECT conversation_id FROM app_logs"
        " WHERE event_name = 'conversation.message.received'").fetchall()
    conn.close()
    assert len(rows) == 2
    assert {row["conversation_id"] for row in rows} == {conv}
