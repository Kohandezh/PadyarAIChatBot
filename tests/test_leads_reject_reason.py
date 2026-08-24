"""Why a text was refused, and the two states that must never look alike.

`dataset_edits` used to record who reviewed and when, and nothing about why. So
a rejection reached a company manager as "not approved", they opened their page,
read back their own words, and had nothing to change. The only move left was to
send the same text again.

These cases hold the fix in place:

  * a rejection cannot be made without a reason, and a refused rejection leaves
    the edit pending rather than half-reviewed;
  * the reason reaches the contact on both pages they can arrive on, the invite
    page the rejection notice opens and `/my`;
  * an approval invents no reason and carries none, on any row;
  * a resubmission clears the old reason instead of standing it next to text it
    was never about;
  * "you have not sent anything yet" and "an administrator refused your text"
    are two different states, with two different tones and one reason line
    between them.

The last one is the whole point. Telling somebody their text was turned down
when they never sent one is worse than saying nothing.
"""
import datetime
import re
import secrets

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_db_connection
from app.services import identity as identity_service
from app.services import leads as leads_service

LIVE_TEXT = "متن قدیمی که تیم پادیار نوشته است."
CONTACT_TEXT = "متن معرفی شرکت را خودمان نوشتیم. شماره تماس ما ۰۹۱۲۱۱۱۲۲۳۳ است."
REASON = "شمارهٔ تماس را از متن بردارید."
PHONE = "09121110022"


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "reason.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    leads_service.ensure_tables()
    identity_service.ensure_tables()
    return tmp_path


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    import app.routers.leads as leads_router
    monkeypatch.setattr(leads_router, "check_rate_limit", lambda *a, **k: None)


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
    monkeypatch.setattr(sms_service, "send_invite_link",
                        lambda dest, link, reference="": sent.append((dest, link, reference)))
    return sent


@pytest.fixture(autouse=True)
def _no_reindex(monkeypatch):
    import app.routers.dataset as dataset_router
    monkeypatch.setattr(dataset_router, "_trigger_reindex", lambda: None)


@pytest.fixture
def make_client(paths):
    from app.main import app
    clients = []

    def factory():
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c
    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


# ── Helpers ──────────────────────────────────────────────────────────────

def _rows(sql, params=()):
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _admin(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.auth.csrf import token_for_session
    token = secrets.token_hex(16)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, "tester",
                  (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    client.headers.update({"X-CSRF-Token": token_for_session(token)})
    return token


def _company(dataset_id="co-a", title="پارس فناوران آریا", text=LIVE_TEXT):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, ?, '', '', '', 10)", (dataset_id, title, text))
    conn.commit()
    conn.close()
    return dataset_id


def _booth(client, outbox, dataset_id="co-a", phone=PHONE):
    """A capture carried to `verified`, with the contact's link in hand."""
    _company(dataset_id)
    made = leads_service.create_visitor("همکار غرفه")
    assert client.get(f"/v/{made['code']}", follow_redirects=False).status_code == 303
    registered = client.post("/api/leads/register", json={
        "dataset_id": dataset_id, "first_name": "مینا", "last_name": "رضایی",
        "position": "مدیر فروش", "phone": phone})
    assert registered.status_code == 200, registered.text
    lead_id = registered.json()["lead_id"]
    verified = client.post("/api/leads/verify",
                           json={"lead_id": lead_id, "code": outbox[-1][1]})
    assert verified.status_code == 200, verified.text
    client.cookies.delete(leads_service.VISITOR_COOKIE)
    return {"lead_id": lead_id, "dataset_id": dataset_id,
            "token": _token(lead_id, dataset_id)}


def _token(lead_id, dataset_id):
    url = leads_service.create_invite(lead_id, dataset_id, "http://testserver")["invite_url"]
    return url.rsplit("/", 1)[1]


def _submit(client, token, text=CONTACT_TEXT):
    sent = client.post(f"/api/leads/edit/{token}", json={"text": text})
    assert sent.status_code == 200, sent.text


def _pending_id(client):
    return client.get("/admin/api/leads/edits").json()["edits"][0]["id"]


def _review(client, edit_id, approve, note=""):
    return client.post(f"/admin/api/leads/edits/{edit_id}",
                       json={"approve": approve, "note": note})


def _login(client, phone, outbox):
    asked = client.post("/api/auth/login/request", json={"phone": phone})
    assert asked.status_code == 200, asked.text
    done = client.post("/api/auth/login/verify",
                       json={"challenge_id": asked.json()["challenge_id"],
                             "code": outbox[-1][1]})
    assert done.status_code == 200, done.text


# ── The reviewer cannot refuse in silence ───────────────────────────────

def test_a_rejection_without_a_reason_is_refused_and_changes_nothing(
        client, outbox, notices):
    """The whole defect in one case.

    An empty rejection is what left the contact with nothing to act on, so it
    is not accepted at all. And the refusal has to be total: an edit that came
    back `400` must still be sitting in the queue, still `pending`, with no
    reviewer and no SMS spent on it.
    """
    booth = _booth(client, outbox)
    _submit(client, booth["token"])
    _admin(client)
    edit_id = _pending_id(client)

    for empty in ("", "   ", "..."):
        refused = _review(client, edit_id, False, empty)
        assert refused.status_code == 400, refused.text
        assert "دلیل" in refused.json()["detail"]

    row = _rows("SELECT status, reviewed_by, review_note FROM dataset_edits")[0]
    assert row["status"] == "pending"
    assert row["reviewed_by"] == ""
    assert row["review_note"] == ""
    assert notices == []
    assert len(client.get("/admin/api/leads/edits").json()["edits"]) == 1


def test_a_reason_with_markup_is_refused(client, outbox, notices):
    """An administrator writes this and an outside contact's browser renders
    it. The panel is used by non-technical staff, and a fragment pasted out of
    a document must not become markup on somebody else's phone."""
    booth = _booth(client, outbox)
    _submit(client, booth["token"])
    _admin(client)
    refused = _review(client, _pending_id(client), False,
                      "<b>متن</b> را ساده‌تر بنویسید.")
    assert refused.status_code == 400, refused.text
    assert _rows("SELECT status FROM dataset_edits")[0]["status"] == "pending"


def test_an_approval_carries_no_reason_and_invents_none(client, outbox, notices):
    """Approving asks for nothing and stores nothing.

    A note typed beside an approval is dropped rather than kept: the text lands
    on the chatbot, nobody is ever shown a sentence about it, and a stored
    reason nobody reads is a reason that misleads whoever finds it later.
    """
    booth = _booth(client, outbox)
    _submit(client, booth["token"])
    _admin(client)
    edit_id = _pending_id(client)

    approved = _review(client, edit_id, True, "این یادداشت جایی نمی‌رود.")
    assert approved.status_code == 200, approved.text
    assert approved.json()["note"] == ""

    row = _rows("SELECT status, review_note FROM dataset_edits")[0]
    assert row["status"] == "approved"
    assert row["review_note"] == ""
    assert notices == []


# ── The reason reaches the person who has to act on it ──────────────────

def test_the_reason_reaches_the_contact_on_the_page_the_notice_opens(
        client, outbox, notices):
    """The rejection SMS is a fixed approved template: a link and no prose.

    So the sentence the reviewer wrote has exactly one place to be read, and it
    is the page that link opens. The contact also gets their own refused words
    back in the box, because "اصلاح کنید" is not the same task as retyping four
    hundred words from memory.
    """
    booth = _booth(client, outbox)
    _submit(client, booth["token"])
    _admin(client)
    assert _review(client, _pending_id(client), False, REASON).status_code == 200

    assert len(notices) == 1
    fresh = notices[0][1].rsplit("/", 1)[1]
    # The reviewer's sentence is not in the SMS. It cannot be: the line is a
    # service line and only an approved template with fixed wording arrives.
    assert REASON not in notices[0][1]

    page = client.get(f"/api/leads/edit/{fresh}")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["submission"] == {"status": "rejected", "reason": REASON}
    assert body["pending"] is False
    assert body["text"] == CONTACT_TEXT


def test_the_reason_reaches_the_contact_on_my(client, make_client, outbox, notices):
    """The same answer through the other door.

    The invite dies; the account does not. Somebody coming back a week later
    with their phone number has to find the same explanation waiting.
    """
    booth = _booth(client, outbox)
    _submit(client, booth["token"])
    _admin(client)
    assert _review(client, _pending_id(client), False, REASON).status_code == 200

    contact = make_client()
    _login(contact, PHONE, outbox)
    listed = contact.get("/api/my/companies")
    assert listed.status_code == 200, listed.text
    company = listed.json()["companies"][0]
    assert company["submission"] == {"status": "rejected", "reason": REASON}
    assert company["text"] == CONTACT_TEXT

    one = contact.get(f"/api/my/edit/{company['id']}")
    assert one.json()["submission"] == {"status": "rejected", "reason": REASON}


def test_a_resubmission_clears_the_old_reason(client, outbox, notices):
    """A stale reason beside new text is a lie about the new text.

    The reason belongs to the row it was written on. A corrected text is a NEW
    row, so what the page reads is `pending` with nothing beside it, and the
    old sentence stays where it happened for the audit trail.
    """
    booth = _booth(client, outbox)
    _submit(client, booth["token"])
    _admin(client)
    assert _review(client, _pending_id(client), False, REASON).status_code == 200

    fresh = notices[0][1].rsplit("/", 1)[1]
    _submit(client, fresh, "متن اصلاح‌شده، بدون شمارهٔ تماس.")

    page = client.get(f"/api/leads/edit/{_token(booth['lead_id'], booth['dataset_id'])}")
    assert page.json()["submission"] == {"status": "pending", "reason": ""}

    # Both rows are still there. The rejected one keeps its note; the new one
    # never had one.
    rows = _rows("SELECT status, review_note FROM dataset_edits ORDER BY created_at")
    assert [r["status"] for r in rows] == ["rejected", "pending"]
    assert rows[0]["review_note"] == REASON
    assert rows[1]["review_note"] == ""


# ── The two states are not the same state ───────────────────────────────

def test_a_contact_who_never_sent_anything_is_told_nothing_was_refused(
        client, make_client, outbox, notices):
    """State 2. Nothing was rejected, so there is no reason and no blame.

    This is the mistake worth avoiding above all the others here: a company
    manager who has simply not got round to it must not read that their text
    was turned down. `none` is the vocabulary for it, and it carries an empty
    reason so nothing downstream can render one.
    """
    _company("co-quiet", "شرکت ساکت")
    made = leads_service.create_visitor("همکار")
    assert client.get(f"/v/{made['code']}", follow_redirects=False).status_code == 303
    registered = client.post("/api/leads/register", json={
        "dataset_id": "co-quiet", "first_name": "رضا", "phone": PHONE})
    client.post("/api/leads/verify", json={"lead_id": registered.json()["lead_id"],
                                           "code": outbox[-1][1]})

    contact = make_client()
    _login(contact, PHONE, outbox)
    company = contact.get("/api/my/companies").json()["companies"][0]
    assert company["submission"] == {"status": "none", "reason": ""}
    # The live answer, not a draft: they have not written one.
    assert company["text"] == LIVE_TEXT
    assert not _rows("SELECT id FROM dataset_edits")


def test_the_page_gives_the_two_states_different_words_and_different_colours():
    """The distinction has to survive into what a person actually sees.

    The service can return `none` and `rejected` perfectly and still fail the
    person if the page paints them the same, so this reads the page. `none`
    gets the calm tone and no reason line; `rejected` gets the refusal tone and
    the one line that says what to change.
    """
    import os
    from app.config import BASE_DIR

    with open(os.path.join(BASE_DIR, "static", "leads", "my.js"), encoding="utf-8") as f:
        js = f.read()
    states = dict(re.findall(r"^\s*(none|pending|approved|rejected):\s*\['(\w+)'",
                             js, re.M))
    assert states["none"] == "info"
    assert states["rejected"] == "bad"
    # The reason line belongs to a rejection and to nothing else.
    assert "rejectedWithReason = info.status === 'rejected' && info.reason" in js
    assert "el('state-reason').hidden = !rejectedWithReason" in js

    with open(os.path.join(BASE_DIR, "static", "leads", "leads.css"), encoding="utf-8") as f:
        css = f.read()
    assert ".state.info" in css and ".state.bad" in css


@pytest.mark.parametrize("page", ["my.js", "edit.js"])
def test_the_reason_only_ever_reaches_the_page_as_text(page):
    """An administrator wrote it and a stranger's browser renders it.

    Both contact-facing pages show it, so both are read. Every line that puts
    the reason on screen has to be a textContent assignment: the panel is used
    by non-technical staff and a fragment pasted out of a document must not
    become markup on somebody else's phone.
    """
    import os
    from app.config import BASE_DIR

    with open(os.path.join(BASE_DIR, "static", "leads", page), encoding="utf-8") as f:
        lines = [line for line in f if "reason" in line.lower()]
    assert lines, f"{page} does not render the reviewer's reason at all"
    assert not [line for line in lines if "innerHTML" in line]
    assert [line for line in lines if ".textContent" in line]


# ── Nobody was reaching the quiet ones at all ───────────────────────────

def test_an_admin_can_send_a_stuck_contact_their_link_again(
        client, outbox, notices):
    """Until this existed, a contact who verified and went quiet heard nothing.

    `verified` is a normal resting state, not a failure, so the answer is one
    operator pressing one button for one person: a fresh link by SMS, and the
    company stays theirs. Releasing is still the other button, for when there
    is no hope left.
    """
    _company("co-quiet", "شرکت ساکت")
    made = leads_service.create_visitor("همکار")
    assert client.get(f"/v/{made['code']}", follow_redirects=False).status_code == 303
    registered = client.post("/api/leads/register", json={
        "dataset_id": "co-quiet", "first_name": "رضا", "phone": PHONE})
    lead_id = registered.json()["lead_id"]
    client.post("/api/leads/verify", json={"lead_id": lead_id, "code": outbox[-1][1]})
    client.cookies.delete(leads_service.VISITOR_COOKIE)
    notices.clear()

    _admin(client)
    reminded = client.post(f"/admin/api/leads/{lead_id}/remind")
    assert reminded.status_code == 200, reminded.text
    assert reminded.json()["notified"] is True
    assert "*" in reminded.json()["destination_masked"]

    assert len(notices) == 1
    destination, link, reference = notices[0]
    assert destination == PHONE
    assert reference == lead_id
    # A working link, or the message is worse than none.
    client.cookies.clear()
    assert client.get(f"/api/leads/edit/{link.rsplit('/', 1)[1]}").status_code == 200


def test_a_contact_who_already_answered_is_not_nudged(client, outbox, notices):
    """`completed` has nothing to be reminded about, and a registration that
    never verified has no link to send."""
    booth = _booth(client, outbox)
    _submit(client, booth["token"])
    _admin(client)
    assert client.post(f"/admin/api/leads/{booth['lead_id']}/remind").status_code == 404
    assert client.post("/admin/api/leads/no-such-lead/remind").status_code == 404


def test_the_reminder_needs_an_admin_session(client, outbox, notices):
    booth = _booth(client, outbox)
    assert client.post(f"/admin/api/leads/{booth['lead_id']}/remind").status_code == 401
