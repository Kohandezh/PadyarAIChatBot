"""The SMS outbox and its delivery poller (migrations/0023).

A 200 from Asanak means QUEUED. "Queued" is not "arrived", and until this
change the msgid that could prove either way lived only in a log row nobody
read. The scenarios:

- every send path writes one outbox row, destination masked, msgid kept;
- a dev-outbox send (no msgid) is recorded as `unknown`, never polled;
- the poller turns the gateway's success word into `delivered`, keeps any
  other word as `queued` with the code recorded for the operator, and closes
  wordless rows older than the window as `unknown`;
- the admin can ask for a refresh on demand and read the ledger.
"""
import datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "outbox.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()  # the dev-send test writes a settings row
    from app.services import sms_outbox
    sms_outbox.ensure_table()
    return sms_outbox


def test_record_keeps_the_msgid_and_masks_the_destination(temp_env):
    row_id = temp_env.record("asanak", "invite", "09120000000", "5316257402",
                             reference="lead-1")
    assert row_id
    rows = temp_env.list_messages()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "queued" and row["msgid"] == "5316257402"
    assert "09120000000" not in row["destination"], "a raw number must not sit in the ledger"
    assert row["destination"].startswith("0912") and "*" in row["destination"]


def test_a_msgidless_send_is_unknown_not_forever_queued(temp_env):
    temp_env.record("dev", "invite", "09120000000", "")
    counts = temp_env.status_counts()
    assert counts["unknown"] == 1 and counts["queued"] == 0


def test_poll_turns_the_success_word_into_delivered(temp_env, monkeypatch):
    from app.services import sms as sms_service
    temp_env.record("asanak", "invite", "09120000000", "111")
    temp_env.record("asanak", "invite", "09120000001", "222")
    answers = {"111": {"meta": {"status": 200}, "data": {"status": 6}},
               "222": {"meta": {"status": 200}, "data": {"status": 20}}}
    monkeypatch.setattr(sms_service, "asanak_status",
                        lambda msgid: answers[msgid])

    summary = temp_env.poll_deliveries()

    assert summary["asked"] == 2 and summary["delivered"] == 1
    by_msgid = {r["msgid"]: r for r in temp_env.list_messages()}
    assert by_msgid["111"]["status"] == "delivered"
    assert by_msgid["111"]["status_checked_at"]
    # Status 20 is not a failure word — it is "not the success word". The row
    # stays queued with the code where the operator can see it.
    assert by_msgid["222"]["status"] == "queued"
    assert "20" in by_msgid["222"]["status_detail"]


def test_poll_closes_wordless_rows_after_the_window(temp_env, monkeypatch):
    from app.db.connection import get_db_connection
    from app.services import sms as sms_service
    temp_env.record("asanak", "invite", "09120000000", "333")
    past = (datetime.datetime.utcnow()
            - datetime.timedelta(hours=temp_env.POLL_WINDOW_HOURS + 1)).isoformat()
    conn = get_db_connection()
    try:
        conn.execute("UPDATE sms_messages SET created_at = ?", (past,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(sms_service, "asanak_status",
                        lambda msgid: pytest.fail("a stale row must not be asked"))

    summary = temp_env.poll_deliveries()

    assert summary["closed_unknown"] == 1
    assert temp_env.status_counts()["unknown"] == 1


def test_a_gateway_failure_is_survived_and_left_queued(temp_env, monkeypatch):
    from app.services import sms as sms_service
    temp_env.record("asanak", "invite", "09120000000", "444")

    def boom(msgid):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(sms_service, "asanak_status", boom)
    summary = temp_env.poll_deliveries()

    assert summary["asked"] == 0
    assert temp_env.status_counts()["queued"] == 1


def test_the_dev_invite_send_lands_in_the_outbox(temp_env, tmp_path, monkeypatch):
    import app.services.sms as sms
    from app.db.queries import set_setting
    set_setting("sms_provider", "dev")
    monkeypatch.setattr(sms, "_DEV_OUTBOX", str(tmp_path / "outbox.log"))

    msgid = sms.send_invite_link("+989120000000", "https://x/edit/tok", "ref-1")

    assert msgid is None
    rows = temp_env.list_messages(kind="invite")
    assert len(rows) == 1 and rows[0]["provider"] == "dev"
    assert rows[0]["status"] == "unknown"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import secrets
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "outbox_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        from app.services import sms_outbox
        sms_outbox.ensure_table()
        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('oadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "oadmin",
                      # 12h: verify_admin compares naive expiry against LOCAL
                      # now, so a +03:30 dev machine reads utcnow()+1h as
                      # expired (CI runs UTC and is unaffected).
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=12)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def test_the_panel_can_refresh_and_read_the_ledger(admin_client, monkeypatch):
    from app.services import sms_outbox
    from app.services import sms as sms_service
    sms_outbox.record("asanak", "invite", "09120000000", "555")
    monkeypatch.setattr(sms_service, "asanak_status",
                        lambda msgid: {"data": {"status": 6}})

    r = admin_client.post("/admin/api/sms/refresh-statuses")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["delivered"] == 1

    r = admin_client.get("/admin/api/sms/outbox")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["delivered"] == 1
    assert body["messages"][0]["status"] == "delivered"


def test_the_ledger_refuses_an_anonymous_caller(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "outbox_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/api/sms/outbox").status_code in (401, 403)
        assert anon.post("/admin/api/sms/refresh-statuses").status_code in (401, 403)
