"""Company profiles: the organizer's exhibitor data beside the chatbot's.

The relation under test — two tables now (migrations/0013_companies.sql
merged what used to be `dataset` + `company_profiles` into one `companies`
row per company):

    companies.id ◄── company_leads.dataset_id    (a VERIFIED capture event)

The load-bearing rule: profile data never creates a lead and never claims a
company. If an upsert left a company owned, the booth search would hide all
169 exhibitors the day the spreadsheet lands — so the scenario test below
walks list → upsert → list again and asserts the company is still in the
booth's search list.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "profiles_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        from app.services import leads as leads_svc
        leads_svc.ensure_tables()
        conn = get_db_connection()
        # Companies are their own table now (migrations/0013_companies.sql),
        # not `dataset` rows.
        conn.execute("INSERT INTO companies (id, title, text)"
                     " VALUES ('co-a', 'شرکت آ', 'متن آ'), ('co-b', 'شرکت ب', 'متن ب')")
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('padmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "padmin",
                      # LOCAL now, not utcnow: verify_admin compares a naive
                      # expiry against datetime.now() (app/db/timeutil.py
                      # compare_now), so a UTC timestamp expires instantly on
                      # any machine ahead of UTC — the whole fixture 401'd
                      # locally while passing on CI.
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def test_the_round_trip_and_the_ownership_rule(admin_client):
    from app.services import leads as svc

    # Empty book: both companies listed, neither with a profile.
    r = admin_client.get("/admin/api/company-profiles")
    assert r.status_code == 200
    rows = r.json()["companies"]
    assert {c["id"] for c in rows} == {"co-a", "co-b"}
    assert not any(c["has_profile"] for c in rows)

    # Fill one profile.
    r = admin_client.put("/admin/api/company-profiles/co-a", json={
        "contact_name": "بهار حمزه‌ای", "contact_position": "مدیر اجرایی",
        "contact_mobile": "09124308928", "email": "info@example.com",
        "website": "example.com", "province": "تهران",
        "activity_field": "هوش مصنوعی و داده",
    })
    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["contact_name"] == "بهار حمزه‌ای"

    # The row is back in the list, flagged; the other company still has none.
    rows = admin_client.get("/admin/api/company-profiles").json()["companies"]
    by_id = {c["id"]: c for c in rows}
    assert by_id["co-a"]["has_profile"] and not by_id["co-b"]["has_profile"]

    # THE rule: knowing things about a company did not own it. The booth can
    # still register co-a, because search_companies hides owned companies.
    assert any(c["id"] == "co-a" for c in svc.search_companies(""))

    # Search reaches into the profile columns, not just the company name.
    rows = admin_client.get("/admin/api/company-profiles?q=بهار").json()["companies"]
    assert [c["id"] for c in rows] == ["co-a"]
    rows = admin_client.get("/admin/api/company-profiles?q=هوش مصنوعی").json()["companies"]
    assert [c["id"] for c in rows] == ["co-a"]

    # Re-save updates in place — one row per company, never two (a PRIMARY
    # KEY makes that structural now, but the total company count must stay
    # unchanged and the new value must have landed on the right row).
    admin_client.put("/admin/api/company-profiles/co-a",
                     json={"contact_name": "نام تازه"})
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
        name = conn.execute("SELECT contact_name FROM companies"
                            " WHERE id = 'co-a'").fetchone()["contact_name"]
    finally:
        conn.close()
    assert n == 2 and name == "نام تازه"


def test_upsert_drops_unknown_fields_and_refuses_unknown_companies(admin_client):
    r = admin_client.put("/admin/api/company-profiles/co-a", json={
        "contact_name": "x", "dataset_id": "hacked", "status": "owned"})
    assert r.status_code == 200
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(companies)")]
        assert "status" not in cols
    finally:
        conn.close()

    assert admin_client.put("/admin/api/company-profiles/nope",
                            json={"contact_name": "x"}).status_code == 404


def test_profile_of_a_company_without_one_is_empty_not_404(admin_client):
    r = admin_client.get("/admin/api/company-profiles/co-b")
    assert r.status_code == 200 and r.json()["profile"] == {}


def test_a_verified_capture_becomes_the_profile_and_shows_in_the_list(admin_client):
    """The sales round-trip, end to end: the operator registers a contact from
    the admin panel (same path the booth's verify takes through
    sync_from_lead), and the companies page reflects it — the lead state shows,
    and the OTP'd contact overwrites the spreadsheet's guess."""
    # A spreadsheet-era profile with a WRONG guess of a contact.
    admin_client.put("/admin/api/company-profiles/co-a", json={
        "contact_name": "حدس قدیمی", "contact_mobile": "09120000000"})

    r = admin_client.post("/admin/api/leads/contacts", json={
        "dataset_id": "co-a", "first_name": "بهار", "last_name": "حمزه‌ای",
        "position": "مدیر اجرایی", "phone": "09124308928"})
    assert r.status_code == 200

    # The company's state in the book: verified, waiting for text.
    rows = admin_client.get("/admin/api/company-profiles").json()["companies"]
    co = next(c for c in rows if c["id"] == "co-a")
    assert co["lead_status"] == "verified"
    assert co["contact_name"] == "بهار حمزه‌ای"
    assert co["contact_mobile"].endswith("9124308928"), \
        "the OTP-verified number must replace the spreadsheet guess"

    # co-b is untouched and stays "not approached".
    co_b = next(c for c in rows if c["id"] == "co-b")
    assert co_b["lead_status"] is None

    # The profile itself was folded, not replaced: the spreadsheet-only fields
    # the form had set (province) survive beside the booth's contact data.
    profile = admin_client.get("/admin/api/company-profiles/co-a").json()["profile"]
    assert profile["source"] == "booth"


def test_company_video_round_trip_and_isolation_from_profile(admin_client):
    """The video is dataset-style content, not a profile field: setting it
    must not flip has_profile, and get_profile()'s {} contract must stay
    untouched by it (see app/services/company_profiles.py:set_video)."""
    base_content = {"title": "شرکت آ", "title_en": "", "text": "متن آ", "text_en": ""}

    r = admin_client.get("/admin/api/company-profiles/co-a")
    assert r.status_code == 200
    assert r.json() == {"profile": {}, "video_url": "", "content": base_content,
                        "priority_boost": False}

    r = admin_client.put("/admin/api/company-profiles/co-a/video",
                         json={"video_url": "/media/videos/co-a.mp4"})
    assert r.status_code == 200
    assert r.json() == {"video_url": "/media/videos/co-a.mp4"}

    r = admin_client.get("/admin/api/company-profiles/co-a")
    assert r.json() == {"profile": {}, "video_url": "/media/videos/co-a.mp4",
                        "content": base_content, "priority_boost": False}

    # The list endpoint (companies.js's "ویدیو" column) sees it too, and the
    # profile badge is still "ندارد" — a video is not a profile.
    rows = admin_client.get("/admin/api/company-profiles").json()["companies"]
    co_a = next(c for c in rows if c["id"] == "co-a")
    assert co_a["video_url"] == "/media/videos/co-a.mp4"
    assert co_a["has_profile"] is False

    # Clearing it round-trips to empty.
    r = admin_client.put("/admin/api/company-profiles/co-a/video",
                         json={"video_url": ""})
    assert r.status_code == 200 and r.json()["video_url"] == ""


def test_company_video_refuses_unknown_company(admin_client):
    r = admin_client.put("/admin/api/company-profiles/nope/video",
                         json={"video_url": "/media/videos/x.mp4"})
    assert r.status_code == 404


def test_company_priority_boost_round_trip_and_isolation_from_profile(admin_client):
    """priority_boost is a sort-order pin, not organizer knowledge: setting
    it must not flip has_profile, and get_profile()'s {} contract must stay
    untouched (see app/services/company_profiles.py:set_priority_boost)."""
    r = admin_client.get("/admin/api/company-profiles/co-a")
    assert r.json()["priority_boost"] is False

    r = admin_client.put("/admin/api/company-profiles/co-a/priority-boost",
                         json={"priority_boost": True})
    assert r.status_code == 200
    assert r.json() == {"priority_boost": True}

    r = admin_client.get("/admin/api/company-profiles/co-a")
    assert r.json()["priority_boost"] is True
    assert r.json()["profile"] == {}

    r = admin_client.put("/admin/api/company-profiles/co-a/priority-boost",
                         json={"priority_boost": False})
    assert r.status_code == 200 and r.json()["priority_boost"] is False


def test_company_priority_boost_refuses_unknown_company(admin_client):
    r = admin_client.put("/admin/api/company-profiles/nope/priority-boost",
                         json={"priority_boost": True})
    assert r.status_code == 404


def test_company_content_round_trip_and_isolation_from_profile(admin_client):
    """title/title_en/text/text_en are the chatbot's own words about the
    company — dataset-style content, not a profile field. Setting them must
    not flip has_profile, and get_profile()'s {} contract must stay
    untouched (see app/services/company_profiles.py:set_public_content)."""
    r = admin_client.put("/admin/api/company-profiles/co-a/content", json={
        "title": "نام تازه", "title_en": "New Name",
        "text": "متن تازه", "text_en": "New text",
    })
    assert r.status_code == 200
    assert r.json() == {"content": {
        "title": "نام تازه", "title_en": "New Name",
        "text": "متن تازه", "text_en": "New text",
    }}

    r = admin_client.get("/admin/api/company-profiles/co-a")
    assert r.json()["content"] == {
        "title": "نام تازه", "title_en": "New Name",
        "text": "متن تازه", "text_en": "New text",
    }
    assert r.json()["profile"] == {}

    # The profile badge stays "ندارد" — content is not a profile.
    rows = admin_client.get("/admin/api/company-profiles").json()["companies"]
    co_a = next(c for c in rows if c["id"] == "co-a")
    assert co_a["has_profile"] is False
    # The list's own title column reflects the write too.
    assert co_a["title"] == "نام تازه"

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT title, text FROM companies"
                           " WHERE id = 'co-a'").fetchone()
    finally:
        conn.close()
    assert row["title"] == "نام تازه" and row["text"] == "متن تازه"


def test_company_content_drops_unknown_fields_and_refuses_unknown_companies(admin_client):
    r = admin_client.put("/admin/api/company-profiles/co-a/content",
                         json={"title": "x", "video_url": "hacked", "id": "co-z"})
    assert r.status_code == 200
    assert r.json()["content"]["title"] == "x"
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id, video_url FROM companies"
                           " WHERE id = 'co-a'").fetchone()
    finally:
        conn.close()
    assert row["id"] == "co-a" and row["video_url"] != "hacked"

    r = admin_client.put("/admin/api/company-profiles/nope/content",
                         json={"title": "x"})
    assert r.status_code == 404


def test_the_page_and_apis_require_an_admin(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "profiles_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/api/company-profiles").status_code in (401, 403)
        assert anon.put("/admin/api/company-profiles/co-a",
                        json={}).status_code in (401, 403)
        assert anon.put("/admin/api/company-profiles/co-a/video",
                        json={"video_url": "x"}).status_code in (401, 403)
        assert anon.put("/admin/api/company-profiles/co-a/content",
                        json={"title": "x"}).status_code in (401, 403)
        assert anon.delete("/admin/api/company-profiles/co-a").status_code in (401, 403)
        assert anon.post("/admin/api/company-profiles/bulk-delete",
                         json={"ids": ["co-a"]}).status_code in (401, 403)
        page = anon.get("/secure-panel-inotex/companies",
                        follow_redirects=False)
        assert page.status_code in (302, 303, 401, 403)


# --- Delete (single + bulk) ------------------------------------------------
#
# A delete takes the company's WHOLE footprint: the companies row, the
# curated questions mapped to it (import-content.py's per-company anchors —
# an anchor whose record is gone is a Tier 0 match that serves nothing), and
# the capture history in company_leads (a lead without its company is a
# capture event nobody can act on).

def _seed_delete_footprint(admin_client):
    """co-a gets a curated question and a live lead; co-b gets a question
    only. Deleting co-a must leave every co-b trace intact.

    The lead row goes in by direct SQL, same as
    tests/test_leads_visitors_admin.py:_seed_lead — the register path's OTP
    side is another module's business and flaky on a laptop; the delete
    scenario only needs the row to exist.
    """
    from app.db.connection import get_db_connection
    from app.services import leads as leads_svc
    visitor_id = leads_svc.create_visitor("علی")["id"]
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                     " VALUES ('شرکت آ چیست؟', 'co-a', '')")
        conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                     " VALUES ('شرکت ب چیست؟', 'co-b', '')")
        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " phone, phone_hash, status, created_at)"
            " VALUES (?, 'co-a', 'شرکت آ', ?, '09120000000', 'hash',"
            " 'verified', ?)",
            (secrets.token_urlsafe(8), visitor_id,
             datetime.datetime.utcnow().isoformat()))
        conn.commit()
    finally:
        conn.close()


def _table_pairs(admin_client):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        companies = sorted(r["id"] for r in
                           conn.execute("SELECT id FROM companies").fetchall())
        questions = sorted(r["dataset_id"] for r in
                           conn.execute("SELECT dataset_id FROM questions").fetchall())
        leads = sorted(r["dataset_id"] for r in
                       conn.execute("SELECT dataset_id FROM company_leads").fetchall())
    finally:
        conn.close()
    return companies, questions, leads


def test_delete_company_takes_its_whole_footprint(admin_client):
    _seed_delete_footprint(admin_client)

    r = admin_client.delete("/admin/api/company-profiles/co-a")
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"

    companies, questions, leads = _table_pairs(admin_client)
    assert companies == ["co-b"]
    assert questions == ["co-b"]
    assert leads == []

    # The list page reflects the delete immediately.
    rows = admin_client.get("/admin/api/company-profiles").json()["companies"]
    assert [c["id"] for c in rows] == ["co-b"]


def test_delete_company_refuses_unknown_company(admin_client):
    assert admin_client.delete(
        "/admin/api/company-profiles/nope").status_code == 404
    companies, _, _ = _table_pairs(admin_client)
    assert companies == ["co-a", "co-b"]


def test_bulk_delete_takes_only_the_named_companies(admin_client):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO companies (id, title, text)"
                     " VALUES ('co-c', 'شرکت پ', 'متن پ')")
        conn.commit()
    finally:
        conn.close()
    _seed_delete_footprint(admin_client)
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                     " VALUES ('شرکت پ چیست؟', 'co-c', '')")
        conn.commit()
    finally:
        conn.close()

    r = admin_client.post("/admin/api/company-profiles/bulk-delete",
                          json={"ids": ["co-a", "co-c"]})
    assert r.status_code == 200
    assert r.json() == {"status": "deleted", "deleted": 2}

    companies, questions, leads = _table_pairs(admin_client)
    assert companies == ["co-b"]
    assert questions == ["co-b"]
    assert leads == []


def test_bulk_delete_rejects_bad_payloads(admin_client):
    for body in ({}, {"ids": []}, {"ids": [1, 2]}, {"ids": "co-a"}):
        r = admin_client.post("/admin/api/company-profiles/bulk-delete",
                              json=body)
        assert r.status_code == 400, body
    companies, _, _ = _table_pairs(admin_client)
    assert companies == ["co-a", "co-b"]


def test_bulk_delete_of_unknown_ids_reports_zero(admin_client):
    r = admin_client.post("/admin/api/company-profiles/bulk-delete",
                          json={"ids": ["nope-1", "nope-2"]})
    assert r.status_code == 200
    assert r.json() == {"status": "deleted", "deleted": 0}
    companies, _, _ = _table_pairs(admin_client)
    assert companies == ["co-a", "co-b"]


def test_the_companies_page_wires_the_delete_ui():
    """The anti-scaffold guard: the page must actually carry the delete
    wiring (checkbox column, bulk toolbar, per-row delete) — endpoints with
    no caller are the defect class PR #17 flushed out seven times."""
    from pathlib import Path
    from app.config import BASE_DIR
    base = Path(BASE_DIR)
    html = (base / "templates" / "admin" / "companies.html").read_text(
        encoding="utf-8")
    js = (base / "static" / "admin" / "js" / "companies.js").read_text(
        encoding="utf-8")
    assert "companies-bulk-toolbar" in html
    assert "companies-select-all" in html
    assert "row-check" in js
    assert "/admin/api/company-profiles/bulk-delete" in js
    assert "data-del" in js
