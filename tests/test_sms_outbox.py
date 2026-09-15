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


def test_a_business_refusal_keeps_asking_row_by_row(temp_env, monkeypatch):
    """A gateway-RETURNED refusal is an answer, not an outage: the round
    must keep walking the rows exactly as before — only the gateway being
    UNREACHABLE stops it."""
    from app.services import sms as sms_service
    from app.services.sms import SmsError
    temp_env.record("asanak", "invite", "09120000000", "888")
    temp_env.record("asanak", "invite", "09120000001", "889")

    asked = []

    def refuses(msgid):
        asked.append(msgid)
        raise SmsError(detail="the gateway said no", code=1014)

    monkeypatch.setattr(sms_service, "asanak_status", refuses)
    summary = temp_env.poll_deliveries()

    assert len(asked) == 2, "a refusal answers its row; the next row is still asked"
    assert summary["asked"] == 0
    assert temp_env.status_counts()["queued"] == 2


def test_a_transport_failure_stops_the_round_not_just_the_row(temp_env, monkeypatch):
    """The hang (2026-09): a blackholed gateway costs TIMEOUT_SECONDS per
    queued row, serially, on every boot. The first transport-level failure
    must end the round; the unasked rows stay queued for a healthier one."""
    import socket
    import urllib.error
    from app.services import sms as sms_service
    for msgid in ("901", "902", "903"):
        temp_env.record("asanak", "invite", "09120000000", msgid)

    asked = []

    def blackholed(msgid):
        asked.append(msgid)
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(sms_service, "asanak_status", blackholed)
    summary = temp_env.poll_deliveries()

    assert len(asked) == 1, "the first transport failure must end the round"
    assert summary["asked"] == 0 and summary["candidates"] == 3
    assert temp_env.status_counts()["queued"] == 3


def test_the_real_transport_signal_stops_the_round_too(temp_env, monkeypatch):
    """sms._http_post converts a network failure into
    SmsError(code=TRANSPORT_FAILED) — the shape the unmocked path actually
    raises. The poller must read that sentinel, not only raw socket errors."""
    from app.services import sms as sms_service
    from app.services.sms import SmsError, TRANSPORT_FAILED
    temp_env.record("asanak", "invite", "09120000000", "911")
    temp_env.record("asanak", "invite", "09120000001", "912")

    asked = []

    def unreachable(msgid):
        asked.append(msgid)
        raise SmsError(detail="ارتباط با سامانه پیامک برقرار نشد.",
                       code=TRANSPORT_FAILED)

    monkeypatch.setattr(sms_service, "asanak_status", unreachable)
    summary = temp_env.poll_deliveries()

    assert len(asked) == 1
    assert summary["asked"] == 0 and summary["candidates"] == 2
    assert temp_env.status_counts()["queued"] == 2


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
                       # Aware UTC: verify_admin's compare_now() answers an
                       # aware expiry with aware-UTC now, so the seed holds
                       # on any host — the old naive utcnow()+12h seed read
                       # as already expired on a +03:30 dev machine.
                       (datetime.datetime.now(datetime.timezone.utc)
                        + datetime.timedelta(hours=12)).isoformat()))
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
