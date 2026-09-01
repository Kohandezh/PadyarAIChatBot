"""The `pwa_api` module: Bearer auth, the public companies directory, the
independent chat-token mint, per-visitor settings, and the personal QR contact
exchange. See docs/features/pwa-api/SPEC.md, section 12 ("استراتژی تست"), for
the exact coverage this file implements.

Each test gets its OWN throwaway SQLite database — the same idiom
tests/test_chat_visitor_identity.py and tests/test_company_profiles.py already
use: monkeypatch app.config.DB_PATH to a tmp_path file BEFORE the app's
lifespan runs (inside a `with TestClient(app):` block), so init_db() builds a
fresh schema and nothing here ever touches another test's rows or the real
database. That is also why no per-IP OTP throttle needs disabling here, unlike
tests/test_otp.py: every test's `rate_limit_hits` table starts empty.
"""
import pytest
from fastapi.testclient import TestClient

from app.services import otp as otp_service

BROWSER = {"Origin": "http://localhost", "User-Agent": "pytest-agent/1.0"}

WITHHELD_FIELDS = ("contact_name", "contact_position", "contact_mobile",
                   "email", "notes")

DEST_A = "+989120000501"
DEST_B = "+989120000502"

DATASET_QUESTION = "ساعت کاری نمایشگاه چیست؟"


# ── App / client plumbing ───────────────────────────────────────────────────

@pytest.fixture
def app_instance(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "pwa_api.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app as fastapi_app
    with TestClient(fastapi_app):          # lifespan: init_db()
        from app.db.queries import set_setting
        # No network in this file — every path exercised here is local.
        set_setting("openai_enabled", "false")
        yield fastapi_app


@pytest.fixture
def outbox(monkeypatch):
    """Capture delivered OTP codes in memory instead of the dev outbox file."""
    sent = []
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


def _new_client(app_instance):
    """A fresh browser: its own cookie jar, on the SAME app/database."""
    c = TestClient(app_instance)
    c.headers.update(BROWSER)
    return c


@pytest.fixture
def client(app_instance):
    return _new_client(app_instance)


def _register(client, outbox, dest, x_client_pwa=False, **profile):
    """Drive a real OTP registration to completion. Returns the verify JSON.

    `x_client_pwa` sets `X-Client: pwa` on the verify call only (REQ-002) —
    the header that puts `access_token` in the response.
    """
    body = {"destination": dest, "first_name": "علی", "last_name": "احمدی"}
    body.update(profile)
    r = client.post("/api/auth/otp/request", json=body)
    assert r.status_code == 200, r.text
    headers = {"X-Client": "pwa"} if x_client_pwa else {}
    v = client.post("/api/auth/otp/verify",
                    json={"challenge_id": r.json()["challenge_id"],
                          "code": outbox[-1][1]},
                    headers=headers)
    assert v.status_code == 200, v.text
    return v.json()


# ── A. Bearer auth ───────────────────────────────────────────────────────

def test_bearer_token_alone_signs_in_a_cookieless_client(app_instance, outbox):
    reg = _new_client(app_instance)
    data = _register(reg, outbox, DEST_A, x_client_pwa=True)
    token = data["access_token"]

    bearer_only = _new_client(app_instance)     # no cookie ever set on this jar
    r = bearer_only.get("/api/auth/session",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["signed_in"] is True


def test_verify_without_x_client_pwa_never_adds_access_token(app_instance, outbox):
    reg = _new_client(app_instance)
    data = _register(reg, outbox, DEST_B)
    assert "access_token" not in data
    # Today's exact shape, unchanged.
    assert set(data.keys()) == {"verified", "message", "profile"}


def test_a_valid_cookie_with_no_bearer_header_is_unchanged(app_instance, outbox):
    """Regression guard: cookie-only traffic (today's web chat) is untouched."""
    reg = _new_client(app_instance)
    _register(reg, outbox, DEST_A)

    r = reg.get("/api/auth/session")
    assert r.status_code == 200
    assert r.json()["signed_in"] is True


def test_cookie_wins_over_bearer_when_a_request_carries_both(app_instance, outbox):
    """SEC-002: the cookie is checked first and, if valid, Bearer is never read."""
    client_a = _new_client(app_instance)
    _register(client_a, outbox, DEST_A, job="مهندس / متخصص فنی")

    client_b = _new_client(app_instance)
    data_b = _register(client_b, outbox, DEST_B, x_client_pwa=True, job="خبرنگار / رسانه")
    token_b = data_b["access_token"]

    # client_a's jar carries A's valid cookie; force B's Bearer onto the SAME
    # request. The answer must still be A.
    r = client_a.get("/api/auth/session",
                     headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 200
    body = r.json()
    assert body["signed_in"] is True
    assert body["profile"]["job"] == "مهندس / متخصص فنی"


def test_a_garbage_bearer_token_is_anonymous_not_a_crash(app_instance):
    c = _new_client(app_instance)
    r = c.post("/api/chat-token/mint",
              headers={"Authorization": "Bearer this-was-never-issued"})
    assert r.status_code == 401


def test_a_bearer_only_request_never_plants_a_session_cookie(app_instance, outbox):
    """SEC-002 / this app's kiosk threat model: a request authenticated
    purely by Bearer must not cause resolve_visitor to Set-Cookie a fresh
    30-day session in whatever HTTP client sent the header. A shared browser
    that ever carries a bearer token (a debugging proxy, a WebView, a browser
    build of the PWA) would otherwise silently pick up that visitor's cookie
    for the next person at the same machine to inherit — exactly the bug
    otp_verify revokes the previous session to avoid. Caught in security
    review; see app/main.py's resolve_visitor for the fix."""
    reg = _new_client(app_instance)
    data = _register(reg, outbox, DEST_A, x_client_pwa=True)
    token = data["access_token"]

    bearer_only = _new_client(app_instance)   # empty cookie jar throughout
    r = bearer_only.get("/api/auth/session",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["signed_in"] is True
    assert "set-cookie" not in {k.lower() for k in r.headers.keys()}
    assert not bearer_only.cookies


# ── B. Public companies API ───────────────────────────────────────────────

def _seed_companies(app_instance):
    """Three companies, inserted straight into `companies` — no admin
    endpoint involved. Every withheld field is filled so a leak would be
    caught, and co-a's text is long enough to exercise short_text truncation.
    """
    from app.db.connection import get_db_connection
    long_text = "متن کامل معرفی شرکت آ. " * 20     # well over 200 chars
    rows = [
        ("co-a", "شرکت آ", "Company A", long_text,
         "نام محرمانه آ", "مدیر محرمانه آ", "09120000001", "a@example.com",
         "یادداشت محرمانه آ", "https://a.example.com", "هوش مصنوعی", "استارتاپ"),
        ("co-b", "شرکت ب", "Company B", "متن کوتاه ب",
         "نام محرمانه ب", "مدیر محرمانه ب", "09120000002", "b@example.com",
         "یادداشت محرمانه ب", "https://b.example.com", "رباتیک", "دانش‌بنیان"),
        ("co-c", "شرکت ج", "Company C", "متن کوتاه ج",
         "نام محرمانه ج", "مدیر محرمانه ج", "09120000003", "c@example.com",
         "یادداشت محرمانه ج", "https://c.example.com", "هوش مصنوعی", "استارتاپ"),
    ]
    conn = get_db_connection()
    try:
        for (cid, title, title_en, text, contact_name, contact_position,
             contact_mobile, email, notes, website, activity_field,
             company_type) in rows:
            conn.execute(
                "INSERT INTO companies (id, title, title_en, text, video_url,"
                " contact_name, contact_position, contact_mobile, email, notes,"
                " website, activity_field, company_type)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, title, title_en, text, f"/media/videos/{cid}.mp4",
                 contact_name, contact_position, contact_mobile, email, notes,
                 website, activity_field, company_type))
        conn.commit()
    finally:
        conn.close()
    return {"co-a": long_text}


def _all_keys(obj) -> set:
    """Every dict key anywhere in a JSON-shaped structure, walked recursively —
    a leak must be caught even if it ever nested somewhere unexpected."""
    keys = set()
    if isinstance(obj, dict):
        keys.update(obj.keys())
        for v in obj.values():
            keys.update(_all_keys(v))
    elif isinstance(obj, list):
        for v in obj:
            keys.update(_all_keys(v))
    return keys


def test_companies_list_has_no_auth_and_never_leaks_withheld_fields(app_instance):
    _seed_companies(app_instance)
    c = _new_client(app_instance)

    r = c.get("/api/companies")
    assert r.status_code == 200
    body = r.json()

    ids = {row["id"] for row in body["companies"]}
    assert {"co-a", "co-b", "co-c"} <= ids
    assert not (_all_keys(body) & set(WITHHELD_FIELDS))

    # The allowlist is additive, not just subtractive: a public field survives.
    co_a = next(row for row in body["companies"] if row["id"] == "co-a")
    assert co_a["website"] == "https://a.example.com"


def test_company_detail_never_leaks_withheld_fields_and_truncates_text(app_instance):
    texts = _seed_companies(app_instance)
    c = _new_client(app_instance)

    r = c.get("/api/companies/co-a")
    assert r.status_code == 200
    body = r.json()

    assert not (_all_keys(body) & set(WITHHELD_FIELDS))
    assert "short_text" in body
    assert body["short_text"]
    assert texts["co-a"].startswith(body["short_text"])
    assert len(body["short_text"]) < len(texts["co-a"])


def test_company_detail_404_for_an_unknown_id(app_instance):
    c = _new_client(app_instance)
    r = c.get("/api/companies/does-not-exist")
    assert r.status_code == 404


def test_companies_pagination_reports_has_more_correctly(app_instance):
    _seed_companies(app_instance)               # 3 companies
    c = _new_client(app_instance)

    page1 = c.get("/api/companies", params={"page_size": 2, "page": 1}).json()
    assert len(page1["companies"]) == 2
    assert page1["has_more"] is True

    page2 = c.get("/api/companies", params={"page_size": 2, "page": 2}).json()
    assert len(page2["companies"]) == 1
    assert page2["has_more"] is False


# ── C. Chat-token mint ────────────────────────────────────────────────────

def _seed_answerable_dataset(app_instance):
    from app.db.connection import get_db_connection
    from app.services import search
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM dataset")
        conn.execute("DELETE FROM questions")
        conn.execute(
            "INSERT INTO dataset (id, title, text, video_url) VALUES (?,?,?,?)",
            ("faq-hours", "ساعت کاری",
             "نمایشگاه هر روز از ۹ صبح تا ۱۸ باز است.", ""))
        conn.execute(
            "INSERT INTO questions (question, dataset_id, video_url)"
            " VALUES (?, ?, '')", (DATASET_QUESTION, "faq-hours"))
        conn.commit()
    finally:
        conn.close()
    search.load_dataset_internal()


def test_mint_without_any_session_is_401(app_instance):
    c = _new_client(app_instance)
    r = c.post("/api/chat-token/mint")
    assert r.status_code == 401


def test_mint_with_a_session_but_a_bad_origin_is_403(app_instance, outbox):
    reg = _new_client(app_instance)
    _register(reg, outbox, DEST_A)

    r = reg.post("/api/chat-token/mint",
                 headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_mint_with_a_cookie_session_and_valid_origin_works_end_to_end(
        app_instance, outbox):
    _seed_answerable_dataset(app_instance)
    reg = _new_client(app_instance)
    _register(reg, outbox, DEST_A)

    r = reg.post("/api/chat-token/mint")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "chat_token" in body and body["chat_token"]
    assert "expires_at" in body and body["expires_at"]

    chat = reg.post("/chat", json={"message": DATASET_QUESTION, "lang": "fa"},
                    headers={"X-Chat-Token": body["chat_token"]})
    assert chat.status_code == 200, chat.text


def test_mint_also_works_off_a_bearer_only_session(app_instance, outbox):
    reg = _new_client(app_instance)
    data = _register(reg, outbox, DEST_A, x_client_pwa=True)

    bearer_only = _new_client(app_instance)
    r = bearer_only.post("/api/chat-token/mint",
                        headers={"Authorization": f"Bearer {data['access_token']}"})
    assert r.status_code == 200
    assert "chat_token" in r.json()


# ── D. visitor_settings ───────────────────────────────────────────────────

def test_my_settings_without_a_session_is_401(app_instance):
    c = _new_client(app_instance)
    assert c.get("/api/me/settings").status_code == 401


def test_my_settings_default_shape_for_a_fresh_visitor(app_instance, outbox):
    reg = _new_client(app_instance)
    _register(reg, outbox, DEST_A)

    r = reg.get("/api/me/settings")
    assert r.status_code == 200
    assert r.json() == {"calendar": [], "contacts": [], "language": ""}


def test_adding_the_same_calendar_event_twice_is_idempotent(app_instance, outbox):
    reg = _new_client(app_instance)
    _register(reg, outbox, DEST_A)

    first = reg.post("/api/me/calendar", json={"event_id": "abc"})
    second = reg.post("/api/me/calendar", json={"event_id": "abc"})

    assert first.status_code == 200 and second.status_code == 200
    assert len(second.json()["calendar"]) == 1
    assert second.json()["calendar"][0]["event_id"] == "abc"


def test_removing_an_event_that_was_never_added_is_a_harmless_no_op(
        app_instance, outbox):
    reg = _new_client(app_instance)
    _register(reg, outbox, DEST_A)
    reg.post("/api/me/calendar", json={"event_id": "abc"})

    r = reg.delete("/api/me/calendar/never-added")
    assert r.status_code == 200
    assert len(r.json()["calendar"]) == 1     # unchanged


def test_removing_a_real_event_actually_removes_it(app_instance, outbox):
    reg = _new_client(app_instance)
    _register(reg, outbox, DEST_A)
    reg.post("/api/me/calendar", json={"event_id": "abc"})

    r = reg.delete("/api/me/calendar/abc")
    assert r.status_code == 200
    assert r.json()["calendar"] == []


def test_contacts_connect_links_both_sides_and_refuses_a_repeat(app_instance, outbox):
    from app.services import conversations

    a = _new_client(app_instance)
    _register(a, outbox, DEST_A)
    b = _new_client(app_instance)
    _register(b, outbox, DEST_B)

    id_a = conversations.find_visitor_by_phone(DEST_A)["id"]
    id_b = conversations.find_visitor_by_phone(DEST_B)["id"]

    qr = a.get("/api/me/qr")
    assert qr.status_code == 200
    payload = qr.json()["payload"]

    connect = b.post("/api/me/contacts/connect", json={"qr_payload": payload})
    assert connect.status_code == 200, connect.text

    a_settings = a.get("/api/me/settings").json()
    b_settings = b.get("/api/me/settings").json()
    assert len(a_settings["contacts"]) == 1 and len(b_settings["contacts"]) == 1
    assert a_settings["contacts"][0]["visitor_id"] == id_b
    assert b_settings["contacts"][0]["visitor_id"] == id_a

    repeat = b.post("/api/me/contacts/connect", json={"qr_payload": payload})
    assert repeat.status_code == 409


def test_contacts_connect_with_a_garbage_payload_is_400(app_instance, outbox):
    a = _new_client(app_instance)
    _register(a, outbox, DEST_A)

    r = a.post("/api/me/contacts/connect", json={"qr_payload": "not-a-real-payload"})
    assert r.status_code == 400


# ── E. Personal QR ────────────────────────────────────────────────────────

def test_qr_payload_validates_back_to_the_right_visitor(app_instance, outbox):
    from app.auth.security import validate_visitor_qr_payload
    from app.services import conversations

    a = _new_client(app_instance)
    _register(a, outbox, DEST_A)
    visitor_id = conversations.find_visitor_by_phone(DEST_A)["id"]

    r = a.get("/api/me/qr")
    assert r.status_code == 200
    body = r.json()
    assert body["payload"] and body["expires_at"]

    assert validate_visitor_qr_payload(body["payload"]) == visitor_id


def test_an_expired_qr_payload_is_refused_at_connect(app_instance, outbox, monkeypatch):
    import app.auth.security as security
    from app.services import conversations

    a = _new_client(app_instance)
    _register(a, outbox, DEST_A)
    visitor_a_id = conversations.find_visitor_by_phone(DEST_A)["id"]

    # Mint a payload that is already expired, using the real HMAC secret —
    # the same construction generate_visitor_qr_payload() uses, just with a
    # negative TTL, rather than sleeping in the test.
    monkeypatch.setattr(security, "QR_PAYLOAD_TTL_SECONDS", -10)
    expired_payload, _ = security.generate_visitor_qr_payload(visitor_a_id)

    b = _new_client(app_instance)
    _register(b, outbox, DEST_B)

    r = b.post("/api/me/contacts/connect", json={"qr_payload": expired_payload})
    assert r.status_code == 400
