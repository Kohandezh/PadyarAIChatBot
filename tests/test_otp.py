"""OTP verification — service, API and security contract tests.

Runs against the real app database (WAL SQLite) exactly like the running
product; every challenge created here is isolated by its unguessable id and
cleaned up afterwards. Delivery is captured by monkeypatching the provider
seam — no file writes, no network, and the raw code never touches a log.
"""
import re
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import otp as otp_service
from app.db.connection import get_db_connection


@pytest.fixture()
def outbox(monkeypatch):
    """Capture delivered codes in memory instead of the dev outbox file."""
    sent = []
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture()
def client():
    # Origin + User-Agent on every request: /api/auth/otp/verify mints the
    # visitor session cookie, so it runs validate_request_origin like the rest
    # of the public surface. A real browser always sends both; TestClient
    # sends neither, so the fixture supplies them once.
    with TestClient(app) as c:
        c.headers.update({"Origin": "http://localhost",
                          "User-Agent": "pytest-agent/1.0"})
        yield c


@pytest.fixture(autouse=True)
def _no_ip_throttle(monkeypatch):
    """The suite fires dozens of requests from one client IP in seconds; the
    product's per-IP limiter (20/min) would throttle the tests themselves.
    Per-destination and per-challenge limits stay fully active — they are
    what these tests assert."""
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM otp_challenges WHERE destination LIKE '+9891200000%'")
        # A verified challenge is now promoted to a durable `visitors`
        # row (app/routers/otp.py). These three OTP files run against
        # the ambient database, so their test numbers have to be swept
        # out of that table too or they pile up in a real install.
        conn.execute("DELETE FROM visitors WHERE phone LIKE '+9891200000%'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()


DEST = "+989120000011"


def _request(client, dest=DEST):
    r = client.post("/api/auth/otp/request", json={"destination": dest})
    assert r.status_code == 200, r.text
    return r.json()


# ── Generation and storage ──────────────────────────────────────────────

def test_code_is_secure_length_and_never_stored_raw(client, outbox):
    data = _request(client)
    assert len(outbox) == 1
    dest, code = outbox[0]
    assert re.fullmatch(r"\d{6}", code)
    # The DB row keeps a keyed HMAC, never the raw code.
    conn = get_db_connection()
    row = conn.execute("SELECT code_hmac FROM otp_challenges WHERE id = ?",
                       (data["challenge_id"],)).fetchone()
    conn.close()
    assert row is not None
    assert code not in row["code_hmac"]
    assert re.fullmatch(r"[0-9a-f]{64}", row["code_hmac"])  # HMAC-SHA256 hex


def test_response_never_contains_the_code(client, outbox):
    data = _request(client)
    _, code = outbox[0]
    assert code not in str(data)


def test_destination_is_masked_in_response(client, outbox):
    data = _request(client)
    assert "*" in data["destination_masked"]
    assert DEST not in data["destination_masked"]


def test_invalid_destination_rejected(client, outbox):
    # Shape-invalid but long enough to pass schema validation → service 400.
    r = client.post("/api/auth/otp/request", json={"destination": "abcdefgh"})
    assert r.status_code == 400
    # Too-short input is rejected at the schema layer (422) — also a rejection.
    r2 = client.post("/api/auth/otp/request", json={"destination": "abc"})
    assert r2.status_code in (400, 422)


# ── Verification paths ──────────────────────────────────────────────────

def test_valid_code_verifies_once_then_replay_fails(client, outbox):
    data = _request(client)
    _, code = outbox[0]
    ok = client.post("/api/auth/otp/verify",
                     json={"challenge_id": data["challenge_id"], "code": code})
    assert ok.status_code == 200 and ok.json()["verified"] is True
    # Single-use: the same (valid) code must never verify twice.
    replay = client.post("/api/auth/otp/verify",
                         json={"challenge_id": data["challenge_id"], "code": code})
    assert replay.status_code == 400


def test_persian_and_arabic_digits_verify(client, outbox):
    data = _request(client)
    _, code = outbox[0]
    fa = code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    r = client.post("/api/auth/otp/verify",
                    json={"challenge_id": data["challenge_id"], "code": fa})
    assert r.status_code == 200


def test_wrong_code_generic_error(client, outbox):
    data = _request(client)
    r = client.post("/api/auth/otp/verify",
                    json={"challenge_id": data["challenge_id"], "code": "000000"})
    assert r.status_code == 400
    # Generic message; no hint about what specifically failed.
    assert "000000" not in r.text


def test_unknown_challenge_generic_error(client):
    r = client.post("/api/auth/otp/verify",
                    json={"challenge_id": "nonexistent-challenge-id", "code": "123456"})
    assert r.status_code == 400


def test_expired_code_rejected_server_side(client, outbox):
    data = _request(client)
    _, code = outbox[0]
    conn = get_db_connection()
    conn.execute("UPDATE otp_challenges SET expires_at = ? WHERE id = ?",
                 ((datetime.utcnow() - timedelta(seconds=1)).isoformat(), data["challenge_id"]))
    conn.commit()
    conn.close()
    r = client.post("/api/auth/otp/verify",
                    json={"challenge_id": data["challenge_id"], "code": code})
    assert r.status_code == 400


def test_attempt_limit_blocks_brute_force(client, outbox):
    data = _request(client)
    _, code = outbox[0]
    for _ in range(otp_service.OTP_MAX_ATTEMPTS):
        client.post("/api/auth/otp/verify",
                    json={"challenge_id": data["challenge_id"], "code": "999999"})
    # Even the CORRECT code is refused once the attempt budget is spent.
    r = client.post("/api/auth/otp/verify",
                    json={"challenge_id": data["challenge_id"], "code": code})
    assert r.status_code == 400


# ── Resend behavior ─────────────────────────────────────────────────────

def test_resend_respects_cooldown(client, outbox):
    data = _request(client)
    r = client.post("/api/auth/otp/resend", json={"challenge_id": data["challenge_id"]})
    assert r.status_code == 429  # immediately after request → still cooling down


def test_resend_invalidates_previous_code(client, outbox):
    data = _request(client)
    _, old_code = outbox[0]
    conn = get_db_connection()
    conn.execute("UPDATE otp_challenges SET last_sent_at = ? WHERE id = ?",
                 ((datetime.utcnow() - timedelta(seconds=otp_service.OTP_RESEND_COOLDOWN + 1)).isoformat(),
                  data["challenge_id"]))
    conn.commit()
    conn.close()
    r = client.post("/api/auth/otp/resend", json={"challenge_id": data["challenge_id"]})
    assert r.status_code == 200
    _, new_code = outbox[-1]
    old = client.post("/api/auth/otp/verify",
                      json={"challenge_id": data["challenge_id"], "code": old_code})
    if old_code != new_code:  # 1-in-a-million collision guard
        assert old.status_code == 400
    fresh = client.post("/api/auth/otp/verify",
                        json={"challenge_id": data["challenge_id"], "code": new_code})
    # Old failed above → one attempt consumed; fresh code must still work.
    assert fresh.status_code == 200


def test_destination_hourly_rate_limit(client, outbox):
    dest = "+989120000099"
    for _ in range(otp_service.OTP_DEST_HOURLY_LIMIT):
        _request(client, dest)
    r = client.post("/api/auth/otp/request", json={"destination": dest})
    assert r.status_code == 429
    conn = get_db_connection()
    conn.execute("DELETE FROM otp_challenges WHERE destination = ?", (dest,))
    conn.commit()
    conn.close()


# ── Page and audit hygiene ──────────────────────────────────────────────

def _without_html_comments(html: str) -> str:
    """What the BROWSER sees.

    An HTML comment is still bytes in the response, so asserting against
    raw text cannot tell "present" from "commented out" — this helper makes
    companion-presence assertions actually falsifiable.
    """
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def _otp_css() -> str:
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "static" / "otp" / "otp.css"
    return p.read_text(encoding="utf-8")


def test_verify_page_renders_the_digit_group_with_the_companion(client):
    r = client.get("/verify")
    assert r.status_code == 200
    html = r.text
    visible = _without_html_comments(html)

    # The companion is live again (owner request, 2026-08-24), desktop/tablet
    # only — otp.css hides it below 640px. Asserted on the comment-stripped
    # markup, so disabling it via HTML comments fails HERE on purpose.
    assert 'id="pet-canvas"' in visible
    assert "@media (max-width: 639px)" in _otp_css()

    assert 'id="otp-digits"' in visible          # semantic group
    assert 'role="group"' in visible
    assert 'aria-live="polite"' in visible
    assert "otp.PNG" not in html                # reference board is not shipped as UI


def test_status_endpoint_reconciles_timer(client, outbox):
    data = _request(client)
    r = client.get(f"/api/auth/otp/status/{data['challenge_id']}")
    assert r.status_code == 200
    body = r.json()
    assert 0 < body["expires_in"] <= otp_service.OTP_TTL_SECONDS
    assert "*" in body["destination_masked"]


def test_audit_log_never_contains_raw_code(client, outbox, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="PadyarAssistant"):
        data = _request(client)
        _, code = outbox[0]
        client.post("/api/auth/otp/verify",
                    json={"challenge_id": data["challenge_id"], "code": code})
    assert code not in caplog.text
    assert DEST not in caplog.text  # full destination never logged either
