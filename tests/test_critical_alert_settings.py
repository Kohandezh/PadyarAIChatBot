"""Critical-alert settings on the SMS page: phone + credit threshold.

The systemd watchdog (deploy/watchdog/watchdog.py) reads exactly two settings
keys from the app database — `alert_critical_phone` (canonical `+98…`, empty =
alerts off) and `alert_credit_threshold_toman` (a digits string, default
"300000"). These tests hold the WRITER side of that reader–writer pair: the
admin API that stores both, so an operator can set them without touching SQL.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "alert.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('ops','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "ops",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        # Admin mutations require a CSRF token. These tests exercise the
        # endpoint, not the CSRF guard itself (see tests/test_csrf.py).
        from app.auth.csrf import token_for_session
        c.headers.update({'X-CSRF-Token': token_for_session(token)})
        yield c


def _body(**overrides):
    """A minimal body that passes the EXISTING gateway validation."""
    body = {"provider": "asanak", "enabled": False, "daily_budget": "0"}
    body.update(overrides)
    return body


# ── Defaults ────────────────────────────────────────────────────────────

def test_defaults(client):
    """A fresh install has no phone (alerts off) and the documented floor."""
    body = client.get("/admin/api/sms").json()
    assert body["alert_critical_phone"] == ""
    assert body["alert_credit_threshold_toman"] == "300000"


# ── Round-trip ──────────────────────────────────────────────────────────

def test_valid_roundtrip(client):
    """A local 09… number is stored and returned in the canonical +98 form
    the watchdog (and otp_challenges) expects — not the raw local form."""
    r = client.post("/admin/api/sms", json=_body(
        alert_critical_phone="09121234567",
        alert_credit_threshold_toman="250000"))
    assert r.status_code == 200
    body = client.get("/admin/api/sms").json()
    assert body["alert_critical_phone"] == "+989121234567"
    assert body["alert_credit_threshold_toman"] == "250000"


# ── Refusals ────────────────────────────────────────────────────────────

def test_invalid_phone_refused(client):
    r = client.post("/admin/api/sms", json=_body(alert_critical_phone="123"))
    assert r.status_code == 400
    assert "معتبر" in r.json()["detail"]


def test_invalid_threshold_refused(client):
    for bad in ("abc", "-5"):
        r = client.post("/admin/api/sms", json=_body(alert_credit_threshold_toman=bad))
        assert r.status_code == 400, bad


# ── Alerts can be switched off ──────────────────────────────────────────

def test_empty_phone_disables_alerts(client):
    """An empty phone must OVERWRITE a stored one, not be ignored: turning
    alerts off has to be possible from the same form."""
    client.post("/admin/api/sms", json=_body(alert_critical_phone="09121234567"))
    r = client.post("/admin/api/sms", json=_body(alert_critical_phone=""))
    assert r.status_code == 200
    assert client.get("/admin/api/sms").json()["alert_critical_phone"] == ""


# ── Persian digits ──────────────────────────────────────────────────────

def test_persian_digit_threshold(client):
    """int() accepts Persian digits; an operator with a Persian keyboard
    must not have to switch layouts to type a number."""
    r = client.post("/admin/api/sms", json=_body(alert_credit_threshold_toman="۲۵۰۰۰۰"))
    assert r.status_code == 200
    assert client.get("/admin/api/sms").json()["alert_credit_threshold_toman"] == "250000"
