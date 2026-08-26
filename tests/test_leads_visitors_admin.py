"""Removing a booth colleague from the visitor roster.

The scenario (AGENTS.md: a feature is a scenario, not a capability): a
teammate has finished their shift, the operator removes them from the
roster, and the personal link still sitting in that phone stops working on
the very next tap — while every lead they captured stays in the tables,
still owning its company until an admin releases it. If the DELETE route or
the service call behind it is unwired, these tests fail on the real
endpoint, not on a mock.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('leadadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "leadadmin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _seed_lead(visitor_id, status="verified"):
    """A company this visitor captured, straight into the table.

    Direct SQL rather than register_contact() because the OTP side of that
    path is another module's business; the deletion scenario only needs the
    lead row to exist and to belong to this visitor.
    """
    from app.db.connection import get_db_connection
    lead_id = secrets.token_urlsafe(8)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " phone, phone_hash, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_id, "some-company", "شرکتی", visitor_id,
             "09120000000", "hash", status, datetime.datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return lead_id


def test_delete_visitor_kills_the_link_and_keeps_the_leads(admin_client):
    from app.services import leads as svc
    visitor = svc.create_visitor("علی")
    lead_id = _seed_lead(visitor["id"])

    r = admin_client.delete(f"/admin/api/leads/visitors/{visitor['id']}")
    assert r.status_code == 200

    roster = admin_client.get("/admin/api/leads/visitors").json()["visitors"]
    assert all(v["id"] != visitor["id"] for v in roster)

    assert svc.visitor_by_code(visitor["code"]) is None, \
        "the deleted colleague's personal link still opens a session"

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id, status, released_at FROM company_leads"
                           " WHERE id = ?", (lead_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, "deleting a colleague deleted the leads they captured"
    assert row["status"] == "verified" and row["released_at"] is None, \
        "deleting a colleague quietly released the companies they owned"


def test_delete_visitor_is_final_for_the_id(admin_client):
    from app.services import leads as svc
    visitor = svc.create_visitor("ب")
    assert admin_client.delete(f"/admin/api/leads/visitors/{visitor['id']}").status_code == 200
    assert admin_client.delete(f"/admin/api/leads/visitors/{visitor['id']}").status_code == 404


def test_delete_visitor_rejects_an_unauthenticated_caller(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        r = anon.delete("/admin/api/leads/visitors/whoever")
        assert r.status_code in (401, 403)
