"""Ending a visitor's sessions: the control the session table exists for.

WHAT WAS BROKEN
---------------
migrations/0012_visitor_sessions.sql justifies storing visitor sessions as
ROWS instead of a signed token with one argument: a session has to be
revocable the second somebody asks, and a signature cannot be un-signed.
app/auth/visitor.py shipped `revoke_all()` and `purge_expired()` for that.
Nothing in the application called either one.

So the promise was not kept. A visitor registers at the kiosk, their phone is
stolen in the hall, they tell the booth, and an operator had no way to end
the session in that phone. The cookie inside it is the whole credential, and
it stays valid for 30 more days. Separately, `resolve()` deletes an expired
row only when its owner comes back, so on an install that runs for years the
rows nobody comes back for were never deleted at all.

WHAT THIS FILE HOLDS
--------------------
1. The endpoint exists, refuses an unauthenticated caller, and really does
   delete the sessions (asserted through `resolve()`, not by counting rows:
   what matters is that the stolen phone stops working).
2. The retention loop in app/main.py sweeps expired sessions.
3. The operator has a BUTTON. An endpoint with no control on the screen is
   not a fix for the staff who need it, so the template and its JavaScript
   are asserted too, the same way tests/test_admin_js_csrf_conformance.py
   reads the browser's own code.
"""
import asyncio
import datetime
import os
import re
import secrets

import pytest
from fastapi.testclient import TestClient


REVOKE = "/admin/api/visitors/{}/sessions/revoke"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "visitor-revoke.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    yield


@pytest.fixture
def anon(app_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(app_db):
    """An authenticated admin, with the CSRF token the browser would send.

    Same shape as tests/test_conversations_admin.py. The header matters here
    in a way it does not for the read endpoints: this is a POST under
    /admin/, so app/main.py's csrf_protection middleware answers 403 before
    the route runs when the token is missing.
    """
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('panel','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "panel",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _visitor(phone="09120000001"):
    """A real row in `visitors`. The session has a foreign key to it."""
    from app.services.conversations import upsert_visitor
    return upsert_visitor(first_name="سینا", last_name="آزمون", phone=phone,
                          job="مهندس", position="مدیر", interests="رباتیک")


# ── The endpoint ─────────────────────────────────────────────────────────

def test_an_anonymous_caller_cannot_end_anyones_sessions(anon):
    """Whoever can end a visitor's session can also lock them out at will.

    Before the fix this route did not exist and the response was 404, which
    is what made this test fail: 404 is not a refusal, it is an absence.
    """
    visitor_id = _visitor()
    from app.auth import visitor as visitor_auth
    token = visitor_auth.mint(visitor_id)

    response = anon.post(REVOKE.format(visitor_id))

    assert response.status_code in (401, 403), response.status_code
    # The session is untouched. A refusal that still did the work is not one.
    assert visitor_auth.resolve(token) is not None


def test_an_admin_signs_a_visitor_out_of_every_browser(client):
    """The stolen-phone case, end to end.

    Two sessions, because "every browser" is the whole point: the visitor is
    signed in on their phone and on a kiosk, and one call has to kill both.
    Asserted through `resolve()` rather than a row count, because what the
    operator was promised is that the token stops working.
    """
    visitor_id = _visitor()
    from app.auth import visitor as visitor_auth
    phone_token = visitor_auth.mint(visitor_id)
    kiosk_token = visitor_auth.mint(visitor_id)
    assert visitor_auth.resolve(phone_token) is not None
    assert visitor_auth.resolve(kiosk_token) is not None

    response = client.post(REVOKE.format(visitor_id))

    assert response.status_code == 200, response.text
    assert response.json()["revoked"] == 2
    assert visitor_auth.resolve(phone_token) is None
    assert visitor_auth.resolve(kiosk_token) is None


def test_one_persons_sessions_are_not_another_persons(client):
    """The id in the path decides who is signed out, and only them."""
    victim = _visitor(phone="09120000001")
    bystander = _visitor(phone="09120000002")
    from app.auth import visitor as visitor_auth
    victim_token = visitor_auth.mint(victim)
    bystander_token = visitor_auth.mint(bystander)

    client.post(REVOKE.format(victim))

    assert visitor_auth.resolve(victim_token) is None
    assert visitor_auth.resolve(bystander_token) is not None


def test_an_unknown_visitor_is_404_and_not_a_quiet_success(client):
    """A mistyped id must not answer "done".

    `revoke_all()` on an id that does not exist deletes nothing and returns 0,
    which reads as success. An operator cutting off a stolen phone would then
    walk away believing it was done.
    """
    response = client.post(REVOKE.format("no-such-visitor"))
    assert response.status_code == 404


def test_the_revocation_is_written_to_the_audit_trail(client):
    """Cutting off somebody's access is exactly the act an audit trail is for.

    The row names the visitor by ID and never by phone number: audit rows are
    read by more people than the visitor list is.
    """
    from app.services import applog
    applog._recent.clear()      # storm suppression must not swallow this row

    visitor_id = _visitor()
    from app.auth import visitor as visitor_auth
    visitor_auth.mint(visitor_id)

    client.post(REVOKE.format(visitor_id))

    rows, _total = applog.query(tables=["audit_logs"], limit=200)
    mine = [r for r in rows
            if r["event_name"] == "admin.visitor.sessions_revoked"]
    assert mine, [r["event_name"] for r in rows]
    assert mine[0]["actor"] == "panel"
    assert mine[0]["target"] == visitor_id
    assert "09120000001" not in str(dict(mine[0]))


# ── The sweep ────────────────────────────────────────────────────────────

async def test_the_retention_loop_sweeps_expired_visitor_sessions(monkeypatch):
    """`visitor_auth.purge_expired()` had no caller anywhere in the app.

    The loop sleeps six hours before its first pass, so the sleep is replaced
    with one that returns immediately and then cancels the loop. The three
    purges are replaced by recorders: this test is about WHICH ones the loop
    calls, and running the real ones would need a database it does not have.
    """
    import app.main as main
    from app.services import applog
    from app.db import queries
    from app.auth import visitor as visitor_auth

    called = []
    monkeypatch.setattr(applog, "purge_expired", lambda: called.append("applog"))
    monkeypatch.setattr(queries, "purge_chat_logs", lambda: called.append("chat_logs"))
    monkeypatch.setattr(visitor_auth, "purge_expired",
                        lambda: called.append("visitor_sessions"))

    passes = {"n": 0}

    async def _sleep(_seconds):
        passes["n"] += 1
        if passes["n"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await main._retention_loop()

    assert "visitor_sessions" in called, called


def test_purge_expired_really_deletes_a_dead_session(app_db):
    """The sweep the loop calls, doing its job.

    Kept separate from the loop test because the loop test replaces the real
    function. One test proves the loop calls it, this one proves it works.
    """
    from app.db.connection import init_db
    init_db()
    from app.auth import visitor as visitor_auth

    live = visitor_auth.mint(_visitor(phone="09120000001"))
    dead = visitor_auth.mint(_visitor(phone="09120000002"))

    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE visitor_sessions SET expiry = ? WHERE token = ?",
                 (visitor_auth._stamp(past), dead))
    conn.commit()
    conn.close()

    assert visitor_auth.purge_expired() == 1
    assert visitor_auth.resolve(dead) is None
    assert visitor_auth.resolve(live) is not None


# ── The button ───────────────────────────────────────────────────────────
#
# An endpoint nobody can reach from the screen is not a fix for the operator
# standing at the booth. These read the shipped template and the shipped
# JavaScript, the way tests/test_admin_js_csrf_conformance.py does, because a
# request test cannot see whether there is anything to click.

def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_visitors_screen_has_the_button_and_explains_it():
    """Plain Persian, no jargon: the label says what happens to the person."""
    html = _read("templates", "admin", "visitors.html")
    assert 'id="btn-revoke-sessions"' in html
    assert "خروج این نفر از همهٔ دستگاه‌ها" in html
    # The words a non-technical operator would have to look up.
    assert "نشست" not in html and "توکن" not in html


def test_the_button_confirms_and_posts_through_fetch_auth():
    """A bare fetch() sends no X-CSRF-Token, so the middleware answers 403.

    The button would then look like it worked and change nothing. That is the
    failure tests/test_admin_js_csrf_conformance.py was written after.
    """
    js = _read("static", "admin", "js", "visitors.js")
    assert "btn-revoke-sessions" in js
    assert "/sessions/revoke" in js
    # The POST goes through the wrapper, not a bare fetch.
    call = re.search(r"fetchAuth\(\s*\n?\s*'/admin/api/visitors/", js)
    assert call, "the revoke call must go through fetchAuth() from ./utils.js"
    # Ending somebody's access must never be one click.
    assert "confirm(" in js
