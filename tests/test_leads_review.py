"""The admin queue: approve, reject, revert, and the numbers beside them.

`review_edit` is the single writer of `dataset.text`, which is the sentence the
chatbot says to the public. So these tests are about one question: does the
text move only when an admin says so, and does it move back when they change
their mind. Around that sit the counts an operator actually runs the event on,
the stuck list and the release that puts a company back on offer.

The SMS seam is captured, not called: the rejection notice would otherwise
write the fresh invite link into the dev outbox file on disk.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "review.db"))
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
def notices(monkeypatch):
    """Capture the rejection SMS. The link in it is a live credential."""
    from app.services import sms as sms_service
    sent = []
    monkeypatch.setattr(sms_service, "send_reject_notice",
                        lambda dest, link, reference="": sent.append((dest, link, reference)))
    return sent


@pytest.fixture
def reindexes(monkeypatch):
    """Count the reindex calls. An approved text nobody indexed is a text the
    chatbot still cannot say."""
    import app.routers.dataset as dataset_router
    calls = []
    monkeypatch.setattr(dataset_router, "_trigger_reindex", lambda: calls.append(1))
    return calls


@pytest.fixture
def client(paths):
    from app.main import app
    with TestClient(app) as c:
        yield c


LIVE_TEXT = "متن قدیمی که تیم پادیار نوشته است."
NEW_TEXT = "متن درست شرکت را خودمان نوشتیم."


def _admin(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.auth.csrf import token_for_session
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    client.headers.update({"X-CSRF-Token": token_for_session(token)})
    return token


def _add_company(dataset_id, title, text=LIVE_TEXT):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, ?, '', '', '', 10)", (dataset_id, title, text))
    conn.commit()
    conn.close()


PHONE = "09121110022"


def _booth(client, outbox, dataset_id="co-a", title="پارس فناوران آریا",
           text=LIVE_TEXT, phone=PHONE):
    """A capture carried to `verified`, with the contact's token in hand."""
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
    return {"token": _mint_token(lead_id, dataset_id), "lead_id": lead_id,
            "dataset_id": dataset_id, "company": title, "live_text": text,
            "visitor_id": made["id"], "visitor_code": made["code"]}


def _mint_token(lead_id, dataset_id):
    """The raw invite token, which no API response ever carries by design."""
    from app.services import leads as leads_service
    url = leads_service.create_invite(lead_id, dataset_id, "http://testserver")["invite_url"]
    return url.rsplit("/", 1)[1]


def _as_contact(client):
    from app.services import leads as leads_service
    client.cookies.delete(leads_service.VISITOR_COOKIE)


def _submit(client, token, text=NEW_TEXT):
    sent = client.post(f"/api/leads/edit/{token}", json={"text": text})
    assert sent.status_code == 200, sent.text
    return sent


def _live_text(dataset_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return conn.execute("SELECT text FROM dataset WHERE id = ?",
                            (dataset_id,)).fetchone()["text"]
    finally:
        conn.close()


def _edits(dataset_id=None):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        if dataset_id is None:
            rows = conn.execute("SELECT * FROM dataset_edits ORDER BY created_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dataset_edits WHERE dataset_id = ? ORDER BY created_at",
                (dataset_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _lead(lead_id, dataset_id="co-x", status="verified", created_at=None,
          verified_at=None, released_at=None, override_of=None):
    """A registration written the way the product writes one."""
    from app.db.connection import get_db_connection
    stamp = created_at or "2026-08-24T09:00:00"
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
        " first_name, phone, phone_hash, status, created_at, verified_at,"
        " released_at, duplicate_override_of) VALUES (?, ?, ?, 'v1', 'مخاطب',"
        " ?, 'h', ?, ?, ?, ?, ?)",
        (lead_id, dataset_id, f"شرکت {lead_id}", PHONE, status, stamp,
         verified_at or (stamp if status in ("verified", "completed") else None),
         released_at, override_of))
    conn.commit()
    conn.close()


# ── The queue an admin reads ────────────────────────────────────────────

def test_the_pending_queue_carries_the_contact_and_masks_the_phone(
        client, outbox, notices):
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"])
    _admin(client)

    listed = client.get("/admin/api/leads/edits")
    assert listed.status_code == 200, listed.text
    edits = listed.json()["edits"]
    assert len(edits) == 1
    row = edits[0]

    assert row["status"] == "pending"
    assert row["dataset_id"] == booth["dataset_id"]
    assert row["company_name"] == booth["company"]
    assert row["first_name"] == "مینا"
    assert row["position"] == "مدیر فروش"
    assert row["old_text"] == LIVE_TEXT
    assert row["new_text"] == NEW_TEXT
    assert row["reviewed_by"] == ""

    # The reviewer needs to recognise the number, not to be handed it.
    assert "*" in row["phone"]
    assert PHONE not in listed.text
    # Named columns, not `SELECT *`: the join must not leak the rest of the row.
    for hidden in ("phone_hash", "challenge_id", "verified_at", "ip", "user_agent"):
        assert hidden not in row


def test_approving_writes_the_text_and_reindexes(client, outbox, notices, reindexes):
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"])
    assert _live_text(booth["dataset_id"]) == LIVE_TEXT
    assert reindexes == []

    _admin(client)
    edit_id = client.get("/admin/api/leads/edits").json()["edits"][0]["id"]
    approved = client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    assert _live_text(booth["dataset_id"]) == NEW_TEXT
    # An approved text the retrieval index never saw is a text the chatbot
    # still cannot say.
    assert len(reindexes) == 1

    row = _edits(booth["dataset_id"])[0]
    assert row["status"] == "approved"
    assert row["reviewed_at"]
    # The admin's username, not a slice of their session cookie.
    assert row["reviewed_by"] == "tester"
    assert client.get("/admin/api/leads/edits").json()["edits"] == []
    # Approval is silent: the text on the chatbot is the notification.
    assert notices == []


def test_rejecting_leaves_the_live_answer_untouched_and_tells_the_contact(
        client, outbox, notices, reindexes):
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"])

    _admin(client)
    edit_id = client.get("/admin/api/leads/edits").json()["edits"][0]["id"]
    rejected = client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": False})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["notified"] is True

    assert _live_text(booth["dataset_id"]) == LIVE_TEXT
    assert reindexes == []
    assert _edits(booth["dataset_id"])[0]["status"] == "rejected"

    # A refusal the contact never hears about is a refusal that ends the
    # conversation, so the notice carries a fresh working link.
    assert len(notices) == 1
    destination, link, _reference = notices[0]
    assert destination == PHONE
    fresh = link.rsplit("/", 1)[1]
    assert fresh != booth["token"]
    _as_contact(client)
    assert client.get(f"/api/leads/edit/{fresh}").status_code == 200
    # And the burnt one stays burnt.
    assert client.get(f"/api/leads/edit/{booth['token']}").status_code == 410


def test_the_rejection_notice_is_traceable_to_its_lead(client, outbox, notices):
    """FAILING, and the product is what is wrong.

    `review_edit` sends the rejection notice with no `reference`
    (app/services/leads.py:1347), while the invite notice passes the lead id
    (app/services/leads.py:1053). In app/services/sms.py the success log line
    is gated on `if reference:` (app/services/sms.py:736), so on a real gateway
    a rejection SMS that WORKED writes no row at all: no msgid, no destination,
    nothing tying it to a registration. The failure is audited, the success is
    not, and "did this contact ever hear that we refused their text" is exactly
    the question an operator asks a week later.
    """
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"])
    _admin(client)
    edit_id = client.get("/admin/api/leads/edits").json()["edits"][0]["id"]
    client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": False})

    assert len(notices) == 1
    assert notices[0][2] == booth["lead_id"]


def test_an_already_reviewed_edit_cannot_be_reviewed_again(
        client, outbox, notices, reindexes):
    """Two clicks on one row must not write the text twice, and must not let a
    reject undo an approve behind the reviewer's back."""
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"])
    _admin(client)
    edit_id = client.get("/admin/api/leads/edits").json()["edits"][0]["id"]
    assert client.post(f"/admin/api/leads/edits/{edit_id}",
                       json={"approve": True}).status_code == 200

    again = client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": True})
    assert again.status_code == 400, again.text
    flipped = client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": False})
    assert flipped.status_code == 400, flipped.text

    assert _live_text(booth["dataset_id"]) == NEW_TEXT
    assert _edits(booth["dataset_id"])[0]["status"] == "approved"
    assert len(reindexes) == 1
    assert notices == []

    unknown = client.post("/admin/api/leads/edits/no-such-edit", json={"approve": True})
    assert unknown.status_code == 404


def test_a_second_submission_supersedes_the_first(client, outbox, notices):
    """One company, one pending edit. Two competing drafts would leave the
    reviewer reconciling texts nobody appointed them to reconcile."""
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"], "متن اول")
    second = _mint_token(booth["lead_id"], booth["dataset_id"])
    _submit(client, second, "متن دوم")

    rows = _edits(booth["dataset_id"])
    assert [r["status"] for r in rows] == ["superseded", "pending"]
    assert rows[0]["new_text"] == "متن اول"
    assert rows[1]["new_text"] == "متن دوم"

    _admin(client)
    queue = client.get("/admin/api/leads/edits").json()["edits"]
    assert len(queue) == 1
    assert queue[0]["new_text"] == "متن دوم"
    # Neither draft reached the public.
    assert _live_text(booth["dataset_id"]) == LIVE_TEXT


def test_revert_puts_the_old_text_back_and_only_once(
        client, outbox, notices, reindexes):
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"])
    _admin(client)
    edit_id = client.get("/admin/api/leads/edits").json()["edits"][0]["id"]

    # A pending edit has replaced nothing, so there is nothing to put back.
    too_early = client.post(f"/admin/api/leads/edits/{edit_id}/revert")
    assert too_early.status_code == 400, too_early.text

    client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": True})
    assert _live_text(booth["dataset_id"]) == NEW_TEXT

    reverted = client.post(f"/admin/api/leads/edits/{edit_id}/revert")
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["status"] == "reverted"
    assert _live_text(booth["dataset_id"]) == LIVE_TEXT
    assert _edits(booth["dataset_id"])[0]["status"] == "reverted"
    # The approval indexed the new text; the revert has to index the old one.
    assert len(reindexes) == 2

    # A second revert would put a stale text over whatever came after it.
    assert client.post(f"/admin/api/leads/edits/{edit_id}/revert").status_code == 400
    assert _live_text(booth["dataset_id"]) == LIVE_TEXT


# ── The numbers the event is run on ─────────────────────────────────────

def test_the_funnel_counts_the_three_states(client, outbox, notices):
    _lead("l1", "co-1", status="unverified")
    _lead("l2", "co-2", status="unverified")
    _lead("l3", "co-3", status="verified")
    _lead("l4", "co-4", status="completed")
    _lead("l5", "co-5", status="completed", override_of="l1")
    _admin(client)

    counted = client.get("/admin/api/leads/funnel")
    assert counted.status_code == 200, counted.text
    body = counted.json()
    assert body["total"] == 5
    assert body["unverified"] == 2
    assert body["verified"] == 1
    assert body["completed"] == 2
    assert body["unverified"] + body["verified"] + body["completed"] == body["total"]
    # Not stages. An override is an exception an operator should watch.
    assert body["overrides"] == 1
    assert body["released"] == 0
    assert body["pending_review"] == 0


def test_the_stuck_list_matches_the_verified_number_on_the_funnel(
        client, outbox, notices):
    """Two counts of the same thing that disagree read as a bug, so the stuck
    list has no age threshold: every registration resting at `verified` is on
    it, including the one that verified a minute ago."""
    _lead("waiting-long", "co-1", status="verified",
          created_at="2026-08-20T09:00:00", verified_at="2026-08-20T09:05:00")
    _lead("waiting-short", "co-2", status="verified",
          created_at=datetime.datetime.utcnow().isoformat(),
          verified_at=datetime.datetime.utcnow().isoformat())
    _lead("done", "co-3", status="completed")
    _lead("never-answered", "co-4", status="unverified")
    _admin(client)

    stuck = client.get("/admin/api/leads/stuck")
    assert stuck.status_code == 200, stuck.text
    rows = stuck.json()["stuck"]
    assert {r["id"] for r in rows} == {"waiting-long", "waiting-short"}
    assert len(rows) == client.get("/admin/api/leads/funnel").json()["verified"]

    # Oldest first: the operator works down the list.
    assert rows[0]["id"] == "waiting-long"
    assert rows[0]["waiting_hours"] > rows[1]["waiting_hours"]
    assert rows[1]["waiting_hours"] >= 0
    assert "*" in rows[0]["phone"]
    assert PHONE not in stuck.text


def test_releasing_a_lead_puts_the_company_back_in_the_search_pool(
        client, outbox, notices):
    booth = _booth(client, outbox)
    # Setup: the company is claimed, so it is on nobody's list.
    assert client.get("/api/leads/companies").json()["companies"] == []

    _admin(client)
    released = client.post(f"/admin/api/leads/{booth['lead_id']}/release")
    assert released.status_code == 200, released.text

    listed = client.get("/api/leads/companies").json()["companies"]
    assert [c["id"] for c in listed] == [booth["dataset_id"]]
    # And a colleague can register it for real this time.
    second = client.post("/api/leads/register", json={
        "dataset_id": booth["dataset_id"], "first_name": "علی",
        "last_name": "کریمی", "position": "مدیر", "phone": "09121110099"})
    assert second.status_code == 200, second.text

    # The released registration's own link dies with it, so an old invite
    # cannot land an edit on a company somebody else is now registering.
    _as_contact(client)
    assert client.get(f"/api/leads/edit/{booth['token']}").status_code == 404

    # The registration keeps `verified` for the history, so the funnel and the
    # stuck list reconcile through `released` rather than by disagreeing.
    funnel = client.get("/admin/api/leads/funnel").json()
    assert funnel["verified"] == 1
    assert funnel["released"] == 1
    assert client.get("/admin/api/leads/stuck").json()["stuck"] == []

    # Releasing the same registration twice changes nothing.
    assert client.post(f"/admin/api/leads/{booth['lead_id']}/release").status_code == 404
    assert client.post("/admin/api/leads/no-such-lead/release").status_code == 404


# ── The door ────────────────────────────────────────────────────────────

def test_the_review_routes_need_an_admin_session(client, outbox, notices):
    booth = _booth(client, outbox)
    _as_contact(client)
    _submit(client, booth["token"])

    assert client.get("/admin/api/leads/edits").status_code == 401
    assert client.get("/admin/api/leads/funnel").status_code == 401
    assert client.get("/admin/api/leads/stuck").status_code == 401
    assert client.post("/admin/api/leads/edits/x", json={"approve": True}).status_code == 401
    assert client.post("/admin/api/leads/edits/x/revert").status_code == 401
    assert client.post(f"/admin/api/leads/{booth['lead_id']}/release").status_code == 401

    # A session without the CSRF token is refused with a DIFFERENT code, so an
    # operator can tell "not logged in" from "token missing".
    _admin(client)
    edit_id = client.get("/admin/api/leads/edits").json()["edits"][0]["id"]
    client.headers.pop("X-CSRF-Token")
    blocked = client.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": True})
    assert blocked.status_code == 403, blocked.text
    assert _live_text(booth["dataset_id"]) == LIVE_TEXT
