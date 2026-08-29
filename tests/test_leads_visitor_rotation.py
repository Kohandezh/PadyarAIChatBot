"""A booth phone is lost, and the operator gives that colleague a new link.

WHAT WAS BROKEN: rotating the code changed only the `code` column, but the
/v session cookie carried `lead_visitors.id`, the primary key, and
`current_visitor()` resolved it with `visitor_by_id()`. The row id never
changes, so the lost phone kept full staff access for the rest of the 12
hour session, while `rotate_visitor_code`'s docstring told the operator
"The old one stops working immediately." A row id the client sends back is
not a credential. The cookie now carries the CODE, so rotating it kills
every live session by construction.

The tests are shaped around the operator's scenario, not around the
function: an old cookie on the very next request, a fresh link, and the row
id offered as a cookie by someone who read it off the admin roster. All
three go through the real endpoints, because the bug lived in the wiring
between the router and the service, not inside either one.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """A running install with an empty DB. No admin session: the visitor
    side is a different door and needs none."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads_rotation.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _visitor_client(app_client, code):
    """A second browser, holding only what /v/{code} handed it.

    A fresh TestClient rather than reusing `app_client`, so a test can keep
    the old phone's cookie jar and the new phone's side by side. No `with`:
    `app_client` already started the app, and starting it twice would run the
    startup hooks against the same temp database a second time.
    """
    from app.main import app
    phone = TestClient(app)
    r = phone.get(f"/v/{code}")
    assert r.status_code == 200, "the personal link did not open the panel"
    return phone


def test_rotating_the_code_signs_the_old_phone_out_on_the_next_tap(app_client):
    from app.services import leads as svc
    visitor = svc.create_visitor("زهرا")
    lost_phone = _visitor_client(app_client, visitor["code"])

    # Still working before the rotation, so a later 403 means the rotation
    # did it and not a broken fixture.
    assert lost_phone.get("/v").status_code == 200

    svc.rotate_visitor_code(visitor["id"])

    assert lost_phone.get("/v").status_code == 403, \
        "the lost phone still opens the staff panel after the link was rotated"
    assert lost_phone.get("/api/leads/mine").status_code == 401, \
        "the lost phone can still read this colleague's captured leads"


def test_the_rotated_link_works_for_the_colleague(app_client):
    from app.services import leads as svc
    visitor = svc.create_visitor("مهدی")
    _visitor_client(app_client, visitor["code"])

    new_code = svc.rotate_visitor_code(visitor["id"])
    new_phone = _visitor_client(app_client, new_code)

    assert new_phone.get("/v").status_code == 200
    assert new_phone.get("/api/leads/mine").status_code == 200


def test_the_row_id_is_not_a_key_to_the_panel(app_client):
    """The visitor id is printed in the admin roster and travels in query
    strings. Anyone who has seen one must not be able to paste it into a
    cookie and become that colleague."""
    from app.services import leads as svc
    from app.main import app
    visitor = svc.create_visitor("نازنین")

    stranger = TestClient(app)
    stranger.cookies.set(svc.VISITOR_COOKIE, visitor["id"])

    assert stranger.get("/v").status_code == 403, \
        "a visitor row id used as a cookie opened the staff panel"
    assert stranger.get("/api/leads/mine").status_code == 401


def test_revoking_a_visitor_still_ends_the_session(app_client):
    """The rotation fix must not cost the older promise: `active = false`
    takes effect on the next request too."""
    from app.services import leads as svc
    visitor = svc.create_visitor("کاوه")
    phone = _visitor_client(app_client, visitor["code"])

    svc.set_visitor_active(visitor["id"], False)

    assert phone.get("/v").status_code == 403
    assert phone.get("/api/leads/mine").status_code == 401


def test_visitor_by_id_and_visitor_by_code_answer_with_the_same_row(app_client):
    """The cookie path moved from `visitor_by_id` to `visitor_by_code`, and
    the two must hand back the same keys, or a caller reading
    `visitor["name"]` breaks somewhere no test looks.

    `visitor_by_id` has no caller left in the app after this change. It stays
    for now, pinned here, so that whoever wires it up next gets the same
    dict the session path gets.
    """
    from app.services import leads as svc
    visitor = svc.create_visitor("سارا")

    by_code = svc.visitor_by_code(visitor["code"])
    by_id = svc.visitor_by_id(visitor["id"])

    assert by_code == by_id
    assert by_id["id"] == visitor["id"] and by_id["name"] == "سارا"

    # An unknown code is a miss, not a crash: this is the value an attacker
    # controls now that the cookie carries the code.
    assert svc.visitor_by_code("no-such-code") is None
    assert svc.visitor_by_code("") is None


# ── The invite the booth phone must not use itself ────────────────────────
# The /v cookie is also what tells /edit/{token} "this browser is the booth
# that captured the lead". Moving a code into that cookie moves it into
# `edit_invites.issued_by_session` unless the router turns it back into a
# visitor id, so these cover the part of the change nothing else looks at.

def _seed_company_and_lead(visitor_id, dataset_id, status="verified"):
    """A company and the lead this visitor captured for it.

    Direct SQL, like tests/test_leads_visitors_admin.py: the OTP round trip
    that normally creates this row belongs to another module and is not what
    is under test here. Companies are their own table now
    (migrations/0013_companies.sql), not `dataset` rows.
    """
    from app.db.connection import get_db_connection
    lead_id = secrets.token_urlsafe(8)
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO companies (id, title, text) VALUES (?, ?, ?)",
                     (dataset_id, "شرکت نمونه", "متن قدیمی"))
        conn.execute(
            "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
            " phone, phone_hash, status, created_at, challenge_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_id, dataset_id, "شرکت نمونه", visitor_id, "09120000000",
             "hash", status, datetime.datetime.utcnow().isoformat(), "chal-1"),
        )
        conn.commit()
    finally:
        conn.close()
    return lead_id


def test_the_booth_phone_cannot_write_the_companys_own_answer(app_client):
    """The booth holds the QR up for the contact to scan, so the booth can
    also scan it. The invite remembers which visitor minted it and refuses
    exactly that person."""
    from app.services import leads as svc
    visitor = svc.create_visitor("پویا")
    phone = _visitor_client(app_client, visitor["code"])
    lead_id = _seed_company_and_lead(visitor["id"], "co-guard")

    invite = svc.create_invite(lead_id, "co-guard", "http://x",
                               issued_by_session=visitor["id"])
    token = invite["invite_url"].rsplit("/edit/", 1)[1]

    r = phone.post(f"/api/leads/edit/{token}", json={"text": "متن تازه"})
    assert r.status_code == 403, \
        "the booth phone submitted the company's own answer"


def test_the_contact_with_no_booth_cookie_can_still_submit(app_client):
    """The other half of the same guard. A refusal that catches everybody is
    not a guard, it is an outage on the contact's phone."""
    from app.services import leads as svc
    from app.main import app
    visitor = svc.create_visitor("رها")
    lead_id = _seed_company_and_lead(visitor["id"], "co-open")

    invite = svc.create_invite(lead_id, "co-open", "http://x",
                               issued_by_session=visitor["id"])
    token = invite["invite_url"].rsplit("/edit/", 1)[1]

    contact = TestClient(app)
    r = contact.post(f"/api/leads/edit/{token}", json={"text": "متن تازه"})
    assert r.status_code == 200, r.text


def test_revoking_the_visitor_does_not_unlock_their_own_invite(app_client):
    """Why `visitor_id_for_session` exists instead of `visitor_by_code`.

    A revoked colleague still holds the QR on their screen. The session is
    gone, but the invite must still know whose it was, or taking someone off
    the roster would hand them the company's text box.
    """
    from app.services import leads as svc
    visitor = svc.create_visitor("شیرین")
    phone = _visitor_client(app_client, visitor["code"])
    lead_id = _seed_company_and_lead(visitor["id"], "co-revoked")

    invite = svc.create_invite(lead_id, "co-revoked", "http://x",
                               issued_by_session=visitor["id"])
    token = invite["invite_url"].rsplit("/edit/", 1)[1]

    svc.set_visitor_active(visitor["id"], False)
    assert phone.get("/v").status_code == 403, "the revoked panel is still open"

    r = phone.post(f"/api/leads/edit/{token}", json={"text": "متن تازه"})
    assert r.status_code == 403, \
        "a revoked colleague can write the company's answer with their old QR"


def test_the_invite_never_stores_the_visitors_live_code(app_client, monkeypatch):
    """`edit_invites.issued_by_session` is written when the contact's code is
    verified, and it is stored in the clear. Before this change it held the
    cookie, which was a row id. Now the cookie is the visitor's live personal
    link, so writing the cookie there would put a working /v link into a
    table an export, an admin read or a backup hands out.

    The SMS code check is patched to pass: which digits the contact read out
    is the OTP module's business, and this test is about what the leads
    module writes down afterwards.
    """
    from app.services import leads as svc
    from app.services import otp as otp_service
    monkeypatch.setattr(otp_service, "verify", lambda challenge_id, code: (True, ""))

    visitor = svc.create_visitor("بهار")
    phone = _visitor_client(app_client, visitor["code"])
    lead_id = _seed_company_and_lead(visitor["id"], "co-plain", status="unverified")

    r = phone.post("/api/leads/verify", json={"lead_id": lead_id, "code": "12345"})
    assert r.status_code == 200, r.text

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT issued_by_session FROM edit_invites").fetchall()
    finally:
        conn.close()
    assert rows, "verifying the contact did not mint an invite"
    for row in rows:
        assert row["issued_by_session"] != visitor["code"], \
            "the visitor's personal link code is stored in edit_invites"
        assert row["issued_by_session"] == visitor["id"], \
            "the invite no longer remembers which booth phone minted it"


def test_rotation_does_not_unlock_the_lost_phones_own_invite(app_client):
    """The regression rotation itself introduced, and the reason for
    `from_booth_phone`.

    `issued_by_session` stores a lead_visitors.id. The cookie now carries the
    CODE, and `visitor_id_for_session` maps code back to id. Rotation replaces
    the code, so after a rotation that mapping returns "" and the guard in
    `submit_edit` had nothing to compare. It fell open.

    That is the exact story rotation is used for. A staff phone captures a
    lead, mints an invite, and the QR is sitting on its screen. The phone is
    lost. The operator rotates the link, so /v correctly answers 403. But the
    finder could still scan the QR already on the screen and write the
    company's own answer. Before the cookie moved to the code, that was
    refused, because the id in the cookie matched the id on the invite.

    The fix is not to make the mapping survive rotation. It is that only booth
    staff ever hold a /v cookie, so a request that HAS one and resolves to
    nobody is a phone that was cut off, and it is refused.
    """
    from app.services import leads as svc
    visitor = svc.create_visitor("نگار")
    phone = _visitor_client(app_client, visitor["code"])
    lead_id = _seed_company_and_lead(visitor["id"], "co-rotated")

    invite = svc.create_invite(lead_id, "co-rotated", "http://x",
                               issued_by_session=visitor["id"])
    token = invite["invite_url"].rsplit("/edit/", 1)[1]

    svc.rotate_visitor_code(visitor["id"])
    assert phone.get("/v").status_code == 403, "the rotated panel is still open"

    r = phone.post(f"/api/leads/edit/{token}", json={"text": "متن تازه"})
    assert r.status_code == 403, \
        "after a rotation the lost phone can still write the company's own answer"


def test_a_cut_off_phone_cannot_write_someone_elses_invite_either(app_client):
    """A rotated phone is refused on EVERY invite, not only the one it minted.

    This is stricter than the old rule, on purpose, and the cost is named
    here so nobody removes it as an accident: a staff member whose link was
    just rotated cannot submit an invite a colleague minted until they open
    their new link. That is one extra tap for a person who still works here,
    and the alternative is leaving a lost phone able to write.
    """
    from app.services import leads as svc
    mine = svc.create_visitor("سارا")
    theirs = svc.create_visitor("رضا")
    phone = _visitor_client(app_client, mine["code"])
    lead_id = _seed_company_and_lead(theirs["id"], "co-other")

    invite = svc.create_invite(lead_id, "co-other", "http://x",
                               issued_by_session=theirs["id"])
    token = invite["invite_url"].rsplit("/edit/", 1)[1]

    # Still on the roster, holding a colleague's invite: allowed, as before.
    ok = phone.post(f"/api/leads/edit/{token}", json={"text": "متن اول"})
    assert ok.status_code == 200, ok.text

    invite2 = svc.create_invite(lead_id, "co-other", "http://x",
                                issued_by_session=theirs["id"])
    token2 = invite2["invite_url"].rsplit("/edit/", 1)[1]

    svc.rotate_visitor_code(mine["id"])
    cut_off = phone.post(f"/api/leads/edit/{token2}", json={"text": "متن دوم"})
    assert cut_off.status_code == 403, \
        "a phone whose link was rotated still wrote a colleague's invite"
