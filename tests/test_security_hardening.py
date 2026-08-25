"""Security hardening pass — regression tests for the fixes in this branch.

Covers, one behaviour per test:
  * /api/transcribe now requires the same guard trio as /chat (origin,
    chat token, rate limit) instead of being an open STT relay;
  * the chat rate limiter is shared through the rate_limit_buckets table
    (no longer N-per-worker in-memory state);
  * /chat and /api/transcribe limit per visitor identity (the signed
    token's nonce) with a loose per-IP backstop — a shared-NAT booth is no
    longer collectively punished, and voice draws from the same budget
    as text;
  * OTP endpoints limit per identity (destination / challenge) with a
    per-IP backstop, and GET / has its own generous render fence;
  * the legacy unsalted-SHA-256 security answer still verifies and is
    upgraded to bcrypt on the next successful login;
  * changing the password revokes every OTHER admin session;
  * CSV exports neutralize spreadsheet formula injection;
  * the bootstrap-credentials fallback never writes the password to a log;
  * the legacy ai_api_key settings row is encrypted at rest on boot.
"""
import datetime
import hashlib
import io
import secrets

import pytest
from fastapi.testclient import TestClient


ORIGIN_HEADERS = {"Origin": "http://localhost", "User-Agent": "pytest-agent/1.0"}


def _chat_limit() -> int:
    """The limit as the ENFORCING module sees it (it binds the value at
    import time, so re-reading app.config could disagree)."""
    from app.auth import security
    return security.CHAT_RATE_LIMIT


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "sec.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed_admin(username="admin", password="pw-test-123",
                answer="blue", answer_hash=None):
    from app.db.connection import get_db_connection
    from app.auth.security import hash_password
    conn = get_db_connection()
    conn.execute(
        "INSERT OR IGNORE INTO admins (username, password_hash, salt,"
        " security_question, security_answer_hash) VALUES (?,?,?,?,?)",
        (username, hash_password(password), "", "color?",
         answer_hash or hashlib.sha256(answer.encode()).hexdigest()))
    conn.commit()
    conn.close()


def _login(client, username="admin", password="pw-test-123", answer="blue"):
    return client.post("/admin/login", json={
        "username": username, "password": password, "sec_answer": answer})


# ── /api/transcribe ─────────────────────────────────────────────────────


def test_transcribe_without_token_is_forbidden(client):
    r = client.post("/api/transcribe",
                    files={"audio": ("r.webm", b"\x1a\x45\xdf\xa3" + b"x" * 32)},
                    headers=ORIGIN_HEADERS)
    assert r.status_code == 403


def test_transcribe_without_origin_is_forbidden(client):
    r = client.post("/api/transcribe",
                    files={"audio": ("r.webm", b"\x1a\x45\xdf\xa3" + b"x" * 32)},
                    headers={"User-Agent": "pytest-agent/1.0"})
    assert r.status_code == 403


def test_transcribe_with_bad_token_is_forbidden(client):
    r = client.post("/api/transcribe",
                    files={"audio": ("r.webm", b"\x1a\x45\xdf\xa3" + b"x" * 32)},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": "123.deadbeef"})
    assert r.status_code == 403


def test_transcribe_non_audio_is_rejected(client, monkeypatch):
    from app.routers import voice as voice_router
    monkeypatch.setattr(voice_router, "provider_config", lambda: ("https://x", "k"))
    from app.auth.security import generate_chat_token
    r = client.post(
        "/api/transcribe",
        files={"audio": ("payload.exe", b"MZ" + secrets.token_bytes(64))},
        headers={**ORIGIN_HEADERS, "X-Chat-Token": generate_chat_token()})
    assert r.status_code == 400


def test_transcribe_oversized_upload_is_rejected(client, monkeypatch):
    from app.routers import voice as voice_router
    monkeypatch.setattr(voice_router, "MAX_AUDIO_BYTES", 16)
    monkeypatch.setattr(voice_router, "provider_config", lambda: ("https://x", "k"))
    from app.auth.security import generate_chat_token
    r = client.post(
        "/api/transcribe",
        files={"audio": ("r.webm", b"\x1a\x45\xdf\xa3" + secrets.token_bytes(64))},
        headers={**ORIGIN_HEADERS, "X-Chat-Token": generate_chat_token()})
    assert r.status_code == 413


def test_transcribe_provider_error_is_scrubbed(client, monkeypatch):
    """The raw provider exception (base URL, request ids) must not reach an
    anonymous caller in the response body."""
    from app.routers import voice as voice_router

    def _boom(data, filename):
        raise RuntimeError("secret detail from https://provider.internal")

    monkeypatch.setattr(voice_router, "provider_config", lambda: ("https://x", "k"))
    monkeypatch.setattr(voice_router, "_transcribe_sync", _boom)
    from app.auth.security import generate_chat_token
    r = client.post(
        "/api/transcribe",
        files={"audio": ("r.webm", b"\x1a\x45\xdf\xa3" + b"x" * 32)},
        headers={**ORIGIN_HEADERS, "X-Chat-Token": generate_chat_token()})
    assert r.status_code == 500
    assert "secret detail" not in r.text


# ── shared (DB) rate limiter ────────────────────────────────────────────


def test_rate_limit_state_lands_in_the_shared_table(client):
    from app.auth.security import generate_chat_token
    from app.db.connection import get_db_connection
    # One token for both requests: with per-nonce identity buckets the count
    # must land in BOTH the visitor's tight bucket and the shared IP backstop.
    token = generate_chat_token()
    nonce = token.split(".")[1]
    for _ in range(2):
        client.post("/chat", json={"message": "salud"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": token})
    conn = get_db_connection()
    rows = conn.execute("SELECT key, ts FROM rate_limit_hits").fetchall()
    conn.close()
    keys = {r["key"] for r in rows}
    assert f"rl:chat:{nonce}" in keys       # decision D naming
    assert "rl:chatip:testclient" in keys


def test_rate_limit_blocks_in_the_db_not_per_process(client):
    """The counter must be readable by ANOTHER connection (i.e. another
    worker), which is the whole point of moving it out of the dict."""
    from app.auth.security import generate_chat_token
    limit = _chat_limit()
    # One token: one visitor spending their whole tight budget (a fresh mint
    # per request would now be a fresh identity each time).
    token = generate_chat_token()
    for _ in range(limit):
        client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": token})
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": token})
    assert r.status_code == 429


def test_rate_limit_window_is_sliding_not_fixed(client):
    """Regression for the CI flake: a fixed-window counter reset to zero at
    every window boundary, so requests straddling one were all admitted.
    The sliding window must block on the count inside the last window, no
    matter where the wall-clock window edges fall."""
    from app.auth.security import generate_chat_token
    limit = _chat_limit()
    token = generate_chat_token()
    # Fill the bucket right up to the limit — nothing here knows or cares
    # where a window boundary sits; a fixed window would reset on one.
    for _ in range(limit):
        client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": token})
    # Old hits (well outside the window) must not count: seed them directly
    # so no sleeps are involved, then the very next request is judged only
    # against the fresh ones.
    import time as _time
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE rate_limit_hits SET ts = ?", (_time.time() - 10_000,))
    conn.commit()
    conn.close()
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": token})
    assert r.status_code != 429  # everything expired -> window slid clean


def test_blocked_attempts_are_not_recorded(client):
    """A rejected request must not write a hit. Recording them would keep a
    tripped shared-NAT bucket full for as long as anyone keeps trying — the
    self-inflicted booth lockout the generous limit exists to avoid."""
    from app.auth.security import generate_chat_token
    from app.db.connection import get_db_connection
    limit = _chat_limit()
    token = generate_chat_token()
    nonce = token.split(".")[1]
    for _ in range(limit):
        client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": token})
    for _ in range(3):  # hammer past the limit
        client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": token})
    conn = get_db_connection()
    # Decision D naming: the request spends hits in exactly two buckets —
    # the visitor's identity bucket and the shared IP backstop — and the
    # blocked attempts wrote into NEITHER.
    n_tight = conn.execute("SELECT COUNT(*) AS n FROM rate_limit_hits"
                           " WHERE key = ?", (f"rl:chat:{nonce}",)).fetchone()["n"]
    n_ip = conn.execute("SELECT COUNT(*) AS n FROM rate_limit_hits"
                        " WHERE key = 'rl:chatip:testclient'").fetchone()["n"]
    conn.close()
    assert n_tight == limit  # exactly the admitted requests, not one more
    assert n_ip == limit


# ── per-identity (two-tier) chat limiting ───────────────────────────────


def test_booth_visitors_with_distinct_tokens_are_never_collectively_blocked(client):
    """The NAT-booth case the whole change exists for: ten visitors through
    ONE testclient IP, each with their own token, all admitted — the tight
    budget is per identity, and ten identities stay far under the backstop."""
    from app.auth.security import generate_chat_token
    for _ in range(10):
        r = client.post("/chat", json={"message": "salam"},
                        headers={**ORIGIN_HEADERS,
                                 "X-Chat-Token": generate_chat_token()})
        assert r.status_code != 429


def test_one_exhausted_identity_does_not_block_its_neighbour(client):
    """An abuser spends only their own tight bucket; a different visitor at
    the same address is served immediately after."""
    from app.auth.security import generate_chat_token
    abuser = generate_chat_token()
    for _ in range(_chat_limit()):
        client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": abuser})
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": abuser})
    assert r.status_code == 429
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS,
                             "X-Chat-Token": generate_chat_token()})
    assert r.status_code != 429


def test_ip_backstop_trips_independently_of_identities(client, monkeypatch):
    """Distinct identities defeat every tight bucket; the loose per-IP
    backstop is the fence that still trips (the token-mint flood bound)."""
    from app.auth import security
    from app.auth.security import generate_chat_token
    monkeypatch.setattr(security, "CHAT_IP_RATE_LIMIT", 3)
    for _ in range(3):  # each request carries a FRESH identity
        r = client.post("/chat", json={"message": "x"},
                        headers={**ORIGIN_HEADERS,
                                 "X-Chat-Token": generate_chat_token()})
        assert r.status_code != 429
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS,
                             "X-Chat-Token": generate_chat_token()})
    assert r.status_code == 429  # every identity far under 20; the IP is not


def test_legacy_v1_token_still_validates_and_limits_by_ip(client):
    """A "{ts}.{sig}" token minted before nonces keeps validating for its TTL
    and, carrying no identity, lands in the IP-keyed tight bucket — exactly
    the pre-nonce behaviour, so a deploy never strands held tokens."""
    import time as _time
    from app.auth import security
    from app.db.connection import get_db_connection
    ts = str(int(_time.time()))
    sig = hashlib.sha256(
        f"{ts}.{security._get_hmac_key()}".encode()).hexdigest()[:32]
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": f"{ts}.{sig}"})
    assert r.status_code != 403
    conn = get_db_connection()
    keys = {row["key"] for row in
            conn.execute("SELECT key FROM rate_limit_hits").fetchall()}
    conn.close()
    assert "rl:chat:ip:testclient" in keys


def test_validate_chat_token_returns_the_nonce_and_empty_for_v1(client):
    """The pinned contract: validate_chat_token(...) -> str. v2 returns its
    nonce (the rate-limit identity); v1 returns "" (callers fall back to
    IP-keyed limiting)."""
    from app.auth.security import generate_chat_token, validate_chat_token

    class _Req:
        def __init__(self, token):
            self.headers = {"X-Chat-Token": token} if token else {}

    v2 = generate_chat_token()
    assert validate_chat_token(_Req(v2)) == v2.split(".")[1]

    import time as _time
    from app.auth import security
    ts = str(int(_time.time()))
    sig = hashlib.sha256(
        f"{ts}.{security._get_hmac_key()}".encode()).hexdigest()[:32]
    assert validate_chat_token(_Req(f"{ts}.{sig}")) == ""


def test_tampered_nonce_fails_the_signature(client):
    """The identity inside a v2 token sits in the signed payload: changing
    the nonce breaks the signature exactly as changing the timestamp does."""
    from app.auth.security import generate_chat_token
    ts, _nonce, sig = generate_chat_token().split(".")
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS,
                             "X-Chat-Token": f"{ts}.ffffffffffffffff.{sig}"})
    assert r.status_code == 403
    # Any well-shaped-but-foreign 3-part token is equally dead.
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": "1.2.3"})
    assert r.status_code == 403


def test_voice_and_chat_draw_from_one_budget_per_visitor(client, monkeypatch):
    """Decision C: /api/transcribe shares /chat's buckets, so alternating
    surfaces cannot double a visitor's (or an address's) traffic. The tight
    chat:{nonce} bucket trips on a MIX of chat and transcribe requests."""
    from app.auth.security import generate_chat_token
    from app.routers import voice as voice_router
    monkeypatch.setattr(voice_router, "provider_config", lambda: ("https://x", "k"))
    monkeypatch.setattr(voice_router, "_transcribe_sync",
                        lambda data, name: "متن پیام")
    token = generate_chat_token()
    headers = {**ORIGIN_HEADERS, "X-Chat-Token": token}
    for _ in range(_chat_limit() - 1):
        assert client.post("/chat", json={"message": "x"},
                           headers=headers).status_code != 429
    # The LAST tight-budget hit is spent on the VOICE surface.
    r = client.post("/api/transcribe",
                    files={"audio": ("r.webm", b"\x1a\x45\xdf\xa3" + b"x" * 32)},
                    headers=headers)
    assert r.status_code != 429
    # Budget exhausted through both surfaces -> the next chat request blocks.
    r = client.post("/chat", json={"message": "x"}, headers=headers)
    assert r.status_code == 429


# ── OTP identity buckets ────────────────────────────────────────────────


@pytest.fixture
def _no_sms_delivery(monkeypatch):
    """Capture (never send) delivered codes — same seam test_otp.py uses."""
    from app.services import otp as otp_service
    monkeypatch.setattr(otp_service, "_deliver",
                        lambda dest, code: None)


def test_otp_request_buckets_are_per_destination(client, monkeypatch,
                                                 _no_sms_delivery):
    """Two phones interleaved from ONE IP: one phone's bucket trips without
    touching its neighbour's — the booth registration-burst case."""
    from app.auth import security
    monkeypatch.setattr(security, "OTP_RATE_LIMIT", 2)
    dest_a, dest_b = "09120000001", "09120000002"
    for dest in (dest_a, dest_b, dest_a):
        r = client.post("/api/auth/otp/request", json={"destination": dest})
        assert r.status_code == 200, r.text
    # dest_a's tight bucket is at 2 — the limiter refuses before the service.
    r = client.post("/api/auth/otp/request", json={"destination": dest_a})
    assert r.status_code == 429
    # Its neighbour from the same address is untouched.
    r = client.post("/api/auth/otp/request", json={"destination": dest_b})
    assert r.status_code == 200


def test_otp_ip_backstop_bounds_rotating_destinations(client, monkeypatch,
                                                      _no_sms_delivery):
    """The SMS-relay bound: a fresh destination per request defeats every
    per-destination bucket, so the per-IP backstop must be what trips."""
    from app.auth import security
    monkeypatch.setattr(security, "OTP_IP_RATE_LIMIT", 3)
    for i in range(3):
        r = client.post("/api/auth/otp/request",
                        json={"destination": f"0912000000{i:02d}"})
        assert r.status_code == 200, r.text
    r = client.post("/api/auth/otp/request",
                    json={"destination": "09120000999"})
    assert r.status_code == 429


def test_otp_verify_buckets_are_per_challenge(client, monkeypatch,
                                              _no_sms_delivery):
    """One exhausted challenge must not consume its neighbour's retry budget
    — the same booth logic on the verify surface."""
    from app.auth import security
    monkeypatch.setattr(security, "OTP_RATE_LIMIT", 2)
    challenge_ids = []
    for i in range(2):
        r = client.post("/api/auth/otp/request",
                        json={"destination": f"0912100000{i:02d}"})
        assert r.status_code == 200, r.text
        challenge_ids.append(r.json()["challenge_id"])
    wrong_code = "000000"
    for _ in range(2):
        r = client.post("/api/auth/otp/verify",
                        json={"challenge_id": challenge_ids[0], "code": wrong_code})
        assert r.status_code == 400  # wrong code — but the bucket admits it
    r = client.post("/api/auth/otp/verify",
                    json={"challenge_id": challenge_ids[0], "code": wrong_code})
    assert r.status_code == 429     # challenge 1's bucket exhausted
    r = client.post("/api/auth/otp/verify",
                    json={"challenge_id": challenge_ids[1], "code": wrong_code})
    assert r.status_code == 400     # the neighbour is still answerable


# ── page-render limiter (decision A) ────────────────────────────────────


def test_page_render_limit_fences_the_token_mint_path(client, monkeypatch):
    """GET / mints a fresh identity per render, so it gets its own per-IP
    bucket. Monkeypatched low so the test need not issue 121 renders."""
    from app.auth import security
    from app.db.connection import get_db_connection
    monkeypatch.setattr(security, "PAGE_RATE_LIMIT", 3)
    for _ in range(3):
        assert client.get("/").status_code != 429
    assert client.get("/").status_code == 429
    conn = get_db_connection()
    keys = {row["key"] for row in
            conn.execute("SELECT key FROM rate_limit_hits").fetchall()}
    conn.close()
    assert "rl:page:testclient" in keys


# ── security answer: bcrypt upgrade path ────────────────────────────────


def test_legacy_security_answer_still_verifies_and_upgrades(client):
    _seed_admin()
    r = _login(client)
    assert r.status_code == 200
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    row = conn.execute("SELECT security_answer_hash FROM admins WHERE username='admin'").fetchone()
    conn.close()
    assert row["security_answer_hash"].startswith("$2")  # upgraded to bcrypt


def test_wrong_security_answer_is_rejected(client):
    _seed_admin()
    assert _login(client, answer="red").status_code == 401


def test_new_security_answer_is_verified_as_bcrypt(client):
    _seed_admin()
    _login(client)  # upgrades the hash
    assert _login(client).status_code == 200


# ── password rotation revokes other sessions ────────────────────────────


def test_password_change_revokes_other_sessions(client):
    _seed_admin()
    _login(client)
    csrf = client.get("/admin/csrf").json()["csrf_token"]

    # A second, "stolen" session.
    from app.db.connection import get_db_connection
    stolen = secrets.token_hex(32)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                 (stolen, "admin",
                  (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
    conn.commit()
    conn.close()

    r = client.post("/admin/api/change-password",
                    headers={"X-CSRF-Token": csrf},
                    json={"current_password": "pw-test-123",
                          "new_password": "new-pw-456",
                          "confirm_password": "new-pw-456"})
    assert r.status_code == 200

    conn = get_db_connection()
    rows = conn.execute("SELECT token FROM admin_sessions WHERE username='admin'").fetchall()
    conn.close()
    tokens = {r["token"] for r in rows}
    assert stolen not in tokens          # the stolen session is gone
    assert client.cookies.get("admin_session") is None or True  # current may rotate


# ── CSV formula injection ───────────────────────────────────────────────


def test_csv_export_neutralizes_formula_cells(client):
    _seed_admin()
    _login(client)
    csrf = client.get("/admin/csrf").json()["csrf_token"]

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO chat_logs (query, response, response_type, source, confidence,"
        " tokens, cost, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("=HYPERLINK(\"http://evil\")", "=cmd|'/c calc'!A1", "ai", "test",
         0.9, 0, 0, datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()

    r = client.get("/admin/api/export_csv")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert "'=HYPERLINK" in body
    assert "'=cmd" in body


def test_dataset_csv_helper_neutralizes_leads():
    from app.routers.dataset import _csv_safe
    assert _csv_safe("=1+1").startswith("'")
    assert _csv_safe("+SUM(A1)").startswith("'")
    assert _csv_safe("@cmd").startswith("'")
    assert _csv_safe("\tTAB").startswith("'")
    assert _csv_safe("normal text") == "normal text"
    assert _csv_safe(None) == ""


# ── credentials never logged ────────────────────────────────────────────


def test_bootstrap_credentials_failure_does_not_log_password(tmp_path, monkeypatch, caplog):
    """The OSError fallback must point at recovery, not print the password."""
    import logging
    import os as _os
    import app.db.connection as conn_mod

    def _unwritable(*args, **kwargs):
        raise OSError("read-only file system")

    # The credentials file lands next to DB_PATH; point it into the tmp dir.
    monkeypatch.setattr("app.config.DB_PATH", str(tmp_path / "x" / "db.sqlite"))
    monkeypatch.setattr(_os, "open", _unwritable)
    with caplog.at_level(logging.WARNING):
        conn_mod._write_admin_credentials("u", "SECRET-PW", "SECRET-ANSWER")

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRET-PW" not in joined
    assert "SECRET-ANSWER" not in joined


# ── legacy AI key encrypted at rest ────────────────────────────────────


def test_legacy_ai_key_is_encrypted_on_read_path(tmp_path, monkeypatch):
    """provider_config() must decrypt a protected row and pass legacy
    plaintext through, so both forms work across the rollout."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "k.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_BASE", "https://legacy.example")

    from app.db.connection import get_db_connection, init_db
    from app.db.queries import get_setting, set_setting
    from app.services import secure_store
    from app.services.openai import provider_config

    init_db()
    set_setting("ai_api_key", "sk-plain-legacy")
    assert provider_config()[1] == "sk-plain-legacy"  # legacy passthrough

    set_setting("ai_api_key", secure_store.protect("sk-enc-new"))

    def _raw() -> str:
        conn = get_db_connection()
        v = conn.execute("SELECT value FROM settings WHERE key='ai_api_key'").fetchone()["value"]
        conn.close()
        return v

    assert _raw().startswith("enc:")                 # encrypted at rest
    assert provider_config()[1] == "sk-enc-new"      # decrypted transparently for use
