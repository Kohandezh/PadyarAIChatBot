"""Visit notes: what the field team saw, beside (never inside) the lead.

The relation under test — the WhatsApp message the feature replaces:

    marketing_notes.dataset_id -> companies.id
    marketing_notes.visitor_id -> lead_visitors.id

Load-bearing rules:
- A note never claims a company: company_leads' ownership is untouched by
  note writes (the booth search must not lose the company).
- The contact block is note-grade: stored, exported, but never a lead and
  never OTP-consented.
- The CSV export neutralizes spreadsheet formula injection like every
  other export of visitor-typed text.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def clients(tmp_path, monkeypatch):
    """An admin TestClient AND a field-agent (visitor) cookie on the same DB."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "notes.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        from app.services import leads as leads_svc
        leads_svc.ensure_tables()
        conn = get_db_connection()
        conn.execute("INSERT INTO companies (id, title, text)"
                     " VALUES ('co-1', 'شرکت مبین فناوران', 'متن معرفی')")
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('padmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "padmin",
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)

        agent = leads_svc.create_visitor("زهرا بازاریابی")
        c.cookies.set(leads_svc.VISITOR_COOKIE, agent["code"])
        yield c, agent
        c.cookies.delete(leads_svc.VISITOR_COOKIE)


def _post_note(c, **over):
    body = {"dataset_id": "co-1",
            "note": "بشدت مشتاق همکاری با شرکت کهن سیستم فردا هستند",
            "warmth": "high",
            "contact_name": "زهرا باقری",
            "contact_position": "مسئول اداری غرفه",
            "contact_phone": "۰۹۹۳۶۴۹۵۰۰۱"}
    body.update(over)
    return c.post("/api/leads/notes", json=body)


def test_agent_can_note_without_otp_and_company_stays_findable(clients):
    c, agent = clients
    r = _post_note(c)
    assert r.status_code == 200
    assert r.json()["company"] == "شرکت مبین فناوران"

    from app.services import leads as svc
    rows = svc.list_notes()
    assert len(rows) == 1
    note = rows[0]
    assert note["warmth"] == "high"
    # Persian digits folded to ASCII so the number is searchable as typed.
    assert note["contact_phone"] == "09936495001"
    assert note["visitor_name"] == "زهرا بازاریابی"
    # THE OWNERSHIP RULE: the note created no lead, so the company is still
    # in the booth search for the next agent to register properly.
    assert any(x["id"] == "co-1" for x in svc.search_companies("مبین"))


def test_note_requires_known_company_and_real_text(clients):
    c, _ = clients
    assert _post_note(c, dataset_id="nope").status_code == 404
    assert _post_note(c, note="   ").status_code == 400
    assert _post_note(c, warmth="on-fire").status_code == 400


def test_notes_need_a_field_agent_session(clients):
    c, _ = clients
    from app.services import leads as svc
    code = c.cookies.get(svc.VISITOR_COOKIE)
    c.cookies.delete(svc.VISITOR_COOKIE)
    try:
        assert _post_note(c).status_code == 401
    finally:
        if code:
            c.cookies.set(svc.VISITOR_COOKIE, code)


def test_admin_feed_filters_to_one_company_timeline(clients):
    c, _ = clients
    _post_note(c)
    _post_note(c, note="یادداشت دوم", warmth="low")

    r = c.get("/admin/api/leads/notes?dataset_id=co-1")
    assert r.status_code == 200
    notes = r.json()["notes"]
    assert len(notes) == 2
    # newest first — the feed reads like a timeline
    assert notes[0]["note"] == "یادداشت دوم"

    r = c.get("/admin/api/leads/notes?q=کهن سیستم")
    assert all("کهن سیستم" in n["note"] for n in r.json()["notes"])


def test_admin_feed_requires_admin(clients):
    c, _ = clients
    c.cookies.delete("admin_session")
    assert c.get("/admin/api/leads/notes").status_code == 401


def test_csv_export_neutralizes_formula_injection(clients):
    c, _ = clients
    _post_note(c, note="=CMD('calc')!A0", contact_name="@weird")

    r = c.get("/admin/api/leads/notes/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    body = r.text
    assert "'=CMD" in body          # the defusing apostrophe
    assert "داغ" in body            # warmth in the operator's words
    assert "09936495001" in body


def test_warmth_follows_the_company_and_slices_the_table(clients):
    """The newest note's warmth is the company's CURRENT reading, stored on
    the companies row (migrations/0019) so the admin table can filter and
    export by it."""
    c, _ = clients
    _post_note(c, warmth="high")                              # داغ
    _post_note(c, note="بعد از مذاکره سرد شد", warmth="low")  # newest wins

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    row = conn.execute("SELECT marketing_warmth FROM companies WHERE id = 'co-1'").fetchone()
    conn.close()
    assert row["marketing_warmth"] == "low"

    from app.services import company_profiles as cp
    assert [x["id"] for x in cp.list_companies(warmth="low")] == ["co-1"]
    assert cp.count_companies(warmth="high") == 0

    r = c.get("/admin/api/company-profiles?warmth=low")
    assert [x["id"] for x in r.json()["companies"]] == ["co-1"]
    assert r.json()["total"] == 1


def test_companies_export_carries_the_warmth_column(clients):
    c, _ = clients
    _post_note(c, warmth="high")
    r = c.get("/admin/api/company-profiles/export")
    assert r.status_code == 200
    assert "علاقه‌مندی" in r.text
    assert "داغ" in r.text


def test_warmth_never_reaches_a_chat_or_ai_path(clients):
    """ORGANIZER-ONLY, END TO END. The public profile allowlist, the
    company-list tier's loader and the pick tier's record lookup are the
    three doors any company fact walks through on its way to a visitor or
    the model — none of them may carry marketing_warmth. This test fails
    the day someone 'helpfully' widens one of those column lists."""
    c, _ = clients
    _post_note(c, warmth="high")

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE companies SET activity_field = ? WHERE id = 'co-1'",
                 ("هوش مصنوعی",))
    conn.commit()
    conn.close()

    import json

    from app.services import company_profiles, search
    from app.services.company_search import _load_companies, answer_company_list

    assert "marketing_warmth" not in company_profiles.public_profile("co-1")
    assert all("marketing_warmth" not in row for row in _load_companies())
    entry = search.get_entry("co-1")
    assert entry is None or "marketing_warmth" not in entry

    res = answer_company_list("شرکت های هوش مصنوعی")
    assert res is not None and "co-1" in res["matched_ids"]
    assert "marketing_warmth" not in json.dumps(res, ensure_ascii=False)


def test_renaming_the_agent_follows_into_their_notes(clients):
    """A typo fix on the roster must not fork the person into two names:
    the roster, the live-joined leads, AND the denormalized note copies
    all say the new name after one rename."""
    c, agent = clients
    from app.services import leads as svc
    _post_note(c)

    r = c.post(f"/admin/api/leads/visitors/{agent['id']}/rename",
               json={"name": "زهرا باقری‌زاده"})
    assert r.status_code == 200

    assert any(v["name"] == "زهرا باقری‌زاده"
               for v in svc.list_visitors())
    note = svc.list_notes()[0]
    assert note["visitor_name"] == "زهرا باقری‌زاده"

    assert c.post(f"/admin/api/leads/visitors/{agent['id']}/rename",
                  json={"name": "   "}).status_code == 400
    assert c.post("/admin/api/leads/visitors/nope/rename",
                  json={"name": "کسی"}).status_code == 404
