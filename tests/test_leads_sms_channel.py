"""The SMS invite channel on a dev install: selectable, honestly labelled.

The scenario: the install's provider is «حالت آزمایشی» (dev) — no real gateway
line is configured yet — and the operator still needs to exercise the
invite-by-SMS path end to end. The send side already works in dev (the link is
appended to data/otp-dev-outbox.log by sms._send_link), so the capability
answer must not lock the channel; it must say where the message really goes.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "sms_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('smsadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "smsadmin",
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def test_dev_provider_leaves_the_sms_channel_selectable(admin_client):
    from app.db.queries import set_setting
    set_setting("sms_provider", "dev")

    r = admin_client.get("/admin/api/leads/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["sms_available"] is True, \
        "a dev install cannot select the SMS channel it is supposed to test"
    assert body["sms_reason"], "the dev outbox note must ride along, not be empty"


def test_dev_invite_link_lands_in_the_outbox_not_a_401(admin_client, tmp_path):
    """The whole point of calling dev 'available': the send path must actually
    succeed when the channel is picked. A link 'sent' in dev is written to the
    dev outbox; nothing is raised and the booth proceeds without a QR."""
    from app.services import sms as sms_service
    import app.services.sms as sms
    monkeyboxed = tmp_path / "outbox.log"
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sms, "_DEV_OUTBOX", str(monkeyboxed))
    try:
        msgid = sms_service.send_invite_link("+989120000000", "https://x/edit/tok", "ref-1")
        assert msgid is None, "dev must not pretend a gateway message id exists"
        text = monkeyboxed.read_text(encoding="utf-8")
        assert "https://x/edit/tok" in text and "invite" in text
    finally:
        monkey.undo()


def test_anonymous_caller_cannot_read_the_channel_settings(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "sms_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        r = anon.get("/admin/api/leads/settings")
        assert r.status_code in (401, 403)
