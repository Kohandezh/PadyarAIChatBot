"""Admin panel — registration / SMS settings page and its API.

The customer's hard requirement is that the SMS gateway credentials never
become visible: not in the page source, not in an API response, not anywhere
the browser can reach. These tests hold that line, plus the two behaviours the
page exists for — the registration on/off switch and the test-SMS control.

Each test runs against a throwaway SQLite DB (never the real chat_history.db)
and logs in by inserting a real admin session row, because the page route
checks the session cookie directly rather than through a FastAPI dependency.
"""
import datetime
import re
import secrets

import pytest
from fastapi.testclient import TestClient

PASSWORD = "s3cret-gateway-password"
API_KEY = "ak_live_do_not_leak_me"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
    """Create a real admin session and put its cookie on the client."""
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, "tester", expiry.isoformat()),
    )
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    # Admin mutations require a CSRF token. These tests exercise the
    # endpoints, not the CSRF guard itself (see tests/test_csrf.py).
    from app.auth.csrf import token_for_session
    client.headers.update({'X-CSRF-Token': token_for_session(token)})
    return token


def _payload(**overrides):
    body = {
        "enabled": False,
        "provider": "asanak",
        "username": "acme",
        "password": "",
        "api_key": "",
        "source": "98200049",
        "url": "",
        "status_url": "",
        "trim": True,
        "sms_host": "",
    }
    body.update(overrides)
    return body


# ── Auth ────────────────────────────────────────────────────────────────

def test_page_requires_admin(client):
    """Anonymous visitors are bounced to the login page, never shown the form."""
    r = client.get("/secure-panel-inotex/settings/sms", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/secure-panel-inotex/login"


def test_api_requires_admin(client):
    assert client.get("/admin/api/sms").status_code == 401
    assert client.post("/admin/api/sms", json=_payload()).status_code == 401
    assert client.post("/admin/api/sms/test", json={"destination": "09121234567"}).status_code == 401


def test_page_renders_for_admin(client):
    _login(client)
    r = client.get("/secure-panel-inotex/settings/sms")
    assert r.status_code == 200
    html = r.text
    assert 'id="sms-form"' in html
    assert 'id="sms-enabled"' in html          # the registration switch
    assert 'id="sms-test-form"' in html        # the test-SMS control
    assert '/static/admin/js/settings_sms.js' in html


def test_sidebar_links_to_the_page(client):
    _login(client)
    html = client.get("/secure-panel-inotex/settings/ai").text
    assert '/secure-panel-inotex/settings/sms' in html


# ── Secrets never leave the server ──────────────────────────────────────

def test_get_never_returns_the_stored_secrets(client):
    _login(client)
    assert client.post("/admin/api/sms",
                       json=_payload(password=PASSWORD, api_key=API_KEY)).status_code == 200

    r = client.get("/admin/api/sms")
    assert r.status_code == 200
    body = r.json()
    # Only the booleans — never the values, under any key.
    assert body["has_password"] is True
    assert body["has_api_key"] is True
    assert PASSWORD not in r.text
    assert API_KEY not in r.text


def test_page_source_never_contains_a_stored_secret(client):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD, api_key=API_KEY))
    html = client.get("/secure-panel-inotex/settings/sms").text
    assert PASSWORD not in html
    assert API_KEY not in html
    # The secret inputs exist but ship with no value attribute at all —
    # an empty field means "keep what is stored".
    for field in ("sms-password", "sms-api-key"):
        tag = re.search(r'<input[^>]*id="%s"[^>]*>' % field, html)
        assert tag, f"missing input #{field}"
        assert 'type="password"' in tag.group(0)
        assert "value=" not in tag.group(0)


def test_blank_secret_fields_keep_the_stored_ones(client):
    """Editing the sender number must not wipe the password nobody re-typed."""
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD, api_key=API_KEY))

    client.post("/admin/api/sms", json=_payload(source="98200050"))  # secrets blank
    body = client.get("/admin/api/sms").json()
    assert body["source"] == "98200050"
    assert body["has_password"] is True
    assert body["has_api_key"] is True

    from app.db.queries import get_setting
    assert get_setting("sms_asanak_password", "") == PASSWORD
    assert get_setting("sms_asanak_api_key", "") == API_KEY


def test_no_secrets_stored_reports_false(client):
    _login(client)
    client.post("/admin/api/sms", json=_payload())
    body = client.get("/admin/api/sms").json()
    assert body["has_password"] is False
    assert body["has_api_key"] is False


# ── The registration on/off switch ──────────────────────────────────────

def test_registration_switch_round_trips(client):
    _login(client)

    assert client.post("/admin/api/sms", json=_payload(enabled=True)).status_code == 200
    assert client.get("/admin/api/sms").json()["enabled"] is True
    # The public endpoint the chat UI reads must agree — that is what makes
    # the "بازدید هوشمند" call to action appear.
    assert client.get("/api/auth/registration-status").json()["enabled"] is True

    assert client.post("/admin/api/sms", json=_payload(enabled=False)).status_code == 200
    assert client.get("/admin/api/sms").json()["enabled"] is False
    assert client.get("/api/auth/registration-status").json()["enabled"] is False


def test_registration_is_off_until_switched_on(client):
    _login(client)
    assert client.get("/admin/api/sms").json()["enabled"] is False


# ── Test SMS ────────────────────────────────────────────────────────────

def test_test_sms_rejects_an_invalid_number(client):
    _login(client)
    client.post("/admin/api/sms", json=_payload())
    r = client.post("/admin/api/sms/test", json={"destination": "not-a-phone"})
    assert r.status_code == 400


def test_test_sms_refuses_while_provider_is_dev(client):
    _login(client)
    client.post("/admin/api/sms", json=_payload(provider="dev"))
    r = client.post("/admin/api/sms/test", json={"destination": "09121234567"})
    assert r.status_code == 400
    # The refusal has to be ACTIONABLE. The provider is chosen by a tab strip,
    # not a labelled field, so naming the setting is not enough — the operator
    # has to be told where to click. This asserts the instruction survives.
    detail = r.json()["detail"]
    assert "آسانک" in detail
    assert "ذخیره" in detail


def test_test_sms_reports_the_real_gateway_error(client, monkeypatch):
    """A failure shows the gateway's own reason, not an invented success."""
    from app.services import sms as sms_service
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    def boom(destination, message, code=None):
        raise sms_service.SmsError("اعتبار حساب پیامکی کافی نیست.")

    monkeypatch.setitem(sms_service.PROVIDERS["asanak"], "send", boom)
    r = client.post("/admin/api/sms/test", json={"destination": "09121234567"})
    assert r.status_code == 502
    assert r.json()["detail"] == "اعتبار حساب پیامکی کافی نیست."


def test_test_sms_reports_success_with_a_masked_number(client, monkeypatch):
    from app.services import sms as sms_service
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    sent = []
    monkeypatch.setitem(sms_service.PROVIDERS["asanak"], "send",
                        lambda destination, message, code=None: sent.append((destination, message)))
    r = client.post("/admin/api/sms/test", json={"destination": "09121234567"})
    assert r.status_code == 200
    assert len(sent) == 1
    # The full number never comes back in the response.
    assert "*" in r.json()["destination"]
    assert "09121234567" not in r.text
