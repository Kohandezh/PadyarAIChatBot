"""Per-company operator tools on the leads page: reissue a link, delete a company.

Two scenarios:

REISSUE — a contact lost their one-time link (or it expired unread). The
operator knows who they are; re-verifying the phone from the booth is
theatre. One click on the company's row mints a fresh invite off the company's
live owner, kills the previous one, and returns the link + QR once.

DELETE — a company was added by mistake or pulled out. Its leads, invites and
pending drafts go; it disappears from the booth search and the contact form;
the `companies` row (the chatbot's answer) stays, because that is the
companies page's business.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "tools_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        from app.services import leads as svc
        svc.ensure_tables()
        conn = get_db_connection()
        # Companies are their own table now (migrations/0013_companies.sql).
        conn.execute("INSERT INTO companies (id, title, text)"
                     " VALUES ('co-a', 'شرکت آ', 'متن آ')")
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('tadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "tadmin",
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _owner_row(dataset_id="co-a", phone="09121111111"):
    """A live owner for the company, straight into the table."""
    from app.db.connection import get_db_connection
    from app.services import leads as svc
    svc.ensure_tables()
    lead_id = secrets.token_urlsafe(8)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " phone, phone_hash, status, created_at, verified_at)"
            " VALUES (?, ?, ?, '', ?, ?, 'verified', ?, ?)",
            (lead_id, dataset_id, "شرکت آ", phone, "h",
             datetime.datetime.utcnow().isoformat(),
             datetime.datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return lead_id


def test_reissue_mints_a_working_link_and_kills_the_previous(admin_client):
    from app.services import leads as svc
    lead_id = _owner_row()
    first = svc.create_invite(lead_id, "co-a", "http://x")

    r = admin_client.post("/admin/api/leads/contacts/co-a/reissue-invite")
    assert r.status_code == 200
    body = r.json()
    assert "/edit/" in body["link"] and "<svg" in body["qr"]

    # The old link is dead (deleted, so 404 — same page as any other dead
    # invite); the new one serves the one-time gate, the button press opens
    # it, and a submit through the session reaches the queue attributed to
    # the same lead.
    old_token = first["invite_url"].rsplit("/edit/", 1)[1]
    assert admin_client.get(f"/edit/{old_token}").status_code in (404, 410)
    new_token = body["link"].rsplit("/edit/", 1)[1]
    assert admin_client.get(f"/edit/{new_token}").status_code == 200
    assert admin_client.post(f"/api/leads/edit/{new_token}/begin").status_code == 200
    assert admin_client.get("/api/leads/edit/state").status_code == 200
    assert admin_client.post("/api/leads/edit/submit", json={
        "fields": {"title": "شرکت آ", "text": "متن تازه"}}).status_code == 200
    edits = admin_client.get("/admin/api/leads/edits").json()["edits"]
    assert any(e["lead_id"] == lead_id for e in edits)


def test_reissue_refuses_a_company_nobody_owns(admin_client):
    r = admin_client.post("/admin/api/leads/contacts/co-a/reissue-invite")
    assert r.status_code == 404
    assert "مسئول" in r.json()["detail"]


def test_delete_company_removes_leads_invites_and_drafts(admin_client):
    from app.services import leads as svc
    lead_id = _owner_row()
    svc.create_invite(lead_id, "co-a", "http://x")
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset_edits (id, dataset_id, lead_id, old_text, new_text,"
        " status, created_at) VALUES ('e1', 'co-a', ?, 'a', 'b', 'pending', ?)",
        (lead_id, datetime.datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    r = admin_client.delete("/admin/api/leads/companies/co-a")
    assert r.status_code == 200 and r.json()["leads_removed"] == 1

    conn = get_db_connection()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM company_leads").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM edit_invites").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM dataset_edits").fetchone()["c"] == 0
        # The chatbot's answer is the companies page's business, not this one's.
        assert conn.execute("SELECT COUNT(*) c FROM companies WHERE id = 'co-a'"
                            ).fetchone()["c"] == 1
    finally:
        conn.close()

    # With no leads left, the company is back in the booth search: deleting
    # the leads released it, exactly like an explicit release would.
    from app.services import leads as svc2
    assert any(c["id"] == "co-a" for c in svc2.search_companies(""))
    # And it can be re-added from scratch: the contact form accepts it again.
    r = admin_client.post("/admin/api/leads/contacts", json={
        "dataset_id": "co-a", "first_name": "نو", "phone": "09129999999"})
    assert r.status_code == 200


def test_both_acts_refuse_an_anonymous_caller(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "tools_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        assert anon.post("/admin/api/leads/contacts/co-a/reissue-invite"
                         ).status_code in (401, 403)
        assert anon.delete("/admin/api/leads/companies/co-a"
                           ).status_code in (401, 403)
