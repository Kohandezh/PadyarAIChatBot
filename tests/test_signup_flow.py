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


# ── /chat and /api/auth/profile enforcement ──────────────────────────────

DATASET = [("faq-hours", "ساعت کاری", "نمایشگاه هر روز از ۹ صبح تا ۱۸ باز است.", "")]
CHAT_BODY = {"message": "ساعت کاری نمایشگاه چیست؟", "lang": "fa"}


@pytest.fixture()
def gated_app(tmp_path, monkeypatch):
    """Real app + registration switched on + a Tier-1 answer, no network."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "signup-chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app as fastapi_app
    with TestClient(fastapi_app) as boot:
        from app.db.queries import set_setting
        set_setting("registration_enabled", "true")
        conn = get_db_connection()
        conn.execute("DELETE FROM dataset")
        conn.execute("DELETE FROM questions")
        for entry_id, title, text, video in DATASET:
            conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                         " VALUES (?, ?, ?, ?)", (entry_id, title, text, video))
        conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                     " VALUES (?, ?, '')", (CHAT_BODY["message"], "faq-hours"))
        conn.commit()
        conn.close()
        from app.services import search
        search.load_dataset_internal()
        yield fastapi_app
    search.load_dataset_internal()


@pytest.fixture()
def chat_client(gated_app):
    from app.auth.security import generate_chat_token
    c = TestClient(gated_app)
    c.headers.update({"Origin": "http://localhost",
                      "X-Chat-Token": generate_chat_token(),
                      "User-Agent": "KioskBrowser/1.0"})
    return c


def _complete_row(phone="09120000099"):
    from app.services.conversations import upsert_visitor
    return upsert_visitor(first_name="کامل", last_name="کاربر", phone=phone,
                          job="خبرنگار / رسانه", position="کارشناس",
                          interests="هوش مصنوعی")


def _incomplete_row(phone="09120000098"):
    from app.services.conversations import upsert_visitor
    return upsert_visitor(first_name="ناقص", last_name="کاربر", phone=phone,
                          job="", position="", interests="")


def _session_cookie(client, visitor_id):
    from app.auth import visitor as visitor_auth
    token = visitor_auth.mint(visitor_id)
    client.cookies.delete(visitor_auth.VISITOR_COOKIE_NAME)
    client.cookies.set(visitor_auth.VISITOR_COOKIE_NAME, token)


def test_chat_refuses_an_incomplete_signup(chat_client):
    _session_cookie(chat_client, _incomplete_row())
    r = chat_client.post("/chat", json=CHAT_BODY)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "signup_incomplete"


def test_chat_serves_a_complete_signup(chat_client):
    _session_cookie(chat_client, _complete_row())
    r = chat_client.post("/chat", json=CHAT_BODY)
    assert r.status_code == 200


def test_profile_refuses_until_complete_then_validates(client, outbox):
    _signed_in(client, outbox)
    r = client.post("/api/auth/profile", json={
        "job": "خبرنگار / رسانه", "position": "کارشناس",
        "interests": "هوش مصنوعی"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "signup_incomplete"
    for key, value in (("name", "زهرا کریمی"), ("job", "خبرنگار / رسانه"),
                       ("position", "کارشناس"), ("interests", "هوش مصنوعی")):
        assert client.post("/api/signup/answer",
                           json={"key": key, "value": value}).status_code == 200
    bad = client.post("/api/auth/profile", json={
        "job": "دانش‌آموز", "position": "کارشناس", "interests": "هوش مصنوعی"})
    assert bad.status_code == 400 and bad.json()["detail"]
    good = client.post("/api/auth/profile", json={
        "job": "سرمایه‌گذار", "position": "مدیر بخش",
        "interests": "سرمایه‌گذاری و جذب سرمایه"})
    assert good.status_code == 200
