"""The visitor session: minted by the server, carried in a cookie, nothing else.

WHAT THIS FILE IS DEFENDING
---------------------------
A registered visitor used to prove who they were by putting data in the
request — a profile in the POST /chat body, a `challenge_id` in the body of
/api/auth/profile. Both are self-asserted, so four extra fields made you
anybody. Identity now comes from a row in `visitor_sessions` that only the
server can create, reached through one cookie that only the server can read.

The middle test below (`TestOnlyTheCookieCounts`) is the one that matters most.
It sends the visitor id in a header, in a body field, and in a query string,
each with NO cookie, and asserts the request is anonymous every time. If any of
those ever passes, the hole is back and the rest of this file is decoration.

Covers app/auth/visitor.py, the `resolve_visitor` middleware in app/main.py,
and migrations/0012_visitor_sessions.sql via its SQLite mirror.
"""
import datetime

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient


PROBE = "/__probe_visitor"
PROBE_FAIL = "/__probe_visitor_fail"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The REAL app, plus two throwaway routes that report request.state.

    The real app on purpose: half of what is being tested is that the
    middleware runs at all, on every path, in the right place in the stack. A
    hand-built app with only this middleware would prove none of that.

    The probe routes are removed again in teardown, because `app` is a
    module-level singleton the whole suite shares.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "visitor.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app as fastapi_app

    async def _probe(request: Request):
        # getattr with a sentinel, not "": the middleware promises to set both
        # fields on EVERY request, and "<missing>" is how a broken promise
        # shows up as a failure instead of passing as "anonymous".
        profile = getattr(request.state, "visitor", "<missing>")
        return {
            "visitor_id": getattr(request.state, "visitor_id", "<missing>"),
            "job": getattr(profile, "job", None) if profile else profile,
        }

    async def _probe_fail(request: Request):
        raise HTTPException(status_code=418, detail="teapot")

    fastapi_app.add_api_route(PROBE, _probe, methods=["GET", "POST"])
    fastapi_app.add_api_route(PROBE_FAIL, _probe_fail, methods=["GET"])
    try:
        with TestClient(fastapi_app) as c:
            yield c
    finally:
        fastapi_app.router.routes = [
            r for r in fastapi_app.router.routes
            if getattr(r, "path", "") not in (PROBE, PROBE_FAIL)]


def _make_visitor(job="مهندس", position="مدیر", interests="رباتیک"):
    """A real row in `visitors`, because the session has a foreign key to it."""
    from app.services.conversations import upsert_visitor
    return upsert_visitor(first_name="سینا", last_name="آزمون",
                          phone="09120000001", job=job, position=position,
                          interests=interests)


def _cookie_name():
    from app.auth.visitor import VISITOR_COOKIE_NAME
    return VISITOR_COOKIE_NAME


def _expire(token, *, days_ago=1):
    """Push a session's expiry into the past, the way real time would."""
    from app.auth.visitor import _stamp
    from app.db.connection import get_db_connection
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    conn = get_db_connection()
    conn.execute("UPDATE visitor_sessions SET expiry = ? WHERE token = ?",
                 (_stamp(past), token))
    conn.commit()
    conn.close()


# ── The store ────────────────────────────────────────────────────────────

class TestLifecycle:

    def test_a_minted_session_resolves_to_its_visitor(self, client):
        from app.auth import visitor as v
        visitor_id = _make_visitor()

        token = v.mint(visitor_id)
        assert token and len(token) > 30    # secrets.token_urlsafe(32)

        session = v.resolve(token)
        assert session is not None
        assert session["visitor_id"] == visitor_id
        # The profile comes back in the SAME shape POST /chat used to accept in
        # its body, so the pipeline downstream does not care where it came from.
        assert session["profile"].job == "مهندس"
        assert session["profile"].position == "مدیر"
        assert session["profile"].interests == "رباتیک"

    def test_an_unknown_token_resolves_to_nothing(self, client):
        from app.auth import visitor as v
        _make_visitor()
        v.mint(_make_visitor())

        # Right shape, never issued. Guessing is the attack this refuses.
        assert v.resolve("Zm9vYmFyYmF6cXV1eGNvcmdlZ3JhdWx0Z2FycGx5") is None
        assert v.resolve("") is None
        assert v.resolve(None) is None

    def test_an_expired_session_resolves_to_nothing(self, client):
        from app.auth import visitor as v
        from app.db.connection import get_db_connection
        token = v.mint(_make_visitor())
        assert v.resolve(token) is not None

        _expire(token)
        assert v.resolve(token) is None

        # And the dead row is gone, not just refused. Same lazy delete-on-read
        # admin_sessions has done since 0001.
        conn = get_db_connection()
        row = conn.execute("SELECT token FROM visitor_sessions WHERE token = ?",
                           (token,)).fetchone()
        conn.close()
        assert row is None

    def test_resolve_slides_the_expiry_so_an_active_visitor_stays_in(self, client):
        from app.auth import visitor as v
        from app.db.connection import get_db_connection
        token = v.mint(_make_visitor())

        # Two days in, not expired. A resolve must push the expiry back out to
        # the full window, which is what makes the lifetime "days of
        # inactivity" instead of a hard cap.
        _expire(token, days_ago=-2)     # negative: two days into the FUTURE
        conn = get_db_connection()
        before = conn.execute(
            "SELECT expiry FROM visitor_sessions WHERE token = ?",
            (token,)).fetchone()["expiry"]
        conn.close()

        assert v.resolve(token) is not None

        conn = get_db_connection()
        after = conn.execute(
            "SELECT expiry FROM visitor_sessions WHERE token = ?",
            (token,)).fetchone()["expiry"]
        conn.close()
        assert str(after) > str(before)

    def test_revoke_kills_the_session_immediately(self, client):
        from app.auth import visitor as v
        token = v.mint(_make_visitor())
        assert v.resolve(token) is not None

        v.revoke(token)
        assert v.resolve(token) is None

        # Idempotent: a second sign-out, or a token that never existed, is not
        # an error anyone has to handle.
        v.revoke(token)
        v.revoke("never-issued")

    def test_revoke_all_signs_one_person_out_of_every_browser(self, client):
        from app.auth import visitor as v
        visitor_id = _make_visitor()
        phone_token = v.mint(visitor_id)
        laptop_token = v.mint(visitor_id)
        # A different person's session must survive the lost-phone button.
        from app.services.conversations import upsert_visitor
        other_id = upsert_visitor(first_name="دیگری", phone="09120000002")
        other_token = v.mint(other_id)

        assert v.revoke_all(visitor_id) == 2

        assert v.resolve(phone_token) is None
        assert v.resolve(laptop_token) is None
        assert v.resolve(other_token) is not None

    def test_purge_expired_clears_rows_nobody_came_back_for(self, client):
        from app.auth import visitor as v
        dead = v.mint(_make_visitor())
        from app.services.conversations import upsert_visitor
        alive = v.mint(upsert_visitor(first_name="زنده", phone="09120000003"))
        _expire(dead, days_ago=40)

        assert v.purge_expired() >= 1
        assert v.resolve(dead) is None
        assert v.resolve(alive) is not None

    def test_mint_refuses_an_empty_visitor_id(self, client):
        from app.auth import visitor as v
        # "" is the anonymous marker everywhere else in this codebase
        # (conversations.visitor_id). A session belonging to nobody has no
        # meaning, so it is never created.
        assert v.mint("") == ""
        assert v.mint(None) == ""


# ── The middleware ───────────────────────────────────────────────────────

class TestMiddleware:

    def test_the_cookie_puts_the_visitor_on_request_state(self, client):
        from app.auth import visitor as v
        visitor_id = _make_visitor()
        token = v.mint(visitor_id)

        client.cookies.set(_cookie_name(), token)
        body = client.get(PROBE).json()

        assert body["visitor_id"] == visitor_id
        assert body["job"] == "مهندس"

    def test_a_request_with_no_cookie_is_anonymous_but_still_stamped(self, client):
        # Both fields are set on EVERY request so no downstream reader needs a
        # getattr default. "" is anonymous; "<missing>" would mean the
        # middleware did not run.
        body = client.get(PROBE).json()
        assert body["visitor_id"] == ""
        assert body["job"] is None

    def test_the_middleware_re_issues_the_cookie_on_activity(self, client):
        from app.auth import visitor as v
        token = v.mint(_make_visitor())
        client.cookies.set(_cookie_name(), token)

        response = client.get(PROBE)
        header = response.headers.get("set-cookie", "")
        assert _cookie_name() in header
        # The DB row slides on every hit; without this the browser's copy would
        # still die 30 days after the mint however active the visitor was.
        assert token in header

    def test_an_error_response_is_not_activity(self, client):
        from app.auth import visitor as v
        token = v.mint(_make_visitor())
        client.cookies.set(_cookie_name(), token)

        response = client.get(PROBE_FAIL)
        assert response.status_code == 418
        # Same guard slide_admin_cookie earned: a 4xx/5xx must not renew a
        # session. Nothing was accomplished, so nothing is extended.
        assert _cookie_name() not in response.headers.get("set-cookie", "")

    def test_asset_traffic_never_pays_for_a_lookup(self, client, monkeypatch):
        from app.auth import visitor as v
        calls = []
        monkeypatch.setattr(v, "resolve",
                            lambda token: calls.append(token) or None)

        client.cookies.set(_cookie_name(), "anything")
        client.get("/static/chat/base.css")
        # A page with forty images would otherwise cost forty pooled
        # connections and forty queries.
        assert calls == []

    def test_it_is_the_innermost_middleware(self):
        """Position in the stack, asserted so a later edit cannot move it.

        Starlette wraps in REVERSE registration order, and `user_middleware` is
        outermost-first. resolve_visitor must sit after every guard that can
        short-circuit (so a 413 or 403 costs no lookup) and after
        request_correlation (so its log rows carry a request id), while its
        response half runs first so nothing downstream strips its Set-Cookie.
        """
        from app.main import app as fastapi_app
        names = []
        for mw in fastapi_app.user_middleware:
            dispatch = (mw.kwargs or {}).get("dispatch")
            names.append(getattr(dispatch, "__name__",
                                 getattr(mw.cls, "__name__", "?")))

        assert "resolve_visitor" in names
        here = names.index("resolve_visitor")
        for outer in ("csrf_protection", "reject_oversized_bodies",
                      "request_correlation", "security_headers",
                      "slide_admin_cookie"):
            assert here > names.index(outer), f"{outer} must wrap resolve_visitor"


# ── The whole point ──────────────────────────────────────────────────────

class TestOnlyTheCookieCounts:
    """Identity must be unreachable from anywhere a caller controls.

    Each test hands the server a REAL, currently-valid visitor id through a
    channel the client writes, with no cookie at all. Anonymous is the only
    acceptable answer. These are separate tests, not one loop, so a failure
    names the exact channel that opened.
    """

    def test_a_header_carrying_the_visitor_id_is_ignored(self, client):
        visitor_id = _make_visitor()

        body = client.get(PROBE, headers={
            "X-Visitor-Id": visitor_id,
            "X-Visitor": visitor_id,
            "Authorization": f"Bearer {visitor_id}",
        }).json()

        assert body["visitor_id"] == ""
        assert body["job"] is None

    def test_a_body_field_carrying_the_visitor_id_is_ignored(self, client):
        visitor_id = _make_visitor()

        # The exact shape the old hole accepted: a profile and an identity,
        # both posted by the caller.
        body = client.post(PROBE, json={
            "visitor_id": visitor_id,
            "challenge_id": visitor_id,
            "visitor": {"job": "مدیرعامل", "position": "هیئت مدیره",
                        "interests": "همه چیز"},
        }).json()

        assert body["visitor_id"] == ""
        assert body["job"] is None

    def test_a_query_parameter_carrying_the_visitor_id_is_ignored(self, client):
        visitor_id = _make_visitor()

        body = client.get(
            PROBE, params={"visitor_id": visitor_id, "visitor": visitor_id}
        ).json()

        assert body["visitor_id"] == ""
        assert body["job"] is None

    def test_the_cookie_must_hold_a_MINTED_TOKEN_not_a_visitor_id(self, client):
        """The right door still needs the right key.

        Putting the visitor id straight into the cookie is the mistake the
        leads module made (app/services/leads.py stores lead_visitors.id in its
        cookie), and it makes the credential guessable from anything that ever
        printed an id. Here the cookie value is a token that exists only in
        `visitor_sessions`.
        """
        visitor_id = _make_visitor()
        client.cookies.set(_cookie_name(), visitor_id)

        assert client.get(PROBE).json()["visitor_id"] == ""


# ── The cookie itself ────────────────────────────────────────────────────

class TestCookieAttributes:

    def test_the_cookie_is_httponly_and_samesite_lax(self):
        from app.auth import visitor as v
        response = Response()
        v.set_cookie(response, "a-token")

        header = response.headers["set-cookie"].lower()
        # HttpOnly is the reason this replaced a challenge id kept in
        # localStorage, where any XSS could read the credential.
        assert "httponly" in header
        # Lax, not Strict: a visitor tapping the link in their SMS arrives by
        # top-level navigation and must still be signed in. The origin check on
        # every endpoint that consumes this cookie is what makes Lax enough.
        assert "samesite=lax" in header

    def test_clearing_uses_the_same_attributes_as_setting(self):
        from app.auth import visitor as v
        response = Response()
        v.clear_cookie(response)

        header = response.headers["set-cookie"].lower()
        assert v.VISITOR_COOKIE_NAME.lower() in header
        # A delete_cookie() with different attributes does not match, so the
        # browser keeps the old cookie and the visitor stays signed in.
        assert "httponly" in header
        assert "samesite=lax" in header
        assert "max-age=0" in header or "expires=" in header

    def test_the_name_does_not_collide_with_the_leads_cookie(self):
        """Two different tables call their rows "visitors".

        app.visitors is a member of the public who registered in the chat.
        app.lead_visitors is booth STAFF holding a personal capture link, and
        its cookie is already called padyar_visitor on path "/". One name for
        both means a staff member who also chats loses their /v panel and hands
        their capture id to this session lookup.
        """
        from app.auth.visitor import VISITOR_COOKIE_NAME
        from app.services.leads import VISITOR_COOKIE as LEADS_COOKIE

        assert VISITOR_COOKIE_NAME != LEADS_COOKIE


# ── Failure modes ────────────────────────────────────────────────────────

class TestFailuresDegradeToAnonymous:

    def test_a_broken_database_yields_anonymous_not_a_500(self, client, monkeypatch):
        from app.auth import visitor as v
        import app.db.connection as connection
        token = v.mint(_make_visitor())
        client.cookies.set(_cookie_name(), token)

        def _explode():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(connection, "get_db_connection", _explode)

        response = client.get(PROBE)
        # A storage blip must not become a site-wide 500 on GET /. Anonymous is
        # also the least privileged answer, so degrading here still fails safe.
        assert response.status_code == 200
        assert response.json()["visitor_id"] == ""

    def test_mint_and_revoke_swallow_a_broken_database(self, client, monkeypatch):
        from app.auth import visitor as v
        import app.db.connection as connection
        visitor_id = _make_visitor()

        def _explode():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(connection, "get_db_connection", _explode)

        # A failed mint means the person is registered but not signed in. That
        # is recoverable on the next verify; an exception during signup is not.
        assert v.mint(visitor_id) == ""
        v.revoke("some-token")               # must not raise
        assert v.revoke_all(visitor_id) == 0
        assert v.purge_expired() == 0

    def test_a_malformed_cookie_is_anonymous_not_an_error(self, client):
        for junk in ("", "   ", "not a token", "../../etc/passwd",
                     "x" * 5000, "%00%01", "'; DROP TABLE visitor_sessions--"):
            client.cookies.set(_cookie_name(), junk)
            response = client.get(PROBE)
            assert response.status_code == 200, junk
            assert response.json()["visitor_id"] == "", junk


# ── The dependency ───────────────────────────────────────────────────────

class TestRequireVisitor:
    """The one function here that raises. Everything else degrades."""

    def _request(self, visitor_id):
        class _State:
            pass

        class _Request:
            state = _State()

        req = _Request()
        req.state.visitor_id = visitor_id
        return req

    def test_it_returns_the_id_when_a_session_resolved(self):
        from app.auth.visitor import require_visitor
        assert require_visitor(self._request("abc123")) == "abc123"

    def test_it_raises_401_when_anonymous(self):
        from app.auth.visitor import require_visitor
        with pytest.raises(HTTPException) as caught:
            require_visitor(self._request(""))
        assert caught.value.status_code == 401

    def test_the_401_carries_a_machine_readable_marker(self):
        """The frontend has to open the signup modal on THIS 401 and not on
        every other one. It branches on a code; matching Persian prose is not a
        contract."""
        from app.auth.visitor import require_visitor, REGISTRATION_REQUIRED
        with pytest.raises(HTTPException) as caught:
            require_visitor(self._request(""))

        detail = caught.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == REGISTRATION_REQUIRED
        assert detail["message"]

    def test_it_never_touches_storage(self, monkeypatch):
        """It reads state the middleware already resolved, so it cannot be
        talked into looking somewhere else, and a database blip cannot turn a
        signed-in visitor into a 401."""
        import app.db.connection as connection

        def _explode():
            raise AssertionError("require_visitor must not query anything")

        monkeypatch.setattr(connection, "get_db_connection", _explode)
        from app.auth.visitor import require_visitor
        assert require_visitor(self._request("abc123")) == "abc123"


# ── Schema ───────────────────────────────────────────────────────────────

class TestSchema:

    def test_the_sqlite_mirror_matches_the_migration(self, client):
        """Both halves of a schema change have to land, every time.

        PostgreSQL gets migrations/0012_visitor_sessions.sql; the test backend
        gets _create_visitor_sessions_table() in app/db/connection.py. A column
        added to one and not the other passes every test until production.
        """
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        columns = {r["name"] for r in
                   conn.execute("PRAGMA table_info(visitor_sessions)").fetchall()}
        conn.close()
        assert columns == {"token", "visitor_id", "created_at", "expiry",
                           "last_seen"}

    def test_the_migration_file_declares_the_same_columns(self):
        from pathlib import Path
        from app.config import BASE_DIR
        sql = Path(BASE_DIR, "migrations",
                   "0012_visitor_sessions.sql").read_text(encoding="utf-8")
        for column in ("token", "visitor_id", "created_at", "expiry",
                       "last_seen"):
            assert column in sql
        assert "app.visitor_sessions" in sql

        # Revocation is DELETE, never a flag. A boolean column here would also
        # trip tests/test_sql_boolean_portability.py, which keys its checks on
        # the bare column name across every table, so a `revoked` flag would
        # make every unrelated `revoked = 0` in app/ look like a bug.
        # The header prose explains that, so only the DDL is searched.
        ddl = "\n".join(line for line in sql.splitlines()
                        if not line.lstrip().startswith("--"))
        assert "BOOLEAN" not in ddl.upper()
