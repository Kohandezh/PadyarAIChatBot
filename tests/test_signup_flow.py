"""The signup flow's API contract: one question at a time, server-owned
order, every answer validated against the taxonomy before it is kept."""
import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_db_connection
from app.main import app
from app.services import otp as otp_service

DEST = "+989120000066"


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.headers.update({"Origin": "http://localhost",
                          "User-Agent": "pytest-agent/1.0"})
        yield c


@pytest.fixture(autouse=True)
def _no_ip_throttle(monkeypatch):
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    # The first test in this file never calls an OTP endpoint, but its
    # teardown sweeps otp_challenges — a table the service creates lazily
    # (first statement of every OTP entry point). Create it here so the
    # sweep works on a fresh database too (CI checks one out every run).
    otp_service.ensure_table()
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM otp_challenges WHERE destination = ?", (DEST,))
        conn.execute("DELETE FROM visitors WHERE phone = ?", (DEST,))
        conn.commit()
    finally:
        conn.close()


def _signed_in(client, outbox, **carried):
    r = client.post("/api/auth/otp/request", json={
        "destination": DEST, "first_name": "", "last_name": "",
        "job": carried.get("job", ""), "position": carried.get("position", ""),
        "interests": carried.get("interests", "")})
    assert r.status_code == 200, r.text
    cid = r.json()["challenge_id"]
    v = client.post("/api/auth/otp/verify",
                    json={"challenge_id": cid, "code": outbox[-1][1]})
    assert v.status_code == 200, v.text


def _row():
    from app.services import conversations
    return conversations.find_visitor_by_phone(DEST)


def test_next_is_401_for_anonymous(client):
    assert client.get("/api/signup/next").status_code == 401


def test_the_full_flow_collects_and_persists_each_answer(client, outbox):
    _signed_in(client, outbox)
    n1 = client.get("/api/signup/next?lang=fa").json()
    assert n1["step"]["key"] == "name"
    assert n1["step"]["prompt"] == "نام و نام خانوادگی شما چیست؟"
    r = client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    assert r.status_code == 200, r.text
    assert _row()["first_name"] == "زهرا"          # persisted per answer (REQ-004)
    for key, value in (("job", "خبرنگار / رسانه"),
                       ("position", "کارشناس"),
                       ("interests", "هوش مصنوعی، رسانه و محتوا")):
        assert client.post("/api/signup/answer",
                           json={"key": key, "value": value}).status_code == 200
    assert client.get("/api/signup/next").json() == {"complete": True}


def test_resume_starts_at_the_missing_field(client, outbox):
    """The row IS the state: name+job already stored ⇒ سمت asked next."""
    _signed_in(client, outbox, job="خبرنگار / رسانه")
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    assert client.get("/api/signup/next").json()["step"]["key"] == "position"


def test_invalid_answer_is_rejected_and_not_persisted(client, outbox):
    _signed_in(client, outbox)
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    r = client.post("/api/signup/answer", json={"key": "job", "value": "فضانورد"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_answer"
    assert _row()["job"] == ""


def test_student_cannot_hold_a_title(client, outbox):
    """The reported incident, as a regression test (REQ-002)."""
    _signed_in(client, outbox)
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    client.post("/api/signup/answer", json={"key": "job", "value": "دانش‌آموز"})
    r = client.post("/api/signup/answer", json={"key": "position", "value": "کارشناس"})
    assert r.status_code == 400
    # The refused POST changed nothing: REQ-009 auto-wrote سمت with the job
    # answer, and REQ-002 says a rejected answer never moves the profile.
    assert _row()["position"] == "سمت سازمانی ندارم"


def test_student_job_auto_answers_position(client, outbox):
    _signed_in(client, outbox)
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    r = client.post("/api/signup/answer", json={"key": "job", "value": "دانش‌آموز"})
    assert r.status_code == 200
    assert _row()["position"] == "سمت سازمانی ندارم"      # REQ-009
    assert r.json()["next"]["step"]["key"] == "interests"  # سمت never asked


def test_wrong_step_resyncs_the_client(client, outbox):
    _signed_in(client, outbox)
    r = client.post("/api/signup/answer", json={"key": "interests", "value": "هوش مصنوعی"})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "wrong_step" and body["step"]["key"] == "name"


def test_promote_drops_inconsistent_carried_fields(client, outbox):
    """The OTP request body is not a bypass: bad values never reach the row."""
    _signed_in(client, outbox, job="دانش‌آموز", position="کارشناس")
    row = _row()
    assert row["job"] == "دانش‌آموز"
    assert row["position"] == ""


def test_answer_after_complete_is_a_wrong_step(client, outbox):
    test_the_full_flow_collects_and_persists_each_answer(client, outbox)
    r = client.post("/api/signup/answer", json={"key": "job", "value": "دانش‌آموز"})
    assert r.status_code == 409
