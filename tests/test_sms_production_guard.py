"""The SMS settings page must not be able to brick the next restart.

The incident (2026-08-26): an operator saved the «حالت آزمایشی» tab on a
production install. The save wrote OTP_DELIVERY=dev into .env, prodcheck's
startup gate refused the next restart, and the service sat in a crash loop
while the panel still looked healthy. The block belongs in
sms.save_settings(), where BOTH stores are written from, so no caller can
reach .env around it. Staging and development keep the ability — that is how
the invite-by-SMS flow is exercised before a gateway exists.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "sms_guard.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "ENV_FILE", str(tmp_path / ".env"))
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('gadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "gadmin",
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _body(provider):
    return {
        "enabled": True, "provider": provider, "username": "u", "password": "",
        "api_key": "", "source": "", "template_id": "", "invite_text": "",
        "reject_text": "", "daily_budget": "0", "url": "",
        "status_url": "", "credit_url": "", "template_url": "",
        "trim": True, "send_to_blacklist": True, "sms_host": "",
    }


def test_production_refuses_dev_and_touches_no_store(admin_client, monkeypatch):
    import app.prodcheck
    monkeypatch.setattr(app.prodcheck, "is_production", lambda: True)
    # PADYAR_ENV stays unset: patching is_production avoids tripping the
    # startup gate's OTHER production checks inside a hermetic test DB.

    r = admin_client.post("/admin/api/sms", json=_body("dev"))
    assert r.status_code == 400
    assert "آزمایشی" in r.json()["detail"]

    # Nothing was written: neither the settings table nor .env.
    from app.db.queries import get_setting
    assert get_setting("sms_provider", "") != "dev"
    import os
    import app.config as config
    env_text = open(config.ENV_FILE).read() if os.path.exists(config.ENV_FILE) else ""
    assert "OTP_DELIVERY=dev" not in env_text, ".env would brick the next restart"


def test_production_still_accepts_asanak(admin_client, monkeypatch):
    import app.prodcheck
    monkeypatch.setattr(app.prodcheck, "is_production", lambda: True)

    r = admin_client.post("/admin/api/sms", json=_body("asanak"))
    assert r.status_code == 200
    from app.db.queries import get_setting
    assert get_setting("sms_provider", "") == "asanak"


def test_development_keeps_the_dev_tab(admin_client):
    r = admin_client.post("/admin/api/sms", json=_body("dev"))
    assert r.status_code == 200
    from app.db.queries import get_setting
    assert get_setting("sms_provider", "") == "dev"
