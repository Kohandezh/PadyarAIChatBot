"""The identity layer: an account, a session, and a grant of ownership.

Every access rule of SPEC section 8.2 that phase 3 introduces has its own case
here, because each one is a separate way to get at `dataset.text` and finding
out afterwards which of them was open is finding out too late.

  SEC-001  saving needs a live invite OR a live session. Neither means 403.
  SEC-002  on the session path it also needs a live `dataset_owners` row, and
           a revoked grant behaves exactly like one that never existed.
  SEC-003  both conditions, neither sufficient on its own.
  SEC-004  `dataset_id` never comes from the request.
  SEC-005  `dataset_id` is never snapshotted onto a session.
  SEC-006  blocking DELETEs the sessions rather than raising a flag.
  SEC-007  one account cannot read or write another's company.
  SEC-011  one live owner per company; one account may own many companies.
  SEC-012  a grant expires, and an expired one opens nothing.
  SEC-013  capture at a booth never escalates an account that already exists.
  SEC-016  a session outliving its expiry is refused.
  SEC-034  login is OTP only. There is no password.
  SEC-035  the raw code is not in a response, a log or the database.
  SEC-036  capture on a NEW number creates an active account that owns the row.
  REL-001  no path reads then writes; two registrations of one number race to
           a single account.
"""
import datetime
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_db_connection
from app.services import identity as identity_service
from app.services import leads as leads_service


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "identity.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    leads_service.ensure_tables()
    identity_service.ensure_tables()
    # Read once here so the key exists before any threaded test asks for it:
    # on a fresh install the first read WRITES it into `settings`.
    identity_service.phone_digest("+989120000000")
    return tmp_path


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """The suite fires a whole booth's worth of requests in a second."""
    import app.routers.leads as leads_router
    monkeypatch.setattr(leads_router, "check_rate_limit", lambda *a, **k: None)


@pytest.fixture
def outbox(monkeypatch):
    """Capture the delivered code instead of texting anyone."""
    from app.services import otp as otp_service
    sent = []
    monkeypatch.setattr(otp_service, "_deliver",
                        lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture
def make_client(paths):
    """A browser. Tests that need two people need two of these."""
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


def _write(sql, params=()):
    conn = get_db_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _iso(**delta):
    return (datetime.datetime.utcnow() + datetime.timedelta(**delta)).isoformat()


def _company(dataset_id, title, text="متن قدیمی"):
    _write("INSERT INTO dataset (id, title, text, video_url) VALUES (?, ?, ?, '')",
           (dataset_id, title, text))
    return dataset_id


def _login(client, phone, outbox):
    """The whole login: a number, a code, a session cookie."""
    asked = client.post("/api/auth/login/request", json={"phone": phone})
    assert asked.status_code == 200, asked.text
    code = outbox[-1][1]
    done = client.post("/api/auth/login/verify",
                       json={"challenge_id": asked.json()["challenge_id"], "code": code})
    assert done.status_code == 200, done.text
    return done


def _admin(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.auth.csrf import token_for_session
    token = secrets.token_hex(16)
    _write("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
           (token, "tester", (datetime.datetime.now()
                              + datetime.timedelta(hours=1)).isoformat()))
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    client.headers.update({"X-CSRF-Token": token_for_session(token)})
    return token


def _capture(client, dataset_id, phone, outbox, name="سارا"):
    """A field visitor registers a contact at a booth and verifies the code."""
    made = leads_service.create_visitor("همکار")
    assert client.get(f"/v/{made['code']}", follow_redirects=False).status_code == 303
    registered = client.post("/api/leads/register",
                             json={"dataset_id": dataset_id, "first_name": name,
                                   "phone": phone})
    assert registered.status_code == 200, registered.text
    verified = client.post("/api/leads/verify",
                           json={"lead_id": registered.json()["lead_id"],
                                 "code": outbox[-1][1]})
    assert verified.status_code == 200, verified.text
    return verified.json()


def _own(user_id, dataset_id, **kwargs):
    """Grant ownership without going through a booth."""
    return identity_service.grant_ownership(dataset_id, user_id,
                                            granted_by="tester",
                                            status=kwargs.pop("status", "active"),
                                            **kwargs)


def _user_id(phone):
    return identity_service.user_by_phone(phone)["id"]


# ── Login: an account, and no password (SEC-034, REQ-035 to REQ-039) ─────

def test_a_phone_and_a_code_make_an_account_and_a_session(client, outbox):
    """REQ-037. First visit and tenth visit are the same two steps."""
    _login(client, "09121110000", outbox)
    users = _rows("SELECT id, phone, phone_hash, phone_hash_key_version, source"
                  " FROM users")
    assert len(users) == 1
    assert users[0]["phone_hash"]
    assert users[0]["phone_hash_key_version"] == 1
    assert users[0]["source"] == "login"
    assert len(_rows("SELECT token FROM user_sessions")) == 1
    assert client.get("/api/my/companies").status_code == 200


def test_there_is_no_password_to_forget(client, outbox):
    """SEC-034. Nothing on the account is a secret the person has to keep."""
    _login(client, "09121110001", outbox)
    columns = {r["name"] for r in _rows("PRAGMA table_info(users)")}
    assert not [c for c in columns if "password" in c or "hash" == c or "salt" in c]
    assert "phone_hash" in columns


def test_the_second_login_finds_the_account_it_made(client, make_client, outbox):
    """REQ-039. `phone_hash` is unique; signing in again is not a signup."""
    _login(client, "09121110002", outbox)
    _login(make_client(), "09121110002", outbox)
    assert len(_rows("SELECT id FROM users")) == 1
    assert len(_rows("SELECT token FROM user_sessions")) == 2


def test_the_code_is_never_in_the_answer(client, outbox):
    """SEC-035. Not in the body of either step, and not in the database."""
    asked = client.post("/api/auth/login/request", json={"phone": "09121110003"})
    code = outbox[-1][1]
    assert code not in asked.text
    done = client.post("/api/auth/login/verify",
                       json={"challenge_id": asked.json()["challenge_id"], "code": code})
    assert code not in done.text
    assert not _rows("SELECT id FROM otp_challenges WHERE code_hmac = ?", (code,))


def test_a_wrong_code_opens_nothing(client, outbox):
    asked = client.post("/api/auth/login/request", json={"phone": "09121110004"})
    refused = client.post("/api/auth/login/verify",
                          json={"challenge_id": asked.json()["challenge_id"],
                                "code": "000000"})
    assert refused.status_code == 400
    assert _rows("SELECT id FROM users") == []
    assert client.get("/api/my/companies").status_code == 403


def test_logging_out_kills_the_session(client, outbox):
    """REQ-038. The same cookie stops working, on the server."""
    _login(client, "09121110005", outbox)
    cookie = client.cookies.get(identity_service.USER_COOKIE)
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert _rows("SELECT token FROM user_sessions") == []
    client.cookies.set(identity_service.USER_COOKIE, cookie)
    assert client.get("/api/my/companies").status_code == 403


def test_my_page_without_a_session_is_a_login(client):
    page = client.get("/my", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"


# ── The session (SEC-016) ────────────────────────────────────────────────

def test_a_session_that_outlived_its_expiry_is_refused(client, outbox):
    """SEC-016. Two hours are enforced by the server, not by the browser.

    Having verified a code at some point in the past is not access.
    """
    _login(client, "09121110006", outbox)
    _own(_user_id("09121110006"), _company("d-exp", "شرکت الف"))
    assert client.get("/api/my/companies").json()["companies"]

    token = _rows("SELECT token FROM user_sessions")[0]["token"]
    _write("UPDATE user_sessions SET expiry = ? WHERE token = ?", (_iso(hours=-1), token))

    assert client.get("/api/my/companies").status_code == 403
    assert client.post("/api/my/edit", json={"text": "متن تازه"}).status_code == 403
    # And the dead row is gone rather than left to be joined on forever.
    assert _rows("SELECT token FROM user_sessions WHERE token = ?", (token,)) == []


def test_an_invented_cookie_opens_nothing(client):
    client.cookies.set(identity_service.USER_COOKIE, "a" * 40)
    assert client.get("/api/my/companies").status_code == 403
    assert client.post("/api/my/edit", json={"text": "x"}).status_code == 403


# ── Saving an edit: both conditions, neither enough (SEC-001 to SEC-003) ─

def test_no_session_cannot_save(client):
    """SEC-001. No credential at all is a 403 and writes nothing."""
    _company("d-nosess", "شرکت ب", "متن اصلی")
    refused = client.post("/api/my/edit", json={"text": "متن مهاجم"})
    assert refused.status_code == 403
    assert _rows("SELECT id FROM dataset_edits") == []
    assert _rows("SELECT text FROM dataset WHERE id = 'd-nosess'")[0]["text"] == "متن اصلی"


def test_a_session_without_ownership_cannot_save(client, outbox):
    """SEC-002 and SEC-003. A valid session on its own opens nothing."""
    _company("d-noown", "شرکت ج", "متن اصلی")
    _login(client, "09121110007", outbox)
    assert client.get("/api/my/companies").json()["companies"] == []
    refused = client.post("/api/my/edit", json={"text": "متن مهاجم"})
    assert refused.status_code == 403
    assert _rows("SELECT id FROM dataset_edits") == []


def test_a_revoked_grant_is_exactly_an_absent_one(client, outbox):
    """SEC-002. Same status, same sentence, nothing to read off the difference."""
    dataset = _company("d-revoked", "شرکت د")
    _login(client, "09121110008", outbox)
    grant = _own(_user_id("09121110008"), dataset)
    handle = grant["id"]
    assert client.get(f"/api/my/edit/{handle}").status_code == 200

    assert identity_service.revoke_grant(handle, actor="tester") is True
    gone = client.get(f"/api/my/edit/{handle}")
    saving = client.post(f"/api/my/edit/{handle}", json={"text": "متن تازه"})
    never_existed = client.get("/api/my/edit/there-was-never-such-a-grant")
    assert gone.status_code == saving.status_code == never_existed.status_code == 403
    assert gone.json() == never_existed.json()
    assert client.get("/api/my/companies").json()["companies"] == []
    assert _rows("SELECT id FROM dataset_edits") == []


def test_a_revoked_grant_keeps_its_row(client, outbox):
    """REQ-047. Revoked, not deleted: who could edit this, and until when."""
    dataset = _company("d-history", "شرکت ه")
    _login(client, "09121110009", outbox)
    grant = _own(_user_id("09121110009"), dataset)
    identity_service.revoke_grant(grant["id"], actor="tester")
    row = _rows("SELECT status, revoked_at FROM dataset_owners WHERE id = ?",
                (grant["id"],))
    assert len(row) == 1 and row[0]["revoked_at"]


def test_an_expired_grant_opens_nothing(client, outbox):
    """SEC-012. Ownership ends with the exhibition and does not renew itself."""
    dataset = _company("d-stale", "شرکت و")
    _login(client, "09121110010", outbox)
    grant = _own(_user_id("09121110010"), dataset)
    _write("UPDATE dataset_owners SET expires_at = ? WHERE id = ?",
           (_iso(hours=-1), grant["id"]))
    assert client.get(f"/api/my/edit/{grant['id']}").status_code == 403
    assert client.post("/api/my/edit", json={"text": "متن تازه"}).status_code == 403


def test_a_grant_gets_an_expiry_by_default(client, outbox):
    """SEC-012. Nothing is granted forever, including by an admin."""
    _login(client, "09121110011", outbox)
    grant = _own(_user_id("09121110011"), _company("d-ttl", "شرکت ز"))
    expires = _rows("SELECT expires_at FROM dataset_owners WHERE id = ?",
                    (grant["id"],))[0]["expires_at"]
    assert expires and datetime.datetime.fromisoformat(expires) > datetime.datetime.utcnow()


# ── One company, one owner (SEC-007, SEC-011) ────────────────────────────

def test_one_user_cannot_reach_another_users_company(client, make_client, outbox):
    """SEC-007. Not by reading it, and not by writing it."""
    theirs = _company("d-theirs", "شرکت دیگری", "متن دیگری")
    other = make_client()
    _login(other, "09121110012", outbox)
    their_grant = _own(_user_id("09121110012"), theirs)

    _login(client, "09121110013", outbox)
    _own(_user_id("09121110013"), _company("d-mine", "شرکت خودم"))

    assert client.get(f"/api/my/edit/{their_grant['id']}").status_code == 403
    assert client.post(f"/api/my/edit/{their_grant['id']}",
                       json={"text": "متن مهاجم"}).status_code == 403
    assert _rows("SELECT text FROM dataset WHERE id = 'd-theirs'")[0]["text"] == "متن دیگری"
    assert _rows("SELECT id FROM dataset_edits") == []
    # And their own list never mentioned the other company.
    titles = [c["title"] for c in client.get("/api/my/companies").json()["companies"]]
    assert titles == ["شرکت خودم"]


def test_a_company_has_one_live_owner(client, outbox):
    """SEC-011. A second grant on the same row needs an explicit transfer."""
    dataset = _company("d-single", "شرکت تک‌مالک")
    _login(client, "09121110014", outbox)
    first = _user_id("09121110014")
    second = identity_service.find_or_create_user("09121110015")["id"]
    assert _own(first, dataset)["ok"] is True

    refused = _own(second, dataset)
    assert refused["ok"] is False and refused["reason"] == "taken"
    assert identity_service.live_grants(second) == []

    # Revoking the first is the explicit act that frees the company.
    identity_service.revoke_grant(_rows(
        "SELECT id FROM dataset_owners WHERE user_id = ?", (first,))[0]["id"])
    assert _own(second, dataset)["ok"] is True


def test_one_user_may_own_many_companies(client, outbox):
    """SEC-011, the other half. Two constraints, and both hold."""
    _login(client, "09121110016", outbox)
    user = _user_id("09121110016")
    _own(user, _company("d-many-1", "شرکت یک"))
    _own(user, _company("d-many-2", "شرکت دو"))
    listed = client.get("/api/my/companies").json()["companies"]
    assert sorted(c["title"] for c in listed) == ["شرکت دو", "شرکت یک"]
    # With more than one, the bare route cannot guess which.
    assert client.post("/api/my/edit", json={"text": "متن تازه"}).status_code == 403
    handle = [c["id"] for c in listed if c["title"] == "شرکت یک"][0]
    assert client.post(f"/api/my/edit/{handle}", json={"text": "متن تازه"}).status_code == 200


# ── The company id is derived, never given (SEC-004, SEC-005) ────────────

def test_a_dataset_id_in_the_body_is_refused(client, outbox):
    """SEC-004 and REQ-069. Refused outright, not ignored.

    Ignoring an unexpected field is how somebody later assumes the endpoint was
    already reading it.
    """
    _login(client, "09121110017", outbox)
    _own(_user_id("09121110017"), _company("d-own", "شرکت خودم"))
    _company("d-target", "شرکت هدف", "متن هدف")

    refused = client.post("/api/my/edit",
                          json={"text": "متن مهاجم", "dataset_id": "d-target"})
    assert refused.status_code == 400
    assert _rows("SELECT text FROM dataset WHERE id = 'd-target'")[0]["text"] == "متن هدف"
    assert _rows("SELECT id FROM dataset_edits") == []


def test_the_session_never_carries_a_company(client, outbox):
    """SEC-005. The row that was deleted for doing this is `edit_sessions`."""
    _login(client, "09121110018", outbox)
    columns = {r["name"] for r in _rows("PRAGMA table_info(user_sessions)")}
    assert columns == {"token", "user_id", "expiry", "created_at"}
    assert "dataset_id" not in columns


def test_the_edit_is_read_from_the_ownership_every_time(client, outbox):
    """SEC-004. Revoking between two identical requests changes the answer."""
    dataset = _company("d-reread", "شرکت بازخوانی")
    _login(client, "09121110019", outbox)
    grant = _own(_user_id("09121110019"), dataset)
    assert client.get(f"/api/my/edit/{grant['id']}").json()["company"] == "شرکت بازخوانی"
    identity_service.revoke_grant(grant["id"])
    assert client.get(f"/api/my/edit/{grant['id']}").status_code == 403


# ── Blocking (SEC-006) ───────────────────────────────────────────────────

def test_blocking_deletes_the_sessions(client, outbox):
    """SEC-006. A flag only works where somebody remembered to read it."""
    _login(client, "09121110020", outbox)
    user = _user_id("09121110020")
    _own(user, _company("d-blocked", "شرکت مسدود"))
    assert client.get("/api/my/companies").status_code == 200

    identity_service.block_user(user)
    assert _rows("SELECT token FROM user_sessions WHERE user_id = ?", (user,)) == []
    assert client.get("/api/my/companies").status_code == 403
    assert client.post("/api/my/edit", json={"text": "متن تازه"}).status_code == 403


def test_a_blocked_account_cannot_sign_back_in(client, outbox):
    """SEC-006. Otherwise blocking lasts exactly one code."""
    _login(client, "09121110021", outbox)
    identity_service.block_user(_user_id("09121110021"))
    asked = client.post("/api/auth/login/request", json={"phone": "09121110021"})
    refused = client.post("/api/auth/login/verify",
                          json={"challenge_id": asked.json()["challenge_id"],
                                "code": outbox[-1][1]})
    assert refused.status_code == 403
    assert _rows("SELECT token FROM user_sessions") == []


def test_blocking_by_the_admin_route_takes_effect_at_once(client, make_client, outbox):
    _login(client, "09121110022", outbox)
    admin = make_client()
    _admin(admin)
    done = admin.post(f"/admin/api/leads/users/{_user_id('09121110022')}/block",
                      json={"blocked": True})
    assert done.status_code == 200, done.text
    assert client.get("/api/my/companies").status_code == 403


# ── The booth (SEC-013, SEC-036) ─────────────────────────────────────────

def test_a_new_number_gets_an_active_account_and_the_company(client, make_client,
                                                             outbox):
    """SEC-036. Creating an account raises nobody's access: it did not exist."""
    dataset = _company("d-fresh", "شرکت تازه")
    result = _capture(client, dataset, "09121112000", outbox)
    assert result["owner_pending"] is False

    grants = _rows("SELECT status, revoked_at FROM dataset_owners WHERE dataset_id = ?",
                   (dataset,))
    assert len(grants) == 1 and grants[0]["status"] == "active"

    contact = make_client()
    _login(contact, "09121112000", outbox)
    listed = contact.get("/api/my/companies").json()["companies"]
    assert [c["title"] for c in listed] == ["شرکت تازه"]


def test_a_booth_never_escalates_an_account_that_already_exists(client, make_client,
                                                                outbox):
    """SEC-013 and F25. Run backwards this is the attack: sign up with a
    number, have a colleague capture it at the target's booth, own the target.
    """
    contact = make_client()
    _login(contact, "09121112001", outbox)          # the account exists first
    dataset = _company("d-target-booth", "شرکت هدف", "متن هدف")

    result = _capture(client, dataset, "09121112001", outbox)
    assert result["owner_pending"] is True
    assert _rows("SELECT status FROM dataset_owners WHERE dataset_id = ?",
                 (dataset,))[0]["status"] == "pending"

    body = contact.get("/api/my/companies").json()
    assert body["companies"] == []
    assert [g["title"] for g in body["pending_grants"]] == ["شرکت هدف"]

    handle = body["pending_grants"][0]["id"]
    assert contact.get(f"/api/my/edit/{handle}").status_code == 403
    assert contact.post(f"/api/my/edit/{handle}",
                        json={"text": "متن مهاجم"}).status_code == 403
    assert _rows("SELECT text FROM dataset WHERE id = ?", (dataset,))[0]["text"] == "متن هدف"


def test_the_holder_accepts_the_pending_grant_from_their_own_session(client,
                                                                    make_client, outbox):
    """SEC-013, the other half. Accepting is possible, and only from here."""
    contact = make_client()
    _login(contact, "09121112002", outbox)
    dataset = _company("d-accept", "شرکت پذیرفته")
    _capture(client, dataset, "09121112002", outbox)
    handle = contact.get("/api/my/companies").json()["pending_grants"][0]["id"]

    # Not from anybody else's session.
    stranger = make_client()
    _login(stranger, "09121112003", outbox)
    assert stranger.post(f"/api/my/companies/{handle}/accept").status_code == 403

    assert contact.post(f"/api/my/companies/{handle}/accept").status_code == 200
    body = contact.get("/api/my/companies").json()
    assert [c["title"] for c in body["companies"]] == ["شرکت پذیرفته"]
    assert body["pending_grants"] == []
    assert contact.post(f"/api/my/edit/{handle}",
                        json={"text": "متن تازه"}).status_code == 200


def test_releasing_a_stuck_registration_takes_the_company_back(client, make_client,
                                                               outbox):
    """REQ-065 with SEC-002. The company returns to the search list, so the
    account that got it at the booth stops owning it at the same moment."""
    dataset = _company("d-released", "شرکت آزاد")
    _capture(client, dataset, "09121112004", outbox)
    contact = make_client()
    _login(contact, "09121112004", outbox)
    handle = contact.get("/api/my/companies").json()["companies"][0]["id"]

    admin = make_client()
    _admin(admin)
    lead_id = _rows("SELECT id FROM company_leads WHERE dataset_id = ?", (dataset,))[0]["id"]
    assert admin.post(f"/admin/api/leads/{lead_id}/release").status_code == 200

    assert contact.get("/api/my/companies").json()["companies"] == []
    assert contact.post(f"/api/my/edit/{handle}",
                        json={"text": "متن تازه"}).status_code == 403


# ── The edit itself (REQ-043) ────────────────────────────────────────────

def test_an_owners_edit_is_queued_and_never_published(client, outbox):
    """REQ-043 with SEC-028. `dataset.text` has one writer, and it is review."""
    dataset = _company("d-queue", "شرکت صف", "متن زنده")
    _login(client, "09121112005", outbox)
    _own(_user_id("09121112005"), dataset)

    saved = client.post("/api/my/edit", json={"text": "متن تازهٔ شرکت"})
    assert saved.status_code == 200, saved.text
    assert _rows("SELECT text FROM dataset WHERE id = ?", (dataset,))[0]["text"] == "متن زنده"
    queued = _rows("SELECT status, old_text, new_text FROM dataset_edits")
    assert len(queued) == 1
    assert queued[0]["status"] == "pending"
    assert queued[0]["old_text"] == "متن زنده"

    # The review queue can still say which company this is, with no lead behind it.
    assert leads_service.list_edits("pending")[0]["company_name"] == "شرکت صف"
    # And coming back shows the unreviewed text, not the live answer.
    state = client.get("/api/my/edit").json()
    assert state["text"] == "متن تازهٔ شرکت" and state["pending"] is True
    assert state["submission"]["status"] == "pending"


def test_sending_again_replaces_the_draft(client, outbox):
    """One pending draft per company, so a reviewer never reconciles two."""
    dataset = _company("d-again", "شرکت دوباره")
    _login(client, "09121112006", outbox)
    _own(_user_id("09121112006"), dataset)
    client.post("/api/my/edit", json={"text": "اول"})
    client.post("/api/my/edit", json={"text": "دوم"})
    statuses = sorted(r["status"] for r in _rows("SELECT status FROM dataset_edits"))
    assert statuses == ["pending", "superseded"]


def test_markup_is_refused_on_this_path_too(client, outbox):
    """SEC-024. The rule belongs to the column, not to one of its doors."""
    dataset = _company("d-markup", "شرکت متن")
    _login(client, "09121112007", outbox)
    _own(_user_id("09121112007"), dataset)
    refused = client.post("/api/my/edit", json={"text": "<script>alert(1)</script>"})
    assert refused.status_code == 400
    assert _rows("SELECT id FROM dataset_edits") == []


# ── Races (REL-001) ──────────────────────────────────────────────────────

def test_two_registrations_of_one_number_make_one_account(paths):
    """REL-001. Never SELECT then INSERT.

    Two visitors registering the same contact in the same second must not both
    win, and must not fail either: by then the SMS is sent and billed.
    """
    phone = "09121113000"

    def register():
        return identity_service.find_or_create_user(phone, source="booth")

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = [f.result() for f in [pool.submit(register) for _ in range(6)]]

    assert len(_rows("SELECT id FROM users")) == 1
    assert len({r["id"] for r in results}) == 1
    assert sum(1 for r in results if r["created"]) == 1


def test_two_grants_of_one_company_leave_one_owner(paths):
    """REL-001 with SEC-011. The condition is inside the INSERT."""
    dataset = _company("d-race", "شرکت رقابت")
    users = [identity_service.find_or_create_user(f"0912111400{i}")["id"]
             for i in range(4)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result() for f in
                   [pool.submit(_own, u, dataset) for u in users]]

    assert sum(1 for r in results if r["ok"]) == 1
    live = _rows("SELECT id FROM dataset_owners WHERE dataset_id = ?"
                 " AND revoked_at IS NULL AND status = 'active'", (dataset,))
    assert len(live) == 1


# ── Admin (REQ-045 to REQ-047) ───────────────────────────────────────────

def test_the_admin_sees_accounts_with_their_live_ownership_count(client, make_client,
                                                                 outbox):
    """REQ-045. A revoked grant in that column would read as access."""
    _login(client, "09121115000", outbox)
    user = _user_id("09121115000")
    first = _own(user, _company("d-count-1", "شرکت شمارش یک"))
    _own(user, _company("d-count-2", "شرکت شمارش دو"))
    identity_service.revoke_grant(first["id"])

    admin = make_client()
    _admin(admin)
    body = admin.get("/admin/api/leads/users").json()
    row = [u for u in body["users"] if u["id"] == user][0]
    assert row["owns"] == 1
    # The roster is not the screen for a full phone number.
    assert "*" in row["phone"]


def test_the_admin_grants_and_revokes_ownership(client, make_client, outbox):
    """REQ-046 and REQ-047, and SEC-012's "an admin confirms the first owner"."""
    dataset = _company("d-admin-grant", "شرکت اعطا")
    _login(client, "09121115001", outbox)
    user = _user_id("09121115001")

    admin = make_client()
    _admin(admin)
    granted = admin.post("/admin/api/leads/owners",
                         json={"dataset_id": dataset, "user_id": user})
    assert granted.status_code == 200, granted.text
    assert [c["title"] for c in client.get("/api/my/companies").json()["companies"]] \
        == ["شرکت اعطا"]

    # A second account on the same company needs the first revoked.
    other = identity_service.find_or_create_user("09121115002")["id"]
    assert admin.post("/admin/api/leads/owners",
                      json={"dataset_id": dataset, "user_id": other}).status_code == 409

    revoked = admin.post(f"/admin/api/leads/owners/{granted.json()['id']}/revoke")
    assert revoked.status_code == 200
    assert client.get("/api/my/companies").json()["companies"] == []
    assert admin.post("/admin/api/leads/owners",
                      json={"dataset_id": dataset, "user_id": other}).status_code == 200


def test_the_admin_routes_need_an_admin(client, outbox):
    _login(client, "09121115003", outbox)
    assert client.get("/admin/api/leads/users").status_code == 401
    assert client.post("/admin/api/leads/owners",
                       json={"dataset_id": "d", "user_id": "u"}).status_code == 401
