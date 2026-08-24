"""The company contact's path: one link, one field, one submit.

The invite token in the URL is the contact's entire credential, so these tests
hold down what that credential may and may not do:

  * reading is free and repeatable, because a contact who opens the link, gets
    interrupted and comes back an hour later is normal behaviour;
  * the page hands over the company name and `متن پاسخ` and nothing else, so a
    stranger holding a link learns nothing about the row behind it;
  * a submit carries exactly one field, and anything extra is REFUSED rather
    than ignored, because silence is how `dataset_id` gets wired through later;
  * the link dies on the successful submit and on nothing else, and a used, an
    expired and an invented link are one indistinguishable sentence.

The raw token is deliberately absent from every API response (the visitor is
shown a QR, the contact scans it). A test cannot scan, so `_booth` mints one
through the same service function the verify path calls.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.services.leads import DEAD_INVITE_MESSAGE, MAX_EDIT_CHARS


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "invite.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    from app.services import leads as leads_service
    leads_service.ensure_tables()
    return tmp_path


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    import app.routers.leads as leads_router
    # Signature-agnostic: the lead routes pass their own ceiling through, and
    # that call shape is being changed in a parallel piece of work.
    monkeypatch.setattr(leads_router, "check_rate_limit",
                        lambda *args, **kwargs: None)


@pytest.fixture
def outbox(monkeypatch):
    from app.services import otp as otp_service
    sent = []
    monkeypatch.setattr(otp_service, "_deliver",
                        lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture
def client(paths):
    from app.main import app
    with TestClient(app) as c:
        yield c


LIVE_TEXT = "متن قدیمی که تیم پادیار نوشته است."


def _add_company(dataset_id, title, text=LIVE_TEXT):
    """A company row with every column the read response must NOT serve."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, ?, 'https://videos.example/co.mp4', ?, ?, 10)",
        (dataset_id, title, text, f"English name of {dataset_id}",
         f"English answer for {dataset_id}"))
    conn.commit()
    conn.close()


def _as_contact(client):
    """Drop the visitor session. The contact never had one."""
    from app.services import leads as leads_service
    client.cookies.delete(leads_service.VISITOR_COOKIE)


def _booth(client, outbox, dataset_id="co-a", title="پارس فناوران آریا",
           text=LIVE_TEXT, phone="09121110022"):
    """One whole capture, ending with the token the contact would be holding."""
    from app.services import leads as leads_service
    _add_company(dataset_id, title, text)

    made = leads_service.create_visitor("همکار " + dataset_id)
    assert client.get(f"/v/{made['code']}", follow_redirects=False).status_code == 303
    registered = client.post("/api/leads/register", json={
        "dataset_id": dataset_id, "first_name": "مینا", "last_name": "رضایی",
        "position": "مدیر فروش", "phone": phone})
    assert registered.status_code == 200, registered.text
    lead_id = registered.json()["lead_id"]
    verified = client.post("/api/leads/verify",
                           json={"lead_id": lead_id, "code": outbox[-1][1]})
    assert verified.status_code == 200, verified.text

    url = leads_service.create_invite(lead_id, dataset_id,
                                      "http://testserver")["invite_url"]
    _as_contact(client)
    return {"token": url.rsplit("/", 1)[1], "lead_id": lead_id,
            "dataset_id": dataset_id, "company": title, "live_text": text}


@pytest.fixture
def invite(client, outbox):
    return _booth(client, outbox)


def _edits(dataset_id=None):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        if dataset_id is None:
            rows = conn.execute(
                "SELECT * FROM dataset_edits ORDER BY created_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dataset_edits WHERE dataset_id = ? ORDER BY created_at",
                (dataset_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _dataset_row(dataset_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return dict(conn.execute("SELECT * FROM dataset WHERE id = ?",
                                 (dataset_id,)).fetchone())
    finally:
        conn.close()


def _live_text(dataset_id):
    return _dataset_row(dataset_id)["text"]


def _lead_status(lead_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return conn.execute("SELECT status FROM company_leads WHERE id = ?",
                            (lead_id,)).fetchone()["status"]
    finally:
        conn.close()


def _invite_row(token):
    from app.db.connection import get_db_connection
    from app.services import leads as leads_service
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM edit_invites WHERE token_hash = ?",
                           (leads_service._digest(token),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ── Opening the link ────────────────────────────────────────────────────

def test_opening_the_link_does_not_burn_it(client, invite):
    """Read, close the tab, come back after the queue clears. Still works."""
    token = invite["token"]
    first = client.get(f"/edit/{token}")
    assert first.status_code == 200
    # The page is a shell; the company name and the text arrive from the API
    # call below, which is the request that must also stay repeatable.
    assert "/static/leads/edit.js" in first.text
    assert _invite_row(token)["used_at"] is None

    for _ in range(3):
        assert client.get(f"/edit/{token}").status_code == 200
        assert client.get(f"/api/leads/edit/{token}").status_code == 200
    assert _invite_row(token)["used_at"] is None
    assert _edits() == []


def test_the_read_response_carries_the_company_and_the_text_and_nothing_else(
        client, invite):
    """The row has an id, a video and two English columns. None of them are
    part of this conversation, so none of them are in the answer."""
    # Anchor the absences below in something that is really there. Asserting a
    # column is withheld proves nothing if the column was empty all along.
    row = _dataset_row(invite["dataset_id"])
    assert row["video_url"] and row["title_en"] and row["text_en"]

    read = client.get(f"/api/leads/edit/{invite['token']}")
    assert read.status_code == 200, read.text
    body = read.json()

    # `submission` is what became of the last text this company sent, and it is
    # here because the invite a rejection notice carries opens THIS page: the
    # reviewer's reason has nowhere else to be read.
    assert set(body) == {"company", "text", "pending", "submission",
                         "expires_at", "consent_script"}
    assert body["company"] == invite["company"]
    assert body["text"] == invite["live_text"]
    assert body["pending"] is False
    # The script the contact was read at the booth is shown again here.
    assert body["consent_script"].strip()

    # The values, not just the key names: proof the columns exist and are held
    # back, rather than a test that would pass against an empty row.
    assert invite["dataset_id"] not in read.text
    assert "videos.example" not in read.text
    assert "English name of" not in read.text
    assert "English answer for" not in read.text


# ── The refusals, all of which leave the link working ───────────────────

def test_a_submit_carrying_any_extra_field_is_refused(client, invite):
    """Ignoring `dataset_id` is not the same as refusing it. Ignoring is how
    the next person to touch this endpoint wires it through by accident."""
    token = invite["token"]
    smuggled = client.post(f"/api/leads/edit/{token}",
                           json={"text": "متن تازه", "dataset_id": "some-other-company"})
    assert smuggled.status_code == 400, smuggled.text
    assert "dataset_id" in smuggled.json()["detail"]

    assert client.post(f"/api/leads/edit/{token}",
                       json={"text": "متن تازه", "status": "approved"}).status_code == 400
    assert client.post(f"/api/leads/edit/{token}", json={}).status_code == 400
    assert client.post(f"/api/leads/edit/{token}",
                       json={"text": 42}).status_code == 400

    # Nothing was queued and the contact's link still works.
    assert _edits() == []
    assert _invite_row(token)["used_at"] is None
    assert client.post(f"/api/leads/edit/{token}",
                       json={"text": "متن تازه"}).status_code == 200


def test_empty_text_is_refused_and_the_link_stays_alive(client, invite):
    """An empty box is a mis-tap, not a request to delete a company's answer."""
    token = invite["token"]
    for blank in ("", "   ", "\n\t "):
        refused = client.post(f"/api/leads/edit/{token}", json={"text": blank})
        assert refused.status_code == 400, refused.text
    assert _edits() == []
    assert _invite_row(token)["used_at"] is None
    assert client.post(f"/api/leads/edit/{token}",
                       json={"text": "متن درست"}).status_code == 200


def test_text_over_the_cap_is_refused_and_the_cap_itself_is_accepted(client, invite):
    """The boundary is read from the module, so the test stays correct when
    the cap moves."""
    token = invite["token"]
    over = client.post(f"/api/leads/edit/{token}", json={"text": "ا" * (MAX_EDIT_CHARS + 1)})
    assert over.status_code == 400, over.text
    assert str(MAX_EDIT_CHARS) in over.json()["detail"]
    assert _edits() == []
    assert _invite_row(token)["used_at"] is None

    at_cap = "ب" * MAX_EDIT_CHARS
    accepted = client.post(f"/api/leads/edit/{token}", json={"text": at_cap})
    assert accepted.status_code == 200, accepted.text
    assert _edits()[0]["new_text"] == at_cap


# ── The one submit that counts ──────────────────────────────────────────

def test_a_successful_submit_queues_the_edit_and_leaves_the_live_answer_untouched(
        client, invite):
    """What the chatbot says to the public does not change here. It changes
    when an admin approves, and nowhere else."""
    assert _live_text(invite["dataset_id"]) == invite["live_text"]

    sent = client.post(f"/api/leads/edit/{invite['token']}",
                       json={"text": "  متن درست شرکت را خودمان نوشتیم.  "})
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"ok": True, "company": invite["company"]}

    assert _live_text(invite["dataset_id"]) == invite["live_text"]
    rows = _edits(invite["dataset_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["new_text"] == "متن درست شرکت را خودمان نوشتیم."
    # `old_text` is the live answer at the moment of submission. It is what a
    # revert puts back.
    assert rows[0]["old_text"] == invite["live_text"]
    assert rows[0]["lead_id"] == invite["lead_id"]
    # The contact answered, so the registration is done.
    assert _lead_status(invite["lead_id"]) == "completed"


def test_the_link_dies_on_that_success(client, invite):
    token = invite["token"]
    assert client.post(f"/api/leads/edit/{token}",
                       json={"text": "متن اول"}).status_code == 200
    assert _invite_row(token)["used_at"]

    assert client.get(f"/edit/{token}").status_code == 410
    assert client.get(f"/api/leads/edit/{token}").status_code == 410
    replay = client.post(f"/api/leads/edit/{token}", json={"text": "متن دوم"})
    assert replay.status_code == 410, replay.text
    # The second text got nowhere near the queue.
    rows = _edits(invite["dataset_id"])
    assert [r["new_text"] for r in rows] == ["متن اول"]


def test_a_used_an_expired_and_an_unknown_link_all_say_the_same_thing(
        client, outbox):
    """A different page for "used" tells a stranger the token was real."""
    from app.db.connection import get_db_connection
    import secrets

    used = _booth(client, outbox, dataset_id="co-used", title="شرکت الف")
    assert client.post(f"/api/leads/edit/{used['token']}",
                       json={"text": "متن"}).status_code == 200

    expired = _booth(client, outbox, dataset_id="co-expired", title="شرکت ب",
                     phone="09121110033")
    past = (datetime.datetime.utcnow() - datetime.timedelta(minutes=1)).isoformat()
    conn = get_db_connection()
    conn.execute("UPDATE edit_invites SET expires_at = ? WHERE lead_id = ?",
                 (past, expired["lead_id"]))
    conn.commit()
    conn.close()

    unknown = secrets.token_urlsafe(32)

    pages = [client.get(f"/edit/{t}")
             for t in (used["token"], expired["token"], unknown)]
    # The status codes differ so a log can tell the three apart. The body a
    # person reads is one and the same.
    assert [p.status_code for p in pages] == [410, 410, 404]
    assert pages[0].text == pages[1].text == pages[2].text
    # Neither company is named on any of the three.
    assert "شرکت الف" not in pages[0].text
    assert "شرکت ب" not in pages[1].text

    api = [client.get(f"/api/leads/edit/{t}")
           for t in (used["token"], expired["token"], unknown)]
    assert [a.status_code for a in api] == [410, 410, 404]
    assert {a.json()["detail"] for a in api} == {DEAD_INVITE_MESSAGE}


def test_many_simultaneous_submits_produce_exactly_one_edit(client, invite):
    """The burn is the gate, and the gate is the UPDATE's own WHERE clause.

    A mock cannot build a race, so this is one database and eight real threads
    on one token. Two accepted edits here would mean a reviewer holding two
    competing texts and a contact who could keep rewriting after they were
    finished.
    """
    from concurrent.futures import ThreadPoolExecutor
    token = invite["token"]

    def submit(n):
        return client.post(f"/api/leads/edit/{token}", json={"text": f"متن شمارهٔ {n}"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = [r.status_code for r in pool.map(submit, range(8))]

    assert codes.count(200) == 1, codes
    assert set(codes) <= {200, 410}, codes

    rows = _edits(invite["dataset_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert _lead_status(invite["lead_id"]) == "completed"
