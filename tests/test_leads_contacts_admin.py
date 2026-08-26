"""An admin recording a company contact met outside the booth.

The scenario: the contact was never stood next to a field visitor — the
operator met them on the phone or in a corridor. The operator opens
/secure-panel-inotex/leads, picks the company from the same search the booth
sees, types the responsible person's details, and gets a one-time edit link to
hand over personally. The row lands in the SAME table as booth leads, owns its
company by the SAME rule, and its text goes through the SAME review queue.

If the endpoint, the ownership rule, or the invite wiring is removed, these
fail on the real route — not on a mock.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contacts_admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        conn.execute("INSERT INTO dataset (id, title, text)"
                     " VALUES ('co-a', 'شرکت آ', 'متن آ'), ('co-b', 'شرکت ب', 'متن ب')")
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('cadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "cadmin",
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _add(client, dataset_id="co-a", phone="09121111111", first="رضا", **extra):
    return client.post("/admin/api/leads/contacts", json={
        "dataset_id": dataset_id, "first_name": first, "last_name": "احمدی",
        "position": "مدیرعامل", "phone": phone, **extra,
    })


def test_admin_adds_a_contact_and_gets_a_working_invite(admin_client):
    r = _add(admin_client)
    assert r.status_code == 200
    body = r.json()
    assert body["company"] == "شرکت آ"
    assert "<svg" in body["qr"] and "/edit/" in body["link"]

    # The row is a lead like any other: verified (the admin vouched), owning
    # its company, attributed to no booth visitor.
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM company_leads ORDER BY created_at DESC").fetchone()
    finally:
        conn.close()
    assert row["status"] == "verified" and row["visitor_id"] == ""
    assert row["dataset_id"] == "co-a"

    # The company leaves the search list for everyone, including the booth.
    from app.services import leads as svc
    assert all(c["id"] != "co-a" for c in svc.search_companies(""))

    # The invite actually opens the edit page and a submit reaches the queue.
    token = body["link"].rsplit("/edit/", 1)[1]
    assert admin_client.get(f"/edit/{token}").status_code == 200
    r = admin_client.post(f"/api/leads/edit/{token}", json={"text": "متن تازه"})
    assert r.status_code == 200
    edits = admin_client.get("/admin/api/leads/edits").json()["edits"]
    assert any(e["new_text"] == "متن تازه" and e["dataset_id"] == "co-a" for e in edits)


def test_an_owned_company_is_refused_twice(admin_client):
    assert _add(admin_client).status_code == 200
    r = _add(admin_client, phone="09122222222")
    assert r.status_code == 409
    assert r.json().get("duplicate") is None, "a taken company is final, not a question"


def test_duplicate_phone_is_a_question_then_an_override(admin_client):
    assert _add(admin_client, dataset_id="co-a").status_code == 200
    first = _add(admin_client, dataset_id="co-b", phone="09121111111")
    assert first.status_code == 409 and first.json()["duplicate"] is True

    second = _add(admin_client, dataset_id="co-b", phone="09121111111",
                 override_duplicate=True)
    assert second.status_code == 200
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT duplicate_override_of FROM company_leads"
                           " WHERE dataset_id = 'co-b'").fetchone()
    finally:
        conn.close()
    assert row["duplicate_override_of"], "the override must be written down"


def test_the_company_search_only_offers_unowned_companies(admin_client):
    r = admin_client.get("/admin/api/leads/companies?q=")
    assert {c["id"] for c in r.json()["companies"]} == {"co-a", "co-b"}
    _add(admin_client)
    r = admin_client.get("/admin/api/leads/companies?q=")
    assert {c["id"] for c in r.json()["companies"]} == {"co-b"}


def test_adding_a_contact_requires_an_admin(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contacts_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        r = anon.post("/admin/api/leads/contacts", json={
            "dataset_id": "co-a", "first_name": "x", "phone": "09121111111"})
        assert r.status_code in (401, 403)
        assert anon.get("/admin/api/leads/companies").status_code in (401, 403)
