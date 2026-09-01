"""The one-time edit link: it opens exactly once (migrations/0021).

The scenarios, in the order a real link lives them:

- GET alone never spends anything — messengers (Telegram, WhatsApp) prefetch
  URLs server-side before the human taps, so a link that died on GET was a
  link the contact never had.
- The button press is the one spend. A second press, from any device, gets
  the dead page; the dead page says the same sentence for a used, expired and
  unknown token alike.
- The page the press opened survives on its HttpOnly session cookie: same
  browser may refresh until the session's two hours are up.
- Submit ends the session; nothing works afterwards.
- The session expires on its own clock, independent of the invite's 24 hours.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "edit_session.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed(app_client, dataset_id="co-edit"):
    """A company, a verified lead, and a live invite — the state a contact's
    SMS link arrives in. Direct SQL, like the rotation tests: the OTP round
    trip belongs to another module."""
    from app.db.connection import get_db_connection
    from app.services import leads as svc
    svc.ensure_tables()
    lead_id = secrets.token_urlsafe(8)
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO companies (id, title, text) VALUES (?, ?, ?)",
                     (dataset_id, "شرکت نمونه", "متن قدیمی"))
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
    return lead_id, invite["invite_url"].rsplit("/edit/", 1)[1]


def test_get_alone_never_spends_the_link(app_client):
    _, token = _seed(app_client)

    for _ in range(3):
        r = app_client.get(f"/edit/{token}")
        assert r.status_code == 200, "a mere GET spent the one-time link"
        assert 'id="begin"' in r.text, "the unspent link must serve the gate page"


def test_the_button_press_is_the_one_spend(app_client):
    from app.main import app
    _, token = _seed(app_client)

    contact = TestClient(app)
    assert contact.post(f"/api/leads/edit/{token}/begin").status_code == 200

    # Every later device — and the link itself — gets the dead page.
    stranger = TestClient(app)
    assert stranger.post(f"/api/leads/edit/{token}/begin").status_code == 410
    r = stranger.get(f"/edit/{token}")
    assert r.status_code == 410
    assert "اینجا چیزی برای نمایش نیست" in r.text, \
        "a spent link must show the one dead-page sentence, same as any other"


def test_the_open_page_refreshes_in_the_same_browser(app_client):
    _, token = _seed(app_client)

    assert app_client.get(f"/edit/{token}").status_code == 200
    assert app_client.post(f"/api/leads/edit/{token}/begin").status_code == 200

    # Same URL, cookie held: the form itself now, not the gate.
    r = app_client.get(f"/edit/{token}")
    assert r.status_code == 200
    assert 'id="save"' in r.text and 'id="begin"' not in r.text


def test_state_and_submit_ride_the_session_cookie(app_client):
    _, token = _seed(app_client)

    assert app_client.post(f"/api/leads/edit/{token}/begin").status_code == 200
    state = app_client.get("/api/leads/edit/state")
    assert state.status_code == 200
    body = state.json()
    assert body["company"] == "شرکت نمونه"
    assert body["text"] == "متن قدیمی"

    r = app_client.post("/api/leads/edit/submit",
                        json={"fields": {"title": "شرکت نمونه", "text": "متن تازه"}})
    assert r.status_code == 200
    assert r.json()["kind"] == "change"

    assert app_client.get("/api/leads/edit/state").status_code == 410
    assert app_client.post("/api/leads/edit/submit",
                           json={"fields": {"title": "شرکت نمونه",
                                            "text": "دوباره"}}).status_code == 410


def test_submit_without_a_session_is_refused(app_client):
    r = app_client.post("/api/leads/edit/submit",
                        json={"fields": {"title": "x", "text": "متن"}})
    assert r.status_code in (403, 404, 410)


def test_the_session_expires_on_its_own_clock(app_client):
    from app.db.connection import get_db_connection
    _, token = _seed(app_client)

    assert app_client.post(f"/api/leads/edit/{token}/begin").status_code == 200

    conn = get_db_connection()
    try:
        past = (datetime.datetime.utcnow() - datetime.timedelta(seconds=1)).isoformat()
        conn.execute("UPDATE edit_sessions SET expires_at = ?", (past,))
        conn.commit()
    finally:
        conn.close()

    assert app_client.get("/api/leads/edit/state").status_code == 410


def test_an_expired_invite_shows_the_dead_page(app_client):
    from app.db.connection import get_db_connection
    _, token = _seed(app_client)

    conn = get_db_connection()
    try:
        past = (datetime.datetime.utcnow() - datetime.timedelta(seconds=1)).isoformat()
        conn.execute("UPDATE edit_invites SET expires_at = ?", (past,))
        conn.commit()
    finally:
        conn.close()

    assert app_client.get(f"/edit/{token}").status_code == 410
    assert app_client.post(f"/api/leads/edit/{token}/begin").status_code == 410


def test_the_lead_completes_on_submit(app_client):
    from app.db.connection import get_db_connection
    lead_id, token = _seed(app_client)

    assert app_client.post(f"/api/leads/edit/{token}/begin").status_code == 200
    assert app_client.post("/api/leads/edit/submit", json={
        "fields": {"title": "شرکت نمونه", "text": "متن تازه"}}).status_code == 200

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT submitted_at FROM edit_sessions"
                           " WHERE lead_id = ?", (lead_id,)).fetchone()
    finally:
        conn.close()
    assert row["submitted_at"], "the session did not record its submit"
    from app.services import leads as svc
    funnel = svc.funnel()
    assert funnel["completed"] == 1
    assert funnel["pending_review"] == 1
