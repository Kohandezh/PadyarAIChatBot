"""The field visitor's panel: the session, the search, and the registration.

Everything a visitor can do goes through one door, the cookie their personal
link sets, and through one list, the companies nobody has claimed yet. These
tests hold down the three things that door has to guarantee:

  * a revoked colleague stops working on their NEXT tap, not at cookie expiry,
    because the cookie carries only an id and `active` is re-read every time;
  * a company somebody already verified is gone from every visitor's search,
    so two people cannot work the same booth;
  * a repeated phone number warns and costs nothing, and going ahead anyway is
    an explicit act that gets written down.

Delivery is captured at the same seam tests/test_otp.py uses, so no SMS is
attempted and the raw code never reaches a log. Nothing here asserts how long
a session lives or how a visitor code is stored: both are being changed.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """A throwaway install with an empty knowledge base."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "visitor.db"))
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
    """The suite fires a whole booth's worth of requests in a second."""
    import app.routers.leads as leads_router
    # Signature-agnostic: the lead routes pass their own ceiling through, and
    # that call shape is being changed in a parallel piece of work.
    monkeypatch.setattr(leads_router, "check_rate_limit",
                        lambda *args, **kwargs: None)


@pytest.fixture
def outbox(monkeypatch):
    """Capture the delivered code instead of texting anyone."""
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


@pytest.fixture
def visitor(client):
    """A field visitor holding the session their personal link set."""
    return _sign_in(client, "همکار الف")


def _sign_in(client, name):
    """Create a visitor and exchange their personal link for the cookie."""
    from app.services import leads as leads_service
    made = leads_service.create_visitor(name)
    opened = client.get(f"/v/{made['code']}", follow_redirects=False)
    assert opened.status_code == 303, opened.text
    return made


def _admin(client):
    """A real admin session row, plus the CSRF header its mutations need."""
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


def _add_company(dataset_id, title, text="متن قدیمی"):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, ?, '', '', '', 10)", (dataset_id, title, text))
    conn.commit()
    conn.close()


def _leads(dataset_id=None):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        if dataset_id is None:
            rows = conn.execute(
                "SELECT * FROM company_leads ORDER BY created_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM company_leads WHERE dataset_id = ? ORDER BY created_at",
                (dataset_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


PHONE = "09121110022"


def _register(client, dataset_id, phone=PHONE, **overrides):
    body = {"dataset_id": dataset_id, "first_name": "مینا", "last_name": "رضایی",
            "position": "مدیر فروش", "phone": phone}
    body.update(overrides)
    return client.post("/api/leads/register", json=body)


def _verify(client, lead_id, outbox):
    done = client.post("/api/leads/verify",
                       json={"lead_id": lead_id, "code": outbox[-1][1]})
    assert done.status_code == 200, done.text
    return done


# ── The door ────────────────────────────────────────────────────────────

def test_a_personal_link_opens_the_panel_and_keeps_the_code_out_of_the_url(client):
    from app.services import leads as leads_service
    made = leads_service.create_visitor("همکار الف")

    opened = client.get(f"/v/{made['code']}", follow_redirects=False)
    assert opened.status_code == 303
    # The code must not survive into history, a screenshot or a referrer.
    assert opened.headers["location"] == "/v"
    assert made["code"] not in opened.headers["location"]
    # What the cookie holds is the session layer's business. That it exists,
    # and that the code is not in it, is this route's business.
    session = client.cookies.get(leads_service.VISITOR_COOKIE)
    assert session
    assert made["code"] not in session

    panel = client.get("/v")
    assert panel.status_code == 200
    assert "همکار الف" in panel.text
    # And the cookie alone now opens the routes behind it.
    assert client.get("/api/leads/companies").status_code == 200


def test_an_unknown_personal_code_opens_nothing(client):
    from app.services import leads as leads_service
    refused = client.get("/v/" + secrets.token_urlsafe(24), follow_redirects=False)
    assert refused.status_code == 403
    assert client.cookies.get(leads_service.VISITOR_COOKIE) is None
    assert client.get("/api/leads/companies").status_code == 401


def test_a_revoked_visitor_is_locked_out_on_the_very_next_request(client, visitor):
    """The cookie stays valid bytes. What stops it is `active`, re-read every
    time, which is the whole reason a lost phone can be shut off at once."""
    assert client.get("/api/leads/companies").status_code == 200

    _admin(client)
    revoked = client.post(f"/admin/api/leads/visitors/{visitor['id']}/active",
                          json={"active": False})
    assert revoked.status_code == 200, revoked.text

    # Same cookie, same client, next request.
    assert client.get("/api/leads/companies").status_code == 401
    assert client.get("/v").status_code == 403
    assert _register(client, "any").status_code == 401


def test_every_visitor_route_needs_the_cookie(client):
    _add_company("co-a", "شرکت الف")
    assert client.get("/api/leads/companies").status_code == 401
    assert client.get("/api/leads/mine").status_code == 401
    assert _register(client, "co-a").status_code == 401
    assert client.post("/api/leads/verify",
                       json={"lead_id": "x", "code": "123456"}).status_code == 401


# ── The list a visitor may pick from ────────────────────────────────────

def test_search_finds_a_company_by_part_of_its_name(client, visitor):
    _add_company("co-a", "پارس فناوران آریا")
    _add_company("co-b", "صنایع نوین تهران")

    everything = client.get("/api/leads/companies").json()["companies"]
    assert {c["id"] for c in everything} == {"co-a", "co-b"}

    hit = client.get("/api/leads/companies", params={"q": "فناوران"}).json()["companies"]
    assert [c["id"] for c in hit] == ["co-a"]
    assert hit[0]["title"] == "پارس فناوران آریا"

    assert client.get("/api/leads/companies",
                      params={"q": "شرکتی که نیست"}).json()["companies"] == []


def test_search_drops_a_company_that_already_has_a_verified_owner(client, visitor,
                                                                  outbox):
    """Two people cannot work one booth, so a claimed company leaves EVERY
    list, including the list of the visitor who claimed it."""
    _add_company("co-a", "پارس فناوران آریا")
    _add_company("co-b", "صنایع نوین تهران")

    made = _register(client, "co-a")
    assert made.status_code == 200, made.text
    # Still on offer: an unverified registration claims nothing.
    listed = client.get("/api/leads/companies").json()["companies"]
    assert {c["id"] for c in listed} == {"co-a", "co-b"}

    _verify(client, made.json()["lead_id"], outbox)

    listed = client.get("/api/leads/companies").json()["companies"]
    assert [c["id"] for c in listed] == ["co-b"]
    # And the second visitor cannot reach it either.
    _sign_in(client, "همکار ب")
    assert [c["id"] for c in
            client.get("/api/leads/companies").json()["companies"]] == ["co-b"]


# ── Registering a contact ───────────────────────────────────────────────

def test_registering_a_contact_sends_one_code_and_leaves_the_lead_unverified(
        client, visitor, outbox):
    _add_company("co-a", "پارس فناوران آریا")

    made = _register(client, "co-a")
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["company"] == "پارس فناوران آریا"
    assert body["consent_version"] == "v1"
    # The number goes back masked, and the code goes to the phone only.
    assert "*" in body["destination_masked"]
    assert PHONE not in made.text
    assert len(outbox) == 1
    assert outbox[0][0] == PHONE
    assert outbox[0][1] not in made.text

    rows = _leads("co-a")
    assert len(rows) == 1
    assert rows[0]["id"] == body["lead_id"]
    assert rows[0]["status"] == "unverified"
    assert rows[0]["visitor_id"] == visitor["id"]
    assert rows[0]["company_name"] == "پارس فناوران آریا"
    assert rows[0]["first_name"] == "مینا"
    assert rows[0]["duplicate_override_of"] is None


def test_an_unknown_dataset_id_is_refused_and_spends_no_code(client, visitor, outbox):
    """A company id is a row of the knowledge base, never a string a caller
    thought of."""
    refused = _register(client, "co-that-does-not-exist")
    assert refused.status_code == 404, refused.text
    assert _leads() == []
    assert outbox == []


def test_a_blank_name_is_refused_and_spends_no_code(client, visitor, outbox):
    """A lead with no name is a phone number nobody can call back about."""
    _add_company("co-a", "پارس فناوران آریا")
    refused = _register(client, "co-a", first_name="   ")
    assert refused.status_code == 400, refused.text
    assert _leads() == []
    # Refused BEFORE the gateway, so a typo costs no message.
    assert outbox == []


def test_an_unverified_number_does_not_block_the_next_booth(client, visitor, outbox):
    """Only a number that actually answered owns anything. A first attempt
    that was never confirmed must not lock the same person out of booth two."""
    _add_company("co-a", "پارس فناوران آریا")
    _add_company("co-b", "صنایع نوین تهران")

    first = _register(client, "co-a")
    assert first.status_code == 200, first.text
    assert _leads("co-a")[0]["status"] == "unverified"

    second = _register(client, "co-b")
    assert second.status_code == 200, second.text
    assert len(outbox) == 2
    assert len(_leads()) == 2


def test_a_verified_number_warns_on_the_next_booth_without_spending_a_code(
        client, visitor, outbox):
    _add_company("co-a", "پارس فناوران آریا")
    _add_company("co-b", "صنایع نوین تهران")
    first = _register(client, "co-a")
    _verify(client, first.json()["lead_id"], outbox)
    assert len(outbox) == 1

    warned = _register(client, "co-b")
    assert warned.status_code == 409, warned.text
    # `duplicate` is what tells the panel this refusal is a question, not a
    # wall: an owned company is a 409 too and needs a different screen.
    assert warned.json()["duplicate"] is True
    # The other company is never named to whoever is holding this phone.
    assert "پارس فناوران آریا" not in warned.text
    # Nothing was created and no message was paid for.
    assert _leads("co-b") == []
    assert len(outbox) == 1


def test_the_explicit_override_sends_the_code_and_is_written_down(client, visitor,
                                                                  outbox):
    """One person really can run two booths. Waving the warning away in
    silence is what would not be acceptable."""
    _add_company("co-a", "پارس فناوران آریا")
    _add_company("co-b", "صنایع نوین تهران")
    first = _register(client, "co-a")
    first_lead = first.json()["lead_id"]
    _verify(client, first_lead, outbox)

    assert _register(client, "co-b").status_code == 409
    again = _register(client, "co-b", override_duplicate=True)
    assert again.status_code == 200, again.text
    assert len(outbox) == 2

    row = _leads("co-b")[0]
    assert row["status"] == "unverified"
    assert row["duplicate_override_of"] == first_lead
    assert row["duplicate_override_at"]


# ── The visitor's own tally ─────────────────────────────────────────────

def test_mine_lists_only_this_visitors_own_rows_with_the_phone_masked(
        client, visitor, outbox):
    _add_company("co-a", "پارس فناوران آریا")
    _add_company("co-b", "صنایع نوین تهران")
    mine = _register(client, "co-a")
    assert mine.status_code == 200, mine.text

    other = _sign_in(client, "همکار ب")
    theirs = _register(client, "co-b", phone="09121110099")
    assert theirs.status_code == 200, theirs.text

    listed = client.get("/api/leads/mine")
    assert listed.status_code == 200
    rows = listed.json()["leads"]
    assert [r["id"] for r in rows] == [theirs.json()["lead_id"]]
    assert rows[0]["visitor_id"] == other["id"]
    # Masked, and the raw number is nowhere in the payload.
    assert "*" in rows[0]["phone"]
    assert "09121110099" not in listed.text

    # Back to the first visitor: their row, and only theirs.
    client.get(f"/v/{visitor['code']}", follow_redirects=False)
    rows = client.get("/api/leads/mine").json()["leads"]
    assert [r["id"] for r in rows] == [mine.json()["lead_id"]]
