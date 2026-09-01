"""The multi-field edit (migrations/0022): the contact edits their whole
Persian profile, and a no-change submission is a confirmation.

Scenarios:
- the open page's state carries every editable field plus the read-only
  booth/hall context;
- the payload whitelist refuses anything the form does not own;
- a change lands as ONE pending row holding both sides (old/new per field),
  and approval writes every field, syncing a changed mobile back to the lead;
- "correct as-is" is auto-approved, touches nothing, and completes the lead;
- revert puts back every field, not just the text.
"""
import datetime
import json
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "edit_fields.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('fadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "fadmin",
                      # 12h, not 1h: verify_admin compares a naive expiry
                      # against LOCAL now, so a non-UTC dev machine (+03:30
                      # here) reads utcnow()+1h as already expired. CI runs
                      # UTC either way.
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=12)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _seed(app_client, dataset_id="co-fields"):
    """A company with a full profile, a verified lead, a live invite, and the
    contact's session already open (cookie set on the client)."""
    from app.db.connection import get_db_connection
    from app.services import leads as svc
    svc.ensure_tables()
    lead_id = secrets.token_urlsafe(8)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO companies (id, title, text, contact_name, contact_mobile,"
            " email, website, address, province, activity_field, booth_number, hall)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dataset_id, "شرکت نمونه", "متن قدیمی", "نام قدیمی", "09120000000",
             "old@x.co", "https://old.co", "آدرس قدیمی", "تهران", "فناوری", "12B", "A"),
        )
        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " phone, phone_hash, status, created_at, challenge_id)"
            " VALUES (?, ?, ?, '', ?, ?, 'verified', ?, ?)",
            (lead_id, dataset_id, "شرکت نمونه", "09120000000", "hash",
             datetime.datetime.utcnow().isoformat(), "chal-1"),
        )
        conn.commit()
    finally:
        conn.close()
    invite = svc.create_invite(lead_id, dataset_id, "http://x")
    token = invite["invite_url"].rsplit("/edit/", 1)[1]
    r = app_client.post(f"/api/leads/edit/{token}/begin")
    assert r.status_code == 200, r.text
    return lead_id, dataset_id


def _fields(**over):
    base = {
        "title": "شرکت نمونه", "text": "متن قدیمی", "activity_field": "فناوری",
        "contact_name": "نام قدیمی", "contact_position": "",
        "contact_mobile": "09120000000",
        "email": "old@x.co", "website": "https://old.co", "company_phone": "",
        "fax": "", "address": "آدرس قدیمی", "province": "تهران",
    }
    base.update(over)
    return base


def test_state_carries_the_whole_profile_and_context(app_client):
    _, dataset_id = _seed(app_client)

    state = app_client.get("/api/leads/edit/state").json()
    assert state["company"] == "شرکت نمونه"
    assert state["fields"]["contact_mobile"] == "09120000000"
    assert state["fields"]["website"] == "https://old.co"
    assert state["context"] == {"booth_number": "12B", "hall": "A"}
    # Nothing the organizer owns ever reaches this page.
    assert "video_url" not in state["fields"] and "title_en" not in state["fields"]


def test_the_payload_whitelist_refuses_what_the_form_does_not_own(app_client):
    _seed(app_client)
    r = app_client.post("/api/leads/edit/submit",
                        json={"fields": {"title": "x", "text": "y", "booth_number": "99"}})
    assert r.status_code == 400
    r = app_client.post("/api/leads/edit/submit", json={"fields": {"title": 5}})
    assert r.status_code == 400
    r = app_client.post("/api/leads/edit/submit", json={"surprise": True})
    assert r.status_code == 400


def test_required_fields_and_formats_are_checked(app_client):
    _seed(app_client)
    r = app_client.post("/api/leads/edit/submit",
                        json={"fields": _fields(title=" ")})
    assert r.status_code == 400
    r = app_client.post("/api/leads/edit/submit",
                        json={"fields": _fields(contact_mobile="12345")})
    assert r.status_code == 400
    # Persian digits fold before the mobile check, so «۰۹۱۲…» works.
    r = app_client.post("/api/leads/edit/submit",
                        json={"fields": _fields(contact_mobile="۰۹۱۲۳۳۳۴۴۴۴")})
    assert r.status_code == 200 and r.json()["kind"] == "change"
    # A fresh link for the website format check (the session above is spent).
    _seed(app_client, "co-fields-2")
    r2 = app_client.post("/api/leads/edit/submit",
                         json={"fields": _fields(website="old.co")})
    assert r2.status_code == 400


def test_a_change_is_one_pending_row_with_both_sides(app_client):
    lead_id, dataset_id = _seed(app_client)

    r = app_client.post("/api/leads/edit/submit", json={"fields": _fields(
        text="متن تازه", contact_name="نام تازه", contact_mobile="09123334444")})
    assert r.status_code == 200 and r.json()["kind"] == "change"

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM dataset_edits WHERE dataset_id = ? AND status = 'pending'",
            (dataset_id,)).fetchone()
    finally:
        conn.close()
    assert row["edit_kind"] == "change"
    assert json.loads(row["new_values"]) == {
        "text": "متن تازه", "contact_name": "نام تازه",
        "contact_mobile": "09123334444"}
    assert json.loads(row["old_values"])["contact_name"] == "نام قدیمی"


def test_approval_writes_every_field_and_syncs_the_leads_mobile(app_client):
    lead_id, dataset_id = _seed(app_client)
    app_client.post("/api/leads/edit/submit", json={"fields": _fields(
        text="متن تازه", contact_mobile="09123334444", address="آدرس تازه")})

    edits = app_client.get("/admin/api/leads/edits").json()["edits"]
    edit_id = next(e["id"] for e in edits if e["dataset_id"] == dataset_id)
    r = app_client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": True})
    assert r.status_code == 200

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        co = conn.execute("SELECT text, address, contact_mobile FROM companies"
                          " WHERE id = ?", (dataset_id,)).fetchone()
        lead = conn.execute("SELECT phone, phone_hash FROM company_leads"
                            " WHERE id = ?", (lead_id,)).fetchone()
    finally:
        conn.close()
    assert co["text"] == "متن تازه" and co["address"] == "آدرس تازه"
    assert co["contact_mobile"] == "09123334444"
    assert lead["phone"] == "09123334444", \
        "the campaign's next SMS still goes to the number the company confirmed"
    assert lead["phone_hash"]


def test_confirm_as_is_completes_without_touching_the_company(app_client):
    lead_id, dataset_id = _seed(app_client)

    r = app_client.post("/api/leads/edit/submit", json={"confirm": True})
    assert r.status_code == 200 and r.json()["kind"] == "confirm"

    from app.db.connection import get_db_connection
    from app.services import leads as svc
    conn = get_db_connection()
    try:
        co = conn.execute("SELECT text, contact_name FROM companies"
                          " WHERE id = ?", (dataset_id,)).fetchone()
        row = conn.execute("SELECT status, edit_kind, reviewed_by FROM dataset_edits"
                           " WHERE dataset_id = ?", (dataset_id,)).fetchone()
    finally:
        conn.close()
    assert co["text"] == "متن قدیمی", "a confirm must not rewrite anything"
    assert row["status"] == "approved" and row["edit_kind"] == "confirm"
    assert svc.funnel()["completed"] == 1
    assert svc.funnel()["pending_review"] == 0


def test_an_unchanged_form_is_a_confirmation_not_an_empty_draft(app_client):
    _, dataset_id = _seed(app_client)

    r = app_client.post("/api/leads/edit/submit", json={"fields": _fields()})
    assert r.status_code == 200 and r.json()["kind"] == "confirm"

    edits = app_client.get("/admin/api/leads/edits").json()["edits"]
    assert not any(e["dataset_id"] == dataset_id for e in edits), \
        "an identical form must not sit in the reviewer's queue"


def test_confirm_supersedes_a_pending_draft_on_the_record(app_client):
    _, dataset_id = _seed(app_client)
    app_client.post("/api/leads/edit/submit", json={"fields": _fields(text="پیش‌نویس")})

    # A second contact session (fresh invite), confirming as-is.
    from app.services import leads as svc
    invite = svc.create_invite(_live_lead(dataset_id), dataset_id, "http://x")
    token = invite["invite_url"].rsplit("/edit/", 1)[1]
    assert app_client.post(f"/api/leads/edit/{token}/begin").status_code == 200
    r = app_client.post("/api/leads/edit/submit", json={"confirm": True})
    assert r.status_code == 200

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT status FROM dataset_edits WHERE dataset_id = ?",
                            (dataset_id,)).fetchall()
    finally:
        conn.close()
    assert all(r2["status"] != "pending" for r2 in rows), \
        "the abandoned draft still sits in the reviewer's queue"


def _live_lead(dataset_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return conn.execute("SELECT id FROM company_leads WHERE dataset_id = ?",
                            (dataset_id,)).fetchone()["id"]
    finally:
        conn.close()


def test_revert_puts_back_every_field(app_client):
    _, dataset_id = _seed(app_client)
    app_client.post("/api/leads/edit/submit", json={"fields": _fields(
        text="متن تازه", province="اصفهان")})

    edits = app_client.get("/admin/api/leads/edits").json()["edits"]
    edit_id = next(e["id"] for e in edits if e["dataset_id"] == dataset_id)
    app_client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": True})
    r = app_client.post(f"/admin/api/leads/edits/{edit_id}/revert")
    assert r.status_code == 200

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        co = conn.execute("SELECT text, province FROM companies WHERE id = ?",
                          (dataset_id,)).fetchone()
    finally:
        conn.close()
    assert co["text"] == "متن قدیمی" and co["province"] == "تهران"


def test_a_legacy_text_only_draft_still_shows_in_the_state(app_client):
    _, dataset_id = _seed(app_client)
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO dataset_edits (id, dataset_id, lead_id, old_text, new_text,"
            " status, created_at) VALUES ('e-legacy', ?, '', 'a', 'پیش‌نویس قدیمی',"
            " 'pending', ?)",
            (dataset_id, datetime.datetime.utcnow().isoformat()))
        conn.commit()
    finally:
        conn.close()

    state = app_client.get("/api/leads/edit/state").json()
    assert state["fields"]["text"] == "پیش‌نویس قدیمی"
    assert state["pending"] is True
