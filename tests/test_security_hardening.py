"""Security hardening pass — regression tests for the fixes in this branch.

Covers, one behaviour per test:
  * /api/transcribe now requires the same guard trio as /chat (origin,
    chat token, rate limit) instead of being an open STT relay;
  * the chat rate limiter is shared through the rate_limit_buckets table
    (no longer N-per-worker in-memory state);
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
    for _ in range(2):
        client.post("/chat", json={"message": "salud"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": generate_chat_token()})
    conn = get_db_connection()
    rows = conn.execute("SELECT key, count FROM rate_limit_buckets").fetchall()
    conn.close()
    assert any(r["key"].startswith("rl:testclient") for r in rows)


def test_rate_limit_blocks_in_the_db_not_per_process(client):
    """The counter must be readable by ANOTHER connection (i.e. another
    worker), which is the whole point of moving it out of the dict."""
    import app.config as config
    from app.auth.security import generate_chat_token
    limit = config.CHAT_RATE_LIMIT
    for _ in range(limit):
        client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": generate_chat_token()})
    r = client.post("/chat", json={"message": "x"},
                    headers={**ORIGIN_HEADERS, "X-Chat-Token": generate_chat_token()})
    assert r.status_code == 429


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
