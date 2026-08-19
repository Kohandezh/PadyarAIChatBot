"""Secure storage for the SMS gateway credentials, and the Asanak protocol.

The customer's requirement, in their words: every field Asanak needs must be
in the admin panel, and what is saved must go into the environment file and
not be readable there. These tests hold that line from both ends —

  * nothing readable is written (settings table, .env, HTTP responses, logs);
  * what IS written round-trips, so the gateway receives the real password.

Everything runs against a throwaway SQLite DB and a throwaway .env under
tmp_path. No test here touches the real .env (the autouse fixture in
conftest.py redirects it for the whole suite) and none of them opens a socket:
the HTTP seam `app.services.sms._http_post` is monkeypatched.

Credentials below are obvious fakes.
"""
import datetime
import os
import re
import secrets
import stat

import pytest
from fastapi.testclient import TestClient

PASSWORD = "fake-gateway-password-9999"
API_KEY = "fake-api-key-not-real"
USERNAME = "fake-account"
SOURCE = "98200049"

ENV_SAMPLE = """\
# INOTEX chatbot configuration
OPENAI_API_KEY=sk-fake-openai-key

# Keep this comment: an operator's own note
COOKIE_SECURE=false
ASANAK_USERNAME=old-username
"""


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A throwaway .env with other keys and comments already in it."""
    import app.config as config
    path = tmp_path / ".env"
    path.write_text(ENV_SAMPLE, encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", str(path))
    # The "back up before the first modification" guard is per process.
    from app.services import secure_store
    secure_store._backed_up.discard(str(path))
    return path


@pytest.fixture
def client(tmp_path, monkeypatch, env_file):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
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
        "username": USERNAME,
        "password": "",
        "api_key": "",
        "source": SOURCE,
        "url": "",
        "status_url": "",
        "credit_url": "",
        "trim": True,
        "send_to_blacklist": True,
        "sms_host": "",
    }
    body.update(overrides)
    return body


def _raw_setting(key):
    """The value as it actually sits in the table — no decryption."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row["value"] if row else ""


def _capture_sends(monkeypatch, status=200, body='{"meta":{"status":200,"message":"success"},"data":[123456]}'):
    """Replace BOTH HTTP seams. Returns the list of (url, payload) sent.

    There are two now — form-encoded for sendsms/msgstatus/getcredit, JSON for
    the template endpoint — and both must be stubbed. Stubbing only the first
    let a test reach the real gateway over the network as soon as a template id
    was present in the developer's `.env`, because `setting()` falls back to the
    environment when the throwaway database has no row.
    """
    from app.services import sms
    calls = []

    def fake_post(url, payload):
        calls.append((url, dict(payload)))
        return status, body

    monkeypatch.setattr(sms, "_http_post", fake_post)
    monkeypatch.setattr(sms, "_http_post_json", fake_post)
    return calls


# ── The secret is not stored in readable form ───────────────────────────

def test_saved_password_is_not_stored_in_plaintext(client, env_file):
    _login(client)
    assert client.post("/admin/api/sms",
                       json=_payload(password=PASSWORD, api_key=API_KEY)).status_code == 200

    stored_password = _raw_setting("sms_asanak_password")
    stored_key = _raw_setting("sms_asanak_api_key")
    env_text = env_file.read_text(encoding="utf-8")

    for blob in (stored_password, stored_key, env_text):
        assert PASSWORD not in blob
        assert API_KEY not in blob
    assert stored_password.startswith("enc:")
    assert stored_key.startswith("enc:")
    # The .env carries the ciphertext, not the secret.
    assert "ASANAK_PASSWORD=enc:" in env_text
    assert "ASANAK_API_KEY=enc:" in env_text


def test_non_secret_fields_stay_readable(client, env_file):
    """Username, sender and URLs are not secrets — an operator must see them."""
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD, sms_host="inotex.example.com"))

    env_text = env_file.read_text(encoding="utf-8")
    assert "ASANAK_USERNAME=%s" % USERNAME in env_text
    assert "ASANAK_SOURCE=%s" % SOURCE in env_text
    assert "OTP_SMS_HOST=inotex.example.com" in env_text
    assert _raw_setting("sms_asanak_username") == USERNAME


def test_ciphertext_differs_every_time(client):
    """Same password twice must not produce the same stored blob."""
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    first = _raw_setting("sms_asanak_password")
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    assert _raw_setting("sms_asanak_password") != first


# ── ...and it round-trips all the way to the gateway ────────────────────

def test_saved_password_reaches_the_gateway_intact(client, monkeypatch):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    calls = _capture_sends(monkeypatch)
    from app.services import sms
    sms.send("asanak", "09121234567", "hello")

    assert len(calls) == 1
    url, payload = calls[0]
    assert payload["password"] == PASSWORD          # decrypted for the gateway
    assert payload["username"] == USERNAME
    assert payload["source"] == SOURCE
    assert url == sms.ASANAK_DEFAULT_URL


def test_encrypted_env_value_is_used_when_the_table_is_empty(client, monkeypatch):
    """A headless install restarted from .env alone still works."""
    from app.services import sms
    from app.services.secure_store import protect
    _login(client)
    client.post("/admin/api/sms", json=_payload())          # no secret typed
    monkeypatch.setenv("ASANAK_PASSWORD", protect(PASSWORD))

    calls = _capture_sends(monkeypatch)
    sms.send("asanak", "09121234567", "hello")
    assert calls[0][1]["password"] == PASSWORD


def test_a_pre_existing_plaintext_value_still_works(client, monkeypatch):
    """An install that already had credentials must not break on upgrade."""
    from app.db.queries import set_setting
    from app.services import sms
    _login(client)
    client.post("/admin/api/sms", json=_payload())
    # Exactly what an older install has in its settings table.
    set_setting("sms_asanak_password", PASSWORD)
    assert _raw_setting("sms_asanak_password") == PASSWORD

    assert sms.is_configured("asanak") is True
    calls = _capture_sends(monkeypatch)
    sms.send("asanak", "09121234567", "hello")
    assert calls[0][1]["password"] == PASSWORD


def test_plaintext_env_value_still_works(client, monkeypatch):
    """Same for an install whose credentials only ever lived in .env."""
    from app.services import sms
    _login(client)
    client.post("/admin/api/sms", json=_payload())
    monkeypatch.setenv("ASANAK_PASSWORD", PASSWORD)

    calls = _capture_sends(monkeypatch)
    sms.send("asanak", "09121234567", "hello")
    assert calls[0][1]["password"] == PASSWORD


def test_an_undecryptable_secret_fails_closed(client, monkeypatch):
    """A token from another install must never be sent as the password."""
    from app.db.queries import set_setting, get_setting
    from app.services import sms, secure_store
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    # Simulate a restored DB / rotated SECRET_KEY: same ciphertext, new key.
    secure_store._fernet_cache = None
    monkeypatch.setattr("app.auth.security.get_app_secret", lambda: "a-different-app-secret")
    assert get_setting("sms_asanak_password", "") == ""
    assert sms.is_configured("asanak") is False

    # And a value that merely starts with "enc:" is left alone, not eaten.
    set_setting("whitelabel_footer_text", "enc:this is just text")
    assert get_setting("whitelabel_footer_text", "") == "enc:this is just text"


# ── Nothing readable leaves the server ──────────────────────────────────

def test_get_endpoint_leaks_no_secret(client):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD, api_key=API_KEY))

    r = client.get("/admin/api/sms")
    assert r.status_code == 200
    body = r.json()
    assert body["has_password"] is True
    assert body["has_api_key"] is True
    assert PASSWORD not in r.text
    assert API_KEY not in r.text
    assert "enc:" not in r.text                     # not even the ciphertext
    assert set(body) & {"password", "api_key"} == set()


def test_a_gateway_failure_never_logs_the_secret(client, monkeypatch, caplog):
    from app.services import sms
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    _capture_sends(monkeypatch, status=401,
                   body='{"meta":{"status":1008,"message":"Username or Password is not valid"}}')
    with caplog.at_level("DEBUG"):
        with pytest.raises(sms.SmsError):
            sms.send("asanak", "09121234567", "hello")
    assert PASSWORD not in caplog.text


# ── The .env file itself ────────────────────────────────────────────────

def test_env_keeps_its_other_keys_and_comments(client, env_file):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    text = env_file.read_text(encoding="utf-8")
    assert "# INOTEX chatbot configuration" in text
    assert "# Keep this comment: an operator's own note" in text
    assert "OPENAI_API_KEY=sk-fake-openai-key" in text
    assert "COOKIE_SECURE=false" in text
    # An existing key is updated in place, not duplicated.
    assert text.count("ASANAK_USERNAME=") == 1
    assert "ASANAK_USERNAME=old-username" not in text


def test_env_file_is_not_world_readable(client, env_file):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    mode = stat.S_IMODE(os.stat(env_file).st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_env_is_backed_up_before_the_first_edit(client, env_file, tmp_path):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))

    backups = list((tmp_path / "backups").glob(".env.backup.*"))
    assert len(backups) == 1
    # The backup is the file as it was BEFORE the save...
    assert backups[0].read_text(encoding="utf-8") == ENV_SAMPLE
    # ...and is not readable by anyone else either.
    assert stat.S_IMODE(os.stat(backups[0]).st_mode) & 0o077 == 0


def test_a_missing_env_file_is_created(client, tmp_path, monkeypatch):
    import app.config as config
    path = tmp_path / "fresh" / ".env"
    path.parent.mkdir()
    monkeypatch.setattr(config, "ENV_FILE", str(path))
    _login(client)

    assert client.post("/admin/api/sms", json=_payload(password=PASSWORD)).json()["env_file"] is True
    assert "ASANAK_PASSWORD=enc:" in path.read_text(encoding="utf-8")


def test_an_unwritable_env_does_not_break_saving(client, tmp_path, monkeypatch):
    """The settings table is still authoritative — the panel must not 500."""
    import app.config as config
    monkeypatch.setattr(config, "ENV_FILE", str(tmp_path / "no-such-dir" / ".env"))
    _login(client)

    r = client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    assert r.status_code == 200
    assert r.json()["env_file"] is False            # reported, not hidden
    assert client.get("/admin/api/sms").json()["has_password"] is True


def test_env_write_is_atomic_and_leaves_no_temp_files(client, env_file, tmp_path):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    assert list(tmp_path.glob(".env.*.tmp")) == []


# ── Asanak protocol (verified against the published API docs) ───────────

def test_send_posts_the_documented_parameters(client, monkeypatch):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    calls = _capture_sends(monkeypatch)

    from app.services import sms
    sms.send("asanak", "09121234567", "hello")
    payload = calls[0][1]
    assert set(payload) == {"username", "password", "source", "destination", "message"}
    assert payload["destination"] == "09121234567"


def test_optional_parameters_are_only_sent_when_switched_off(client, monkeypatch):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD, trim=False,
                                                send_to_blacklist=False))
    calls = _capture_sends(monkeypatch)

    from app.services import sms
    sms.send("asanak", "09121234567", "hello")
    payload = calls[0][1]
    assert payload["trim"] == "false"
    assert payload["send_to_blacklist"] == "0"


def test_success_is_read_from_meta_status_not_from_http_200(client, monkeypatch):
    """HTTP 200 with a refusal in the body must NOT count as delivered."""
    from app.services import sms
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    _capture_sends(monkeypatch, status=200,
                   body='{"meta":{"status":1006,"message":"Credit is not enough"}}')

    with pytest.raises(sms.SmsError) as excinfo:
        sms.send("asanak", "09121234567", "hello")
    assert excinfo.value.code == 1006
    assert "اعتبار" in excinfo.value.detail


@pytest.mark.parametrize("http_status,code,expected", [
    (401, 1008, "نام کاربری یا رمز عبور"),
    (400, 1008, "اعتبارسنجی"),
    (400, 1014, "لینک"),
    (401, 1015, "منقضی"),
    (402, 1006, "اعتبار حساب"),
    (402, 1005, "نمایندگی"),
    (403, 1013, "تبلیغاتی"),
    (406, 1002, "فرستنده"),
    (406, 1010, "مقصد"),
    (412, 1009, "روزانه"),
    (429, 429, "درخواست"),
    (500, 1004, "سرور"),
])
def test_every_documented_error_code_gets_its_persian_message(
        client, monkeypatch, http_status, code, expected):
    from app.services import sms
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    _capture_sends(monkeypatch, status=http_status,
                   body='{"meta":{"status":%d,"message":"x"}}' % code)

    with pytest.raises(sms.SmsError) as excinfo:
        sms.send("asanak", "09121234567", "hello")
    assert expected in excinfo.value.detail, excinfo.value.detail
    # A visitor still sees only the generic sentence.
    assert str(excinfo.value) == sms._VISITOR_MESSAGE


def test_the_legacy_plaintext_response_still_counts_as_success(client, monkeypatch):
    """The older panel host answers with a bare message id, not JSON."""
    from app.services import sms
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    _capture_sends(monkeypatch, status=200, body="123456")

    sms.send("asanak", "09121234567", "hello")      # must not raise


def test_the_send_url_is_configurable(client, monkeypatch):
    """An install still on panel.asanak.com keeps working."""
    from app.services import sms
    _login(client)
    legacy = "https://panel.asanak.com/webservice/v2rest/sendsms"
    client.post("/admin/api/sms", json=_payload(password=PASSWORD, url=legacy))
    calls = _capture_sends(monkeypatch)

    sms.send("asanak", "09121234567", "hello")
    assert calls[0][0] == legacy


# ── Credit check ────────────────────────────────────────────────────────

def test_credit_endpoint_reports_the_balance(client, monkeypatch):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    calls = _capture_sends(monkeypatch, body='{"meta":{"status":200},"data":{"credit":928}}')

    r = client.get("/admin/api/sms/credit")
    assert r.status_code == 200
    assert r.json() == {"credit": 928}
    from app.services import sms
    assert calls[0][0] == sms.ASANAK_CREDIT_URL
    # It proves the credentials without sending anything.
    assert set(calls[0][1]) == {"username", "password"}
    assert PASSWORD not in r.text


def test_credit_endpoint_reports_an_expired_web_service_password(client, monkeypatch):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    _capture_sends(monkeypatch, status=401, body='{"meta":{"status":1015,"message":"x"}}')

    r = client.get("/admin/api/sms/credit")
    assert r.status_code == 502
    assert "منقضی" in r.json()["detail"]


def test_credit_endpoint_needs_admin_and_a_configured_gateway(client):
    assert client.get("/admin/api/sms/credit").status_code == 401
    _login(client)
    client.post("/admin/api/sms", json=_payload(provider="dev"))
    assert client.get("/admin/api/sms/credit").status_code == 400


# ── The admin test-send surfaces the gateway's own reason ───────────────

def test_test_send_shows_the_mapped_gateway_reason(client, monkeypatch):
    _login(client)
    client.post("/admin/api/sms", json=_payload(password=PASSWORD))
    _capture_sends(monkeypatch, status=400, body='{"meta":{"status":1014,"message":"x"}}')

    r = client.post("/admin/api/sms/test", json={"destination": "09121234567"})
    assert r.status_code == 502
    assert "لینک" in r.json()["detail"]
    assert PASSWORD not in r.text


# ── The form has every field the gateway uses ───────────────────────────

def test_the_form_has_an_input_for_every_gateway_field(client):
    _login(client)
    html = client.get("/secure-panel-inotex/settings/sms").text
    for field in ("sms-username", "sms-password", "sms-api-key",
                  "sms-source", "sms-url", "sms-status-url", "sms-credit-url",
                  "sms-trim", "sms-send-to-blacklist", "sms-host"):
        assert re.search(r'id="%s"' % field, html), f"missing input #{field}"


def test_every_provider_has_a_tab_and_a_pane(client):
    """The gateway is chosen with tabs, not a <select>.

    The dropdown this replaced rendered identically to the text inputs beside
    it, so the single most consequential setting on the page was invisible to
    the operator. Each gateway must have both a tab to click and a pane to
    hold its fields — that pairing is what makes adding a gateway a
    template-only change.
    """
    _login(client)
    html = client.get("/secure-panel-inotex/settings/sms").text

    assert '<select id="sms-provider"' not in html, "the select came back"
    for provider in ("asanak", "dev"):
        assert re.search(r'data-provider="%s"' % provider, html), f"no tab for {provider}"
        assert re.search(r'data-provider-pane="%s"' % provider, html), f"no pane for {provider}"

    # The Asanak credential inputs must live inside the Asanak pane, so
    # switching gateways cannot leave another gateway's fields on screen.
    pane = html.split('data-provider-pane="asanak"', 1)[1].split('data-provider-pane="dev"', 1)[0]
    for field in ("sms-username", "sms-password", "sms-source"):
        assert 'id="%s"' % field in pane, f"#{field} is outside the asanak pane"
    # The two secrets are still write-only password inputs with no value.
    for field in ("sms-password", "sms-api-key"):
        tag = re.search(r'<input[^>]*id="%s"[^>]*>' % field, html).group(0)
        assert 'type="password"' in tag and "value=" not in tag
