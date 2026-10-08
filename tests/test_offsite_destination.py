"""The SFTP off-site destination, set in the admin panel (SPEC-H2).

H1 read the destination only from env vars and files on the server, so an
operator needed a shell on the server to set it. H2 moves it into
Infrastructure > Backups. This file pins the server side of that, with no
network and no Docker (tests/test_offsite_panel_live.py does the real server):

  * the door: admin session for read, save and test; CSRF on save and test;
  * the form: bad host, port, user, path, fingerprint, auth or secret is a
    plain Persian 400 and nothing is saved; an empty host removes the panel
    destination (Amendment 2), which is not bad input;
  * precedence: a panel destination beats OFFSITE_BACKUP_TARGET, an empty
    panel falls back to it, and the env target never reaches the browser;
  * secrets: stored as `enc:` tokens, never in a response, an audit row or a
    log line; an empty field keeps the stored one, `clear_secret` removes both;
  * every save and every test run is audited (who, outcome, no secret);
  * the N+1-th connection test in the window is refused, per admin.
"""
import base64
import datetime
import hashlib
import json
import logging
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

API = "/admin/api/infra/backups/offsite-settings"
TEST_API = f"{API}/test"
PAGE = "/secure-panel-admin/infrastructure/backups"

PASSWORD = "Throwaway-pw-" + secrets.token_hex(6)
KEY_BODY = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ" + secrets.token_hex(24)
KEY = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{KEY_BODY}\n-----END OPENSSH PRIVATE KEY-----"
FPR = "SHA256:" + base64.b64encode(hashlib.sha256(b"pinned").digest()).decode().rstrip("=")
CONTRACT_KEYS = {"host", "port", "user", "path", "auth", "fingerprint",
                 "password_saved", "private_key_saved", "source"}
SETTINGS_KEYS = ("offsite_sftp_host", "offsite_sftp_port", "offsite_sftp_user",
                 "offsite_sftp_path", "offsite_sftp_auth", "offsite_sftp_password",
                 "offsite_sftp_private_key", "offsite_sftp_host_fingerprint")


def _valid(**over):
    body = {"host": "backup.example.com", "port": 2222, "user": "backup",
            "path": "/upload/myevent", "auth": "password", "fingerprint": FPR,
            "password": PASSWORD}
    body.update(over)
    return body


def _persian(text):
    return any("؀" <= c <= "ۿ" for c in text or "")


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "offsite-panel.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    from app.db.connection import init_db
    init_db()
    yield tmp_path


def _session(username):
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    conn = get_db_connection()
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                 " security_question, security_answer_hash) VALUES (?,'x','y','q','z')",
                 (username,))
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                 (token, username,
                  (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
    conn.commit()
    conn.close()
    return token


def _signed_in(c, username="panel", csrf=True):
    from app.auth.csrf import token_for_session
    token = _session(username)
    # The server re-issues the session cookie on every answer (sliding
    # expiry), so a second sign-in must drop the first admin's cookie.
    c.cookies.clear()
    c.cookies.set("admin_session", token)
    if csrf:
        c.headers["X-CSRF-Token"] = token_for_session(token)
    return c


@pytest.fixture
def anon(app_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(anon):
    return _signed_in(anon)


def _rows():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'offsite_sftp_%'"
                            ).fetchall()
    finally:
        conn.close()
    return {r["key"]: r["value"] for r in rows}


def _log_text(app_db):
    """Every row of the four log tables, as one string."""
    import app.config as config
    conn = sqlite3.connect(config.LOGS_DB_PATH)
    try:
        out = []
        for table in ("app_logs", "audit_logs", "security_events", "service_events"):
            try:
                out += [json.dumps(list(r), ensure_ascii=False, default=str)
                        for r in conn.execute(f"SELECT * FROM {table}")]
            except sqlite3.OperationalError:
                pass
        return "\n".join(out)
    finally:
        conn.close()


def _audit(event):
    import app.config as config
    conn = sqlite3.connect(config.LOGS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_logs WHERE event_name = ? ORDER BY id", (event,))]
    finally:
        conn.close()


# ── The door ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,route", [("get", API), ("post", API), ("post", TEST_API)])
def test_every_offsite_route_refuses_an_anonymous_caller(anon, method, route):
    res = getattr(anon, method)(route, json=_valid()) if method == "post" else anon.get(route)
    assert res.status_code == 401, route
    assert _rows() == {}


def test_a_save_without_the_csrf_token_is_refused_and_saves_nothing(anon):
    _signed_in(anon, csrf=False)

    res = anon.post(API, json=_valid())

    assert res.status_code == 403
    assert _rows() == {}


def test_a_test_run_without_the_csrf_token_is_refused(anon, monkeypatch):
    from app.services import offsite_destination
    ran = []
    monkeypatch.setattr(offsite_destination, "try_connection",
                        lambda: ran.append(1) or {"ok": True, "reason": "ok", "message": "x"})
    _signed_in(anon, csrf=False)

    assert anon.post(TEST_API).status_code == 403
    assert ran == []


def test_with_a_session_and_the_csrf_token_every_route_answers(client, monkeypatch):
    from app.services import offsite_destination
    monkeypatch.setattr(offsite_destination, "try_connection",
                        lambda: {"ok": True, "reason": "ok", "message": "x"})

    assert client.get(API).status_code == 200
    assert client.post(API, json=_valid()).status_code == 200
    assert client.post(TEST_API).status_code == 200


# ── Saving ──────────────────────────────────────────────────────────────

def test_an_empty_install_reports_no_destination(client):
    body = client.get(API).json()

    assert set(body) == CONTRACT_KEYS
    assert body["source"] == "none"
    assert body["port"] == 22
    assert body["password_saved"] is False and body["private_key_saved"] is False


def test_a_valid_save_returns_the_saved_settings_and_only_says_a_secret_is_saved(client):
    res = client.post(API, json=_valid())

    assert res.status_code == 200
    body = res.json()
    assert set(body) == CONTRACT_KEYS
    assert body == {"host": "backup.example.com", "port": 2222, "user": "backup",
                    "path": "/upload/myevent", "auth": "password", "fingerprint": FPR,
                    "password_saved": True, "private_key_saved": False,
                    "source": "panel"}
    assert client.get(API).json() == body


def test_the_password_and_the_key_are_stored_encrypted(client):
    client.post(API, json=_valid(private_key=KEY))

    rows = _rows()
    assert rows["offsite_sftp_password"].startswith("enc:")
    assert rows["offsite_sftp_private_key"].startswith("enc:")
    assert PASSWORD not in json.dumps(rows) and KEY_BODY not in json.dumps(rows)


def test_the_port_defaults_to_22_and_a_trailing_slash_is_dropped(client):
    body = client.post(API, json=_valid(port=None, path="/upload/myevent/")).json()

    assert body["port"] == 22
    assert body["path"] == "/upload/myevent"


def test_a_port_typed_as_text_or_in_persian_digits_is_accepted(client):
    assert client.post(API, json=_valid(port="2222")).json()["port"] == 2222
    assert client.post(API, json=_valid(port="۲۲۲۲")).json()["port"] == 2222


def test_the_host_is_stored_in_lower_case(client):
    assert client.post(API, json=_valid(host="Backup.Example.COM")).json()["host"] == \
        "backup.example.com"


BAD = [
    ("host", "bad host"), ("host", "-oProxyCommand=sh"), ("host", "host;rm"),
    ("host", "a" * 300), ("host", 123),
    ("port", "abc"), ("port", 0), ("port", 70000), ("port", -1), ("port", True),
    ("port", "22a"), ("port", 22.5),
    ("user", ""), ("user", "bad user"), ("user", "-x"), ("user", "root;id"),
    ("user", "a" * 40), ("user", ["backup"]),
    ("path", ""), ("path", "upload/myevent"), ("path", "../x"), ("path", "/up/../x"),
    ("path", "/up load"), ("path", '/up"x'), ("path", "/up*"),
    ("fingerprint", ""), ("fingerprint", "SHA256:short"), ("fingerprint", "MD5:aa:bb:cc"),
    ("fingerprint", "SHA256:" + "!" * 43), ("fingerprint", FPR.lower()),
    ("fingerprint", f"256 {FPR} root@host (ED25519) extra SHA256:x"),
    ("auth", ""), ("auth", "both"),
    ("password", "line one\nline two"), ("password", "x" * 600), ("password", 12345678),
    ("private_key", "not a key"), ("private_key", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 me"),
    ("clear_secret", "yes"),
]


@pytest.mark.parametrize("field,value", BAD)
def test_bad_input_is_refused_with_a_plain_message_and_nothing_is_saved(client, field, value):
    client.post(API, json=_valid(host="old.example.com", password="Old-password-1"))
    before = _rows()

    res = client.post(API, json=_valid(**{field: value}))

    assert res.status_code == 400, (field, value)
    assert _persian(res.json()["detail"]), res.json()
    assert _rows() == before, "a refused save changed the stored settings"


@pytest.mark.parametrize("field,value,code", [
    ("host", "bad host", "bad_host"), ("port", "abc", "bad_port"),
    ("user", "", "missing_user"), ("user", "bad user", "bad_user"),
    ("path", "", "missing_path"), ("path", "upload/myevent", "relative_path"),
    ("path", "/up load", "bad_path"), ("auth", "both", "bad_auth"),
    ("fingerprint", "", "missing_fingerprint"), ("fingerprint", "SHA256:x", "bad_fingerprint"),
    ("password", "a\nb", "bad_password"), ("private_key", "not a key", "bad_key"),
])
def test_each_bad_field_gets_the_sentence_that_names_it(client, field, value, code):
    from app.services import offsite_destination

    res = client.post(API, json=_valid(**{field: value}))

    assert res.json()["detail"] == offsite_destination.MESSAGES[code]


@pytest.mark.parametrize("missing", ["user", "path", "fingerprint"])
def test_a_host_without_the_rest_of_the_form_is_bad_input(client, missing):
    body = _valid()
    del body[missing]

    res = client.post(API, json=body)

    assert res.status_code == 400
    assert _rows() == {}


def test_an_empty_host_removes_the_panel_destination(client):
    client.post(API, json=_valid(private_key=KEY))

    res = client.post(API, json={"host": "", "user": "ignored", "path": "relative"})

    assert res.status_code == 200
    assert res.json()["source"] == "none"
    assert res.json()["password_saved"] is False and res.json()["private_key_saved"] is False
    assert _rows() == {}


def test_an_empty_secret_field_keeps_the_stored_secret(client):
    from app.services import offsite_destination
    client.post(API, json=_valid())

    for keep in ({"password": ""}, {"password": None}, {}):
        body = _valid(user="backup2")
        body.pop("password")
        body.update(keep)
        assert client.post(API, json=body).json()["password_saved"] is True

    assert offsite_destination.stored().password == PASSWORD
    assert offsite_destination.stored().user == "backup2"


def test_a_new_secret_replaces_the_stored_one(client):
    from app.services import offsite_destination
    client.post(API, json=_valid())

    client.post(API, json=_valid(password="Second-password-2"))

    assert offsite_destination.stored().password == "Second-password-2"


def test_clear_secret_removes_both_secrets_and_keeps_the_destination(client):
    client.post(API, json=_valid(private_key=KEY))

    body = _valid(clear_secret=True)
    body.pop("password")
    res = client.post(API, json=body).json()

    assert res["password_saved"] is False and res["private_key_saved"] is False
    assert res["source"] == "panel" and res["host"] == "backup.example.com"
    assert "offsite_sftp_password" not in _rows()
    assert "offsite_sftp_private_key" not in _rows()


def test_clear_secret_with_a_new_secret_keeps_only_the_new_one(client):
    from app.services import offsite_destination
    client.post(API, json=_valid(private_key=KEY))

    res = client.post(API, json=_valid(password="Fresh-password-3", clear_secret=True)).json()

    assert res["password_saved"] is True and res["private_key_saved"] is False
    assert offsite_destination.stored().password == "Fresh-password-3"


@pytest.mark.parametrize("pasted", [KEY, KEY + "\n", KEY.replace("\n", "\r\n") + "\r\n",
                                    "  " + KEY + "\n\n"])
def test_a_private_key_is_kept_with_exactly_one_final_newline(client, pasted):
    from app.services import offsite_destination

    res = client.post(API, json=_valid(auth="key", password=None, private_key=pasted))

    assert res.status_code == 200
    assert offsite_destination.stored().private_key == KEY + "\n"


# ── Which destination wins ─────────────────────────────────────────────

ENV_TARGET = "sftp:envuser@env-only-host.example:/env-path"


def test_a_panel_destination_beats_the_env_target(client, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", ENV_TARGET)

    client.post(API, json=_valid())

    assert backup_offsite._target() == "sftp:backup@backup.example.com:2222:/upload/myevent"
    assert client.get(API).json()["source"] == "panel"
    assert backup_offsite.last_result_view([])["configured"] is True


def test_an_empty_panel_falls_back_to_the_env_target(client, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", ENV_TARGET)

    assert backup_offsite._target() == ENV_TARGET
    body = client.get(API).json()
    assert body["source"] == "env"
    assert body["host"] == "" and body["user"] == "" and body["path"] == ""


def test_removing_the_panel_destination_falls_back_to_the_env_target_again(client,
                                                                          monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", ENV_TARGET)
    client.post(API, json=_valid())

    client.post(API, json={"host": ""})

    assert backup_offsite._target() == ENV_TARGET
    assert client.get(API).json()["source"] == "env"


def test_no_destination_anywhere_means_none(client):
    from app.services import backup_offsite

    assert backup_offsite._target() == ""
    assert backup_offsite.last_result_view([])["configured"] is False


def test_the_env_target_never_reaches_the_browser(client, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", ENV_TARGET)

    for text in (client.get(API).text, client.get("/admin/api/infra/backups").text):
        assert "env-only-host" not in text and "envuser" not in text and "env-path" not in text


# ── Secrets never come back ────────────────────────────────────────────

def test_no_response_audit_row_or_log_line_ever_holds_a_secret(client, app_db, monkeypatch,
                                                              caplog):
    import subprocess

    from app.services import offsite_destination
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 255, stdout="",
                                           stderr="Permission denied (publickey).")

    monkeypatch.setattr(offsite_destination.subprocess, "run", fake_run)
    monkeypatch.setattr(offsite_destination, "_why_no_keys", lambda *a: "unreachable")
    caplog.set_level(logging.DEBUG)

    texts = [client.post(API, json=_valid(private_key=KEY)).text,
             client.get(API).text,
             client.post(API, json=_valid(auth="key", password=None)).text,
             client.post(API, json=_valid(host="bad host", password=PASSWORD,
                                          private_key=KEY)).text,
             client.post(TEST_API).text,
             client.get(PAGE).text,
             client.get("/admin/api/infra/backups").text,
             _log_text(app_db),
             caplog.text,
             json.dumps(seen)]

    for i, text in enumerate(texts):
        assert PASSWORD not in text, i
        assert KEY_BODY not in text, i
    saved = json.loads(texts[1])
    assert saved["password_saved"] is True and saved["private_key_saved"] is True


# ── Audit ───────────────────────────────────────────────────────────────

def test_a_save_is_audited_with_who_and_outcome(client):
    client.post(API, json=_valid())

    rows = _audit("admin.backup.offsite_settings.saved")
    assert len(rows) == 1
    assert rows[0]["actor"] == "panel" and rows[0]["outcome"] == "ok"
    meta = json.loads(rows[0]["metadata"])
    assert meta["method"] == "password" and meta["replaced"] == ["password"]
    # applog masks `user@host` like an e-mail address; host and path stay.
    assert meta["target"].endswith("@backup.example.com:2222:/upload/myevent")


def test_a_refused_save_is_audited_as_refused(client):
    client.post(API, json=_valid(port="abc"))

    rows = _audit("admin.backup.offsite_settings.saved")
    assert len(rows) == 1 and rows[0]["outcome"] == "refused"


def test_every_test_run_is_audited_with_its_outcome(client, monkeypatch):
    from app.services import offsite_destination
    answers = iter([{"ok": True, "reason": "ok", "message": "a"},
                    {"ok": False, "reason": "host_key", "message": "b"}])
    monkeypatch.setattr(offsite_destination, "try_connection", lambda: next(answers))

    client.post(TEST_API)
    client.post(TEST_API)

    rows = _audit("admin.backup.offsite_settings.tested")
    assert [r["outcome"] for r in rows] == ["ok", "failed"]
    assert all(r["actor"] == "panel" for r in rows)
    assert "host_key" in rows[1]["metadata"]


# ── Rate limit ──────────────────────────────────────────────────────────

def test_the_test_after_the_limit_is_refused_with_a_plain_message(client, monkeypatch):
    from app.services import offsite_destination
    ran = []
    monkeypatch.setattr(offsite_destination, "try_connection",
                        lambda: ran.append(1) or {"ok": True, "reason": "ok", "message": "x"})

    for _ in range(offsite_destination.TEST_LIMIT):
        assert client.post(TEST_API).status_code == 200
    res = client.post(TEST_API)

    assert res.status_code == 429
    assert _persian(res.json()["detail"])
    assert len(ran) == offsite_destination.TEST_LIMIT


def test_the_limit_is_per_admin_not_per_address(anon, monkeypatch):
    from app.services import offsite_destination
    monkeypatch.setattr(offsite_destination, "try_connection",
                        lambda: {"ok": True, "reason": "ok", "message": "x"})
    _signed_in(anon, "first")
    for _ in range(offsite_destination.TEST_LIMIT):
        anon.post(TEST_API)
    assert anon.post(TEST_API).status_code == 429

    _signed_in(anon, "second")

    assert anon.post(TEST_API).status_code == 200
