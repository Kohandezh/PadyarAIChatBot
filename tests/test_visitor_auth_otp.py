"""The registration router after identity stopped travelling in the request.

WHAT THIS FILE DEFENDS
----------------------
`POST /api/auth/profile` used to take a `challenge_id` out of the request body
and treat it as proof of who was asking. The id never expired, lived in
localStorage where any XSS could lift it, and whoever held it could rewrite
that person's job, position and interests and read their name and masked phone
back. `POST /api/visit-plan` accepted the same field for the same purpose.

Identity now comes from one place: the HttpOnly session cookie minted by
`POST /api/auth/otp/verify`. So the tests here are mostly about what must NOT
work — a body field, a header, a borrowed id, a request from another site.

Runs against the ambient database like the other OTP suites, with its own
phone-number prefix (`+9891200002…`) so its rows never collide with theirs.
"""
import pytest
from fastapi.testclient import TestClient

from app.auth import visitor as visitor_auth
from app.db.connection import get_db_connection
from app.main import app
from app.services import otp as otp_service

DEST_A = "+989120000201"
DEST_B = "+989120000202"

# Every request a browser makes carries these. TestClient sends neither, and
# validate_request_origin refuses a request with no Origin/Referer and a short
# User-Agent — which is the point of the cross-origin test at the bottom.
BROWSER = {"Origin": "http://localhost", "User-Agent": "pytest-agent/1.0"}


@pytest.fixture()
def outbox(monkeypatch):
    """Capture delivered codes in memory instead of the dev outbox file."""
    sent = []
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture()
def client():
    """One browser: its own cookie jar, so a session belongs to it alone."""
    with TestClient(app) as c:
        c.headers.update(BROWSER)
        yield c


@pytest.fixture()
def other_client():
    """A SECOND browser. Separate jar, so it never inherits a session."""
    with TestClient(app) as c:
        c.headers.update(BROWSER)
        yield c


@pytest.fixture(autouse=True)
def _no_ip_throttle(monkeypatch):
    """This file fires dozens of requests from one IP in seconds; the product's
    per-IP limiter would throttle the tests themselves. The bucket KEY is
    asserted directly instead — see the rate-limit test."""
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    conn = get_db_connection()
    try:
        # Sessions first: they hang off the visitor rows about to go.
        conn.execute(
            "DELETE FROM visitor_sessions WHERE visitor_id IN"
            " (SELECT id FROM visitors WHERE phone LIKE '+9891200002%')")
        conn.execute("DELETE FROM visitors WHERE phone LIKE '+9891200002%'")
        conn.execute("DELETE FROM otp_challenges WHERE destination LIKE '+9891200002%'")
        conn.commit()
    except Exception:  # noqa: BLE001 — a cleanup failure must not fail a test
        pass
    conn.close()


def _register(client, outbox, dest, **profile):
    """Sign somebody up for real: request a code, verify it, keep the cookie."""
    body = {"destination": dest, "first_name": "علی", "last_name": "احمدی"}
    body.update(profile)
    r = client.post("/api/auth/otp/request", json=body)
    assert r.status_code == 200, r.text
    v = client.post("/api/auth/otp/verify", json={
        "challenge_id": r.json()["challenge_id"], "code": outbox[-1][1]})
    assert v.status_code == 200, v.text
    return v


def _visitor_id(dest):
    """The durable visitor row for a phone, straight from the database."""
    from app.services import conversations
    row = conversations.find_visitor_by_phone(dest)
    assert row, f"no visitor row was written for {dest}"
    return row["id"]


def _stored(dest):
    from app.services import conversations
    return conversations.find_visitor_by_phone(dest)


# ── The mint point ───────────────────────────────────────────────────────

def test_verify_sets_an_httponly_session_cookie(client, outbox):
    """The credential is issued by the server and unreadable to script.

    HttpOnly is the whole reason this replaced a challenge id kept in
    localStorage: that copy was readable by any injected script on the page.
    """
    v = _register(client, outbox, DEST_A)

    raw = [h for k, h in v.headers.multi_items() if k.lower() == "set-cookie"]
    session_cookie = [h for h in raw if h.startswith(visitor_auth.VISITOR_COOKIE_NAME + "=")]
    assert session_cookie, f"verify issued no session cookie: {raw}"
    assert "httponly" in session_cookie[0].lower()
    assert "samesite=lax" in session_cookie[0].lower()

    token = client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)
    assert token, "the browser did not end up holding a session"
    assert visitor_auth.resolve(token)["visitor_id"] == _visitor_id(DEST_A)


def test_the_cookie_value_is_not_the_challenge_id(client, outbox):
    """A session token is minted, never derived from something the client sent.

    If the two were ever equal, the credential would be back in the browser's
    hands — the client already knows the challenge id.
    """
    r = client.post("/api/auth/otp/request", json={"destination": DEST_A})
    challenge = r.json()["challenge_id"]
    client.post("/api/auth/otp/verify",
                json={"challenge_id": challenge, "code": outbox[-1][1]})

    token = client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)
    assert token and token != challenge


def test_a_failed_verification_signs_nobody_in(client, outbox):
    """A wrong code must not leave a session behind."""
    r = client.post("/api/auth/otp/request", json={"destination": DEST_A})
    bad = client.post("/api/auth/otp/verify", json={
        "challenge_id": r.json()["challenge_id"], "code": "000000"})
    assert bad.status_code == 400
    assert not client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)


def test_a_storage_fault_still_reports_a_successful_verification(
        client, outbox, monkeypatch):
    """Being wrongly signed OUT is recoverable. Being told your code was wrong
    when it was right is not.

    So a promotion that cannot write returns "" rather than raising, and
    mint("") returns "": the visitor is told they verified, is simply not
    signed in, and the next verify fixes it. Nothing here may become a 4xx or
    a 500.
    """
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router.conversations, "register_visitor",
                        lambda conversation_id, profile: "")

    r = client.post("/api/auth/otp/request", json={"destination": DEST_A})
    v = client.post("/api/auth/otp/verify", json={
        "challenge_id": r.json()["challenge_id"], "code": outbox[-1][1]})

    assert v.status_code == 200, v.text
    assert v.json()["verified"] is True
    assert not client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)
    assert client.get("/api/auth/session").json()["signed_in"] is False


# ── The kiosk handover: one screen, one visitor after another ────────────

def test_verifying_takes_the_browser_off_the_previous_visitor(client, outbox):
    """Person B verifies on the screen person A just used. B gets it, and A's
    session is DEAD, not merely covered up.

    The old code minted B a session and overwrote the cookie, but never
    touched A's row. On a kiosk that leaves one live session per person who
    ever walked up: rows nobody can reach, each still resolving to a real
    identity, and every one of them a working credential for anybody who
    captured the token. Sign-out is a DELETE everywhere else in this module
    (see visitor_logout), and a handover is a sign-out.

    Both people go through the real endpoints on ONE cookie jar, because a
    kiosk is exactly one browser.
    """
    _register(client, outbox, DEST_A, job="مهندس")
    first_token = client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)
    assert first_token, "the first visitor never got a session"

    _register(client, outbox, DEST_B, job="خبرنگار")
    second_token = client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)

    assert second_token and second_token != first_token
    assert visitor_auth.resolve(second_token)["visitor_id"] == _visitor_id(DEST_B)
    assert visitor_auth.resolve(first_token) is None, (
        "the previous visitor's session row is still alive and still resolves")
    assert client.get("/api/auth/session").json()["profile"]["job"] == "خبرنگار"


def test_a_failed_mint_leaves_the_kiosk_anonymous_not_the_last_visitor(
        client, outbox, monkeypatch):
    """The bug this pair of tests exists for. A storage fault must sign the
    browser OUT, and it used to sign it in as somebody else.

    mint() returns "" when the session cannot be written. The endpoint then
    wrote no cookie at all, and `resolve_visitor` in app/main.py re-issues the
    cookie the REQUEST came in with whenever the response did not write one.
    So person B, who just proved their own phone, was handed person A's
    identity back with a refreshed expiry. Everything B said next was filed
    under A's name.

    mint is patched rather than the storage under it, because the only thing
    that matters here is the empty-token path, whatever produced it.
    """
    _register(client, outbox, DEST_A, job="مهندس")
    stale = client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)
    assert stale

    monkeypatch.setattr(visitor_auth, "mint", lambda visitor_id: "")

    r = client.post("/api/auth/otp/request", json={
        "destination": DEST_B, "first_name": "زهرا", "last_name": "کریمی",
        "job": "خبرنگار"})
    v = client.post("/api/auth/otp/verify", json={
        "challenge_id": r.json()["challenge_id"], "code": outbox[-1][1]})

    # The promise that must survive the fix: a storage fault is never an error
    # in front of somebody who just proved their phone.
    assert v.status_code == 200, v.text
    assert v.json()["verified"] is True

    assert not client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME), (
        "the browser kept a session cookie after a failed mint")
    body = client.get("/api/auth/session").json()
    assert body["signed_in"] is False, "anonymous was expected, got a session"
    assert body["profile"] == {}
    assert visitor_auth.resolve(stale) is None, (
        "the previous visitor's session survived the handover")


# ── /api/auth/profile: no cookie, no write ───────────────────────────────

def test_profile_without_a_cookie_is_401(client):
    r = client.post("/api/auth/profile",
                    json={"job": "مدیرعامل", "position": "", "interests": ""})
    assert r.status_code == 401


def test_the_401_carries_the_machine_readable_marker(client):
    """The frontend opens the signup card on this code.

    It must not have to match Persian prose: a copy edit would silently break
    the wall. `message` is only what a human sees if nothing catches it.
    """
    detail = client.post("/api/auth/profile", json={"job": "x"}).json()["detail"]
    assert detail["code"] == visitor_auth.REGISTRATION_REQUIRED
    assert detail["message"].strip()


def test_a_visitor_id_in_the_body_or_a_header_is_not_identity(
        client, other_client, outbox):
    """The exact attack this whole change exists to stop.

    Somebody else's visitor id, passed the two ways a caller can pass one —
    a body field and a header — with no session cookie of our own. It must be
    401, and the victim's row must be untouched afterwards.
    """
    _register(other_client, outbox, DEST_B, job="خبرنگار", interests="رسانه")
    victim = _visitor_id(DEST_B)
    before = _stored(DEST_B)

    for attempt in (
        {"json": {"job": "مهاجم", "visitor_id": victim, "id": victim},
         "headers": {}},
        {"json": {"job": "مهاجم"},
         "headers": {"X-Visitor-Id": victim, "X-Visitor": victim,
                     "Authorization": f"Bearer {victim}"}},
    ):
        r = client.post("/api/auth/profile", **attempt)
        assert r.status_code == 401, f"{attempt} was accepted: {r.text}"

    after = _stored(DEST_B)
    assert after["job"] == before["job"] == "خبرنگار"
    assert after["interests"] == before["interests"]


def test_a_challenge_id_in_the_body_is_not_identity(client, other_client, outbox):
    """The old credential, replayed. It buys nothing now.

    A verified challenge id used to be the whole proof. Sending it from a
    browser with no session must be refused like any other body field.
    """
    r = other_client.post("/api/auth/otp/request", json={
        "destination": DEST_B, "first_name": "زهرا", "last_name": "کریمی"})
    challenge = r.json()["challenge_id"]
    other_client.post("/api/auth/otp/verify",
                      json={"challenge_id": challenge, "code": outbox[-1][1]})
    before = _stored(DEST_B)

    attack = client.post("/api/auth/profile", json={
        "challenge_id": challenge, "job": "مهاجم", "position": "", "interests": ""})
    assert attack.status_code == 401
    assert _stored(DEST_B)["job"] == before["job"]


def test_one_visitor_cannot_write_another_visitors_profile(
        client, other_client, outbox):
    """A REAL session, aimed at somebody else's row. It writes its own.

    Being signed in is not permission to edit anyone: the UPDATE is keyed on
    the id the cookie resolved to, and no field of the request reaches it.
    """
    _register(client, outbox, DEST_A, job="مهندس")
    _register(other_client, outbox, DEST_B, job="خبرنگار")
    victim = _visitor_id(DEST_B)

    r = client.post("/api/auth/profile",
                    json={"job": "سرمایه‌گذار", "position": "مدیر", "interests": "همه چیز",
                          "visitor_id": victim},
                    headers={"X-Visitor-Id": victim})
    assert r.status_code == 200, r.text

    assert _stored(DEST_A)["job"] == "سرمایه‌گذار", "the caller's own row was not written"
    assert _stored(DEST_B)["job"] == "خبرنگار", "another visitor's row was rewritten"


def test_a_signed_in_visitor_cannot_clear_their_own_profile(client, outbox):
    """The 3 onboarding questions are mandatory now: an empty submission from
    a real session must be refused, and must not overwrite what was stored."""
    _register(client, outbox, DEST_A, job="خبرنگار", interests="رسانه")

    r = client.post("/api/auth/profile",
                    json={"job": "", "position": "", "interests": ""})
    assert r.status_code == 422
    assert _stored(DEST_A)["interests"] == "رسانه"


def test_the_profile_reply_never_carries_the_raw_number(client, outbox):
    _register(client, outbox, DEST_A)
    r = client.post("/api/auth/profile",
                    json={"job": "مهندس", "position": "مدیر", "interests": "همه چیز"})
    assert DEST_A not in r.text and DEST_A.lstrip("+") not in r.text
    assert r.json()["profile"]["destination_masked"].endswith(DEST_A[-4:])


# ── GET /api/auth/session ────────────────────────────────────────────────

def test_session_is_anonymous_without_a_cookie(client):
    body = client.get("/api/auth/session").json()
    assert body["signed_in"] is False
    assert body["profile"] == {}


def test_session_masks_the_phone_number(client, outbox):
    """The durable row keeps the real number so the exhibition can call back.
    Nothing a browser can read is allowed to show it."""
    _register(client, outbox, DEST_A, job="مهندس")

    r = client.get("/api/auth/session")
    assert r.status_code == 200
    body = r.json()
    assert body["signed_in"] is True
    assert body["profile"]["first_name"] == "علی"
    assert body["profile"]["job"] == "مهندس"

    masked = body["profile"]["destination_masked"]
    assert masked and "*" in masked
    assert masked.endswith(DEST_A[-4:])
    assert DEST_A not in r.text and DEST_A.lstrip("+") not in r.text


def test_session_is_never_cached(client, outbox):
    """A shared kiosk: the back button must not redraw the last person."""
    _register(client, outbox, DEST_A)
    r = client.get("/api/auth/session")
    assert "no-store" in r.headers.get("cache-control", "")


def test_session_ignores_a_visitor_id_pushed_in_by_query_or_header(
        client, other_client, outbox):
    """The read side must be as cookie-only as the write side."""
    _register(other_client, outbox, DEST_B, job="خبرنگار")
    victim = _visitor_id(DEST_B)

    r = client.get(f"/api/auth/session?visitor_id={victim}",
                   headers={"X-Visitor-Id": victim})
    assert r.json()["signed_in"] is False
    assert "خبرنگار" not in r.text


# ── POST /api/auth/logout ────────────────────────────────────────────────

def test_logout_ends_the_session_for_good(client, outbox):
    _register(client, outbox, DEST_A)
    token = client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)

    out = client.post("/api/auth/logout")
    assert out.status_code == 200

    assert client.get("/api/auth/session").json()["signed_in"] is False
    # The ROW is gone, not just the browser's copy: a token someone captured
    # off the wire must stop working the same second.
    assert visitor_auth.resolve(token) is None
    assert client.post("/api/auth/profile", json={"job": "x"}).status_code == 401


def test_logout_when_already_anonymous_is_fine(client):
    """A sign-out must never fail, or a shared kiosk keeps the last visitor."""
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/session").json()["signed_in"] is False


def test_logout_does_not_end_anyone_elses_session(client, other_client, outbox):
    _register(client, outbox, DEST_A)
    _register(other_client, outbox, DEST_B)

    client.post("/api/auth/logout")
    assert other_client.get("/api/auth/session").json()["signed_in"] is True


# ── POST /api/visit-plan ─────────────────────────────────────────────────

def test_the_planner_still_works_for_someone_who_never_registered(client):
    """Job and interests are INPUTS to a recommendation, not claims about who
    is asking, so an anonymous caller must still get a plan."""
    r = client.post("/api/visit-plan", json={"interests": "هوش مصنوعی"})
    assert r.status_code == 200, r.text
    assert "ai-iot-conference" in [s["id"] for s in r.json()["sections"]]


def test_a_stored_profile_wins_over_the_body(client, outbox):
    """What the visitor typed at registration decides the plan, not this POST.

    The browser cannot edit the stored copy, which is exactly why it wins.
    """
    _register(client, outbox, DEST_A, job="خبرنگار", interests="رسانه")

    r = client.post("/api/visit-plan", json={"interests": "هوش مصنوعی"})
    matched = [s["id"] for s in r.json()["sections"] if not s["general"]]
    assert "media-hub" in matched
    assert "ai-iot-conference" not in matched


def test_a_challenge_id_no_longer_pulls_a_stranger_profile_into_the_plan(
        client, other_client, outbox):
    """The old leak: the plan came back shaped by whoever owned that id."""
    r = other_client.post("/api/auth/otp/request", json={
        "destination": DEST_B, "first_name": "زهرا", "last_name": "ک",
        "job": "خبرنگار", "interests": "رسانه"})
    challenge = r.json()["challenge_id"]
    other_client.post("/api/auth/otp/verify",
                      json={"challenge_id": challenge, "code": outbox[-1][1]})

    plan = client.post("/api/visit-plan",
                       json={"challenge_id": challenge, "interests": "هوش مصنوعی"})
    matched = [s["id"] for s in plan.json()["sections"] if not s["general"]]
    assert "ai-iot-conference" in matched, "the body's own fields were ignored"
    assert "media-hub" not in matched, "a stranger's stored profile shaped the plan"


# ── Rate limiting ────────────────────────────────────────────────────────

def test_the_tight_bucket_is_keyed_on_the_session_not_the_body(
        client, outbox, monkeypatch):
    """A bucket key built from a body field is no bucket at all: the caller
    varies the value and gets a fresh, empty one every request."""
    _register(client, outbox, DEST_A)
    seen = []

    import app.routers.otp as otp_router
    monkeypatch.setattr(
        otp_router, "check_rate_limit",
        lambda request: seen.append(getattr(request.state, "otp_limit_identity", "")))

    client.post("/api/auth/profile",
                json={"job": "x", "position": "x", "interests": "x",
                      "challenge_id": "z" * 40, "visitor_id": "spoof"})
    client.post("/api/visit-plan", json={"challenge_id": "z" * 40})

    assert seen == [f"otp:visitor:{_visitor_id(DEST_A)}"] * 2


def test_an_anonymous_plan_gets_no_tight_bucket(client, monkeypatch):
    """Nothing server-issued to key on, so only the per-IP backstop counts."""
    seen = []
    import app.routers.otp as otp_router
    monkeypatch.setattr(
        otp_router, "check_rate_limit",
        lambda request: seen.append(getattr(request.state, "otp_limit_identity", "")))

    client.post("/api/visit-plan", json={"interests": "رسانه"})
    assert seen == [""]


# ── Cross-origin ─────────────────────────────────────────────────────────

def test_a_cross_origin_post_is_refused(client, outbox):
    """The cookie is ambient: the browser attaches it to a POST from any page.

    So every endpoint that consumes or mints it validates the origin. Without
    this, evil.example.com could rewrite a visitor's profile just because they
    happened to have the tab open.
    """
    _register(client, outbox, DEST_A, job="مهندس")
    evil = {"Origin": "https://evil.example.com"}

    for path, body in (("/api/auth/profile", {"job": "مهاجم"}),
                       ("/api/visit-plan", {"interests": "رسانه"}),
                       ("/api/auth/logout", {})):
        r = client.post(path, json=body, headers=evil)
        assert r.status_code == 403, f"{path} accepted a cross-site POST: {r.text}"

    assert _stored(DEST_A)["job"] == "مهندس"
    assert client.get("/api/auth/session").json()["signed_in"] is True


def test_verify_refuses_a_cross_origin_mint(client, outbox):
    """Login CSRF. An attacker knows their OWN challenge and code, so a forged
    POST would drop the ATTACKER's session into the victim's browser and file
    everything the victim then said under the attacker's name."""
    r = client.post("/api/auth/otp/request", json={"destination": DEST_A})
    forged = client.post(
        "/api/auth/otp/verify",
        json={"challenge_id": r.json()["challenge_id"], "code": outbox[-1][1]},
        headers={"Origin": "https://evil.example.com"})
    assert forged.status_code == 403
    assert not client.cookies.get(visitor_auth.VISITOR_COOKIE_NAME)


# ── The invariant, so the next endpoint cannot forget ────────────────────

def _iter_api_routes(routes):
    from fastapi.routing import APIRoute
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_api_routes(nested.routes)
        elif hasattr(route, "routes"):
            yield from _iter_api_routes(route.routes)


def _depends_on(dependant, target, depth=0):
    """True if `target` appears anywhere in the dependency tree.

    Route-level dependencies (the `dependencies=[...]` argument) are merged
    into the same dependant, so one recursive walk covers both styles.
    """
    if depth > 8:
        return False
    return any(sub.call is target or _depends_on(sub, target, depth + 1)
               for sub in dependant.dependencies)


def test_every_visitor_mutation_also_validates_its_origin():
    """The fifth endpoint somebody adds next year must not be the unguarded one.

    Same shape as tests/test_csrf.py's admin walk, and for the same reason: an
    invariant nobody can see in a diff has to be checked by the suite.
    """
    from app.auth.security import validate_request_origin

    mutating = [r for r in _iter_api_routes(app.routes)
                if r.methods & {"POST", "PUT", "PATCH", "DELETE"}]
    guarded = [r for r in mutating
               if _depends_on(r.dependant, visitor_auth.require_visitor)]

    # Non-vacuity: if the walker ever goes blind the assertion below would
    # pass while checking nothing.
    assert len(mutating) >= 50, f"route walker went blind: {len(mutating)} mutating"
    assert guarded, "no route requires a visitor session — the walker is blind"

    missing = [f"{sorted(r.methods)} {r.path}" for r in guarded
               if not _depends_on(r.dependant, validate_request_origin)]
    assert not missing, (
        "these act on the ambient visitor cookie but never check where the "
        f"request came from: {missing}")


def test_no_endpoint_in_this_router_takes_a_challenge_id_as_identity():
    """`challenge_id` survives only on verify and resend, where no session
    exists yet and the id is a single-use capability the server minted."""
    from app.routers import otp as otp_router

    allowed = {"OtpVerifyBody", "OtpResendBody"}
    holders = {model.__name__ for model in
               (otp_router.OtpRequestBody, otp_router.OtpVerifyBody,
                otp_router.OtpResendBody, otp_router.VisitPlanBody,
                otp_router.ProfileUpdateBody)
               if "challenge_id" in model.model_fields}
    assert holders == allowed, f"challenge_id is identity again in: {holders - allowed}"
