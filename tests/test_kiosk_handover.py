"""The person who sits down second must not become the person who sat first.

A booth kiosk is ONE browser shared by strangers all day. tests/
test_kiosk_privacy.py already holds two controls to that rule (the history
window and the "New chat" button). This file holds the four that were still
open, and they are all the same bug wearing different clothes.

1. THE SESSION HAD NO CAP A KIOSK COULD REACH. `resolve()` writes a new expiry
   on every hit, so VISITOR_SESSION_DAYS is INACTIVITY, and a kiosk in
   continuous use never goes inactive: it is the NEXT person's traffic that
   renews the session. Lowering the days changed nothing. The cap is now
   counted from `created_at`, which nothing ever moves.

2. THE ROLLING SUMMARY WALKED THROUGH THE HISTORY WINDOW. recent_turns() stops
   at HISTORY_WINDOW_MINUTES so a visitor's raw words never reach the next
   visitor's prompt. get_summary() had no bound at all, so the same words
   reached it anyway, compressed into a paragraph. A summary is not a
   different kind of data.

3. THE LOGOUT BUTTON WAS NEVER DRAWN FOR A /verify VISITOR. It was keyed on
   having a name, and the /verify page posts only a phone number: those
   visitors have first_name = '' and last_name = '', so the button was removed
   for exactly the people who most needed it. Signed in for weeks, with no
   control anywhere in the UI to sign out.

4. SIGNING OUT LEFT THE PREVIOUS PERSON'S TRANSCRIPT ON THE SCREEN. Sign-out
   revoked the session and refreshed the header, and nothing else: the bubbles
   stayed, and core.js loadHistory() replayed them out of localStorage on the
   next page load. The strongest "I am leaving" gesture in the product forgot
   less than the "New chat" button did.

The browser half at the bottom loads the REAL rendered chat page, for the
reason tests/test_kiosk_privacy.py explains at length: hand-written fixture
markup can only test the simplification. Defects 3 and 4 live in the header
and the transcript of that real page.

Every browser test here uses Playwright's ASYNC api and defines its own
`browser` fixture. pytest.ini sets asyncio_mode = auto, and one sync browser
fixture takes the whole suite down with it.
"""
import datetime
import json
import mimetypes
import os
import time
from pathlib import Path

import pytest

from app.services import conversations


ROOT = Path(__file__).resolve().parent.parent
CORE_JS = ROOT / "static" / "chat" / "core.js"
REGISTRATION_JS = ROOT / "static" / "companion" / "registration.js"

CONV = "conv-kiosk-handover"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite database, without booting the whole app.

    Same shape as the `db` fixture in tests/test_kiosk_privacy.py. Nothing
    below this line needs a request, so nothing below it pays for an app.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "handover.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db, get_db_connection
    init_db()
    return get_db_connection


# ── 1. The session's hard cap ────────────────────────────────────────────

def _make_visitor(phone="09120000001"):
    """A real row in `visitors`, because the session has a foreign key to it."""
    return conversations.upsert_visitor(first_name="", last_name="",
                                        phone=phone)


def _age_session(token, *, hours):
    """Push a session's `created_at` back, the way a long day would.

    Written in SQLite's own CURRENT_TIMESTAMP shape ('YYYY-MM-DD HH:MM:SS',
    naive and already UTC) because that is what the column really holds: the
    row is created by DEFAULT CURRENT_TIMESTAMP and nothing in Python writes
    it. A test that wrote an offset-carrying string instead would exercise a
    value production never stores.
    """
    from app.db.connection import get_db_connection
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours))
    conn = get_db_connection()
    conn.execute("UPDATE visitor_sessions SET created_at = ? WHERE token = ?",
                 (past.strftime("%Y-%m-%d %H:%M:%S"), token))
    conn.commit()
    conn.close()


def _expiry_of(token):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    row = conn.execute("SELECT expiry FROM visitor_sessions WHERE token = ?",
                       (token,)).fetchone()
    conn.close()
    return str(row["expiry"]) if row else ""


class TestSessionHardCap:

    def test_the_cap_kills_a_session_whose_expiry_is_still_sliding(self, db):
        """THE KIOSK CASE, exactly. The session has been used all day, so its
        `expiry` is thirty days out and gets pushed further out on every
        request. Nothing about the expiry will ever expire it. Only the age of
        the row says the visitor who opened it went home hours ago.
        """
        from app.auth import visitor as v
        token = v.mint(_make_visitor())
        assert v.resolve(token) is not None

        _age_session(token, hours=13)
        # The expiry is NOT the thing under test: it is deliberately left
        # far in the future, which is the state a busy kiosk keeps it in.
        future = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        assert _expiry_of(token) > future, _expiry_of(token)

        assert v.resolve(token) is None
        # Dead, not just refused. Same lazy delete-on-read the expiry path uses.
        assert _expiry_of(token) == ""

    def test_a_session_inside_the_cap_is_untouched(self, db):
        """The cap must not sign out the visitor it was written for: someone
        who registered this morning and is still walking the same hall."""
        from app.auth import visitor as v
        token = v.mint(_make_visitor())
        _age_session(token, hours=11)
        assert v.resolve(token) is not None

    def test_traffic_does_not_buy_a_session_more_time(self, db):
        """The whole point of counting from `created_at`. Being used is what
        kept the old session alive; here it buys nothing."""
        from app.auth import visitor as v
        token = v.mint(_make_visitor())

        _age_session(token, hours=11)
        assert v.resolve(token) is not None      # this hit slides the expiry
        assert v.resolve(token) is not None      # and so does this one

        _age_session(token, hours=13)
        assert v.resolve(token) is None

    def test_a_session_whose_creation_time_cannot_be_read_is_refused(self, db):
        """NULL is impossible (NOT NULL on both backends), but a value nobody
        can parse is not: SQLite stores this column as text and takes whatever
        it is given. "We cannot tell how old this is" must never mean "keep it
        forever"."""
        from app.auth import visitor as v
        from app.db.connection import get_db_connection
        token = v.mint(_make_visitor())
        conn = get_db_connection()
        conn.execute("UPDATE visitor_sessions SET created_at = ?"
                     " WHERE token = ?", ("not a timestamp", token))
        conn.commit()
        conn.close()

        assert v.resolve(token) is None

    @pytest.mark.skipif(not hasattr(time, "tzset"),
                        reason="switching the process timezone needs tzset")
    def test_the_cap_does_not_misfire_on_a_host_that_is_not_on_utc(
            self, db, monkeypatch):
        """WHY THE COMPARISON USES to_naive_utc ON BOTH SIDES.

        `created_at` comes back naive from SQLite (DEFAULT CURRENT_TIMESTAMP,
        already UTC) and aware from PostgreSQL. compare_now() answers a naive
        value with LOCAL now, so on a host in Tehran a session created one
        second ago would look three and a half hours old, and a cap of one
        hour would sign every visitor out on their very next request. That is
        the same timezone bug that once expired every admin's second request.

        The process timezone is moved for the length of this test so the
        failure is deterministic instead of depending on where CI runs.
        """
        from app.auth import visitor as v
        # Patch the ENFORCING module's binding: app/auth/visitor.py imports
        # the value at module scope, so app.config's copy is not what runs.
        monkeypatch.setattr(v, "VISITOR_SESSION_MAX_HOURS", 1)

        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tehran"          # UTC+3:30, never UTC
        time.tzset()
        try:
            token = v.mint(_make_visitor())
            assert v.resolve(token) is not None
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()

    def test_the_hard_cap_is_shorter_than_the_sliding_window(self):
        """A cap longer than the inactivity window would change nothing, which
        is the state this fix found the code in."""
        from app.config import VISITOR_SESSION_DAYS, VISITOR_SESSION_MAX_HOURS
        assert 1 <= VISITOR_SESSION_MAX_HOURS < VISITOR_SESSION_DAYS * 24


# ── 2. The rolling summary's window ──────────────────────────────────────

SUMMARY = "بازدیدکننده دنبال غرفه‌های رباتیک بود و شماره تماس گرفت."


def _age_conversation(conversation_id, *, minutes):
    """Move the conversation's last message back in time.

    The interval is inlined, not bound: app/db/pg.py only rewrites
    `datetime('now', ...)` when it can see the literal, and a test that bound
    it would pass here and fail on the production backend.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "UPDATE conversations SET last_message_at ="
        f" datetime('now','-{int(minutes)} minutes') WHERE id = ?",
        (conversation_id,))
    conn.commit()
    conn.close()


def _conversation_row(conversation_id=CONV) -> dict:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return dict(conn.execute("SELECT * FROM conversations WHERE id = ?",
                                 (conversation_id,)).fetchone())
    finally:
        conn.close()


def _previous_visitor_talked(turns=3):
    """A conversation with a summary already stored, ready to go stale.

    Returns the id of the last message written, which is where the line has
    to end up once that conversation goes stale.
    """
    for i in range(turns):
        conversations.append_visitor_message(CONV, f"پرسش نفر قبلی {i}")
        newest = conversations.append_assistant_message(
            CONV, f"پاسخ {i}", source="local", confidence=0.9)
    # summary_upto_id 1: the stored summary covers the first message only,
    # which is the normal state while a conversation is still growing.
    conversations.set_summary(CONV, SUMMARY, 1)
    return newest


class TestSummaryWindow:

    def test_a_live_conversation_keeps_its_summary(self, db):
        """The bound must not break the feature. A visitor who has been
        talking for a while is who the summary exists for."""
        _previous_visitor_talked()
        _age_conversation(CONV, minutes=8)
        assert conversations.get_summary(CONV) == SUMMARY

    def test_a_stale_conversation_does_not_hand_over_its_summary(self, db):
        """THE BUG. The padyar_conv cookie slides on every answer, so one
        conversation id covers everyone who touches the kiosk that day. The
        next person's first question shipped a condensed version of the
        previous person's visit to the AI provider."""
        _previous_visitor_talked()
        _age_conversation(CONV, minutes=40)
        assert conversations.get_summary(CONV) == ""

    def test_the_stale_summary_does_not_come_back_on_the_next_turn(self, db):
        """HIDING IT IS NOT ENOUGH, and this is the test that says why.

        update_summary() folds the messages after `summary_upto_id` into the
        stored summary. A summary that was only hidden is merged with the new
        visitor's first messages, stamped with a fresh `last_message_at`, and
        served again two turns later. So the stale read deletes it and moves
        the line past everything written before the gap.
        """
        last_old_message = _previous_visitor_talked()
        _age_conversation(CONV, minutes=40)

        assert conversations.get_summary(CONV) == ""

        # The new visitor types. `last_message_at` is fresh again, so the
        # window no longer hides anything.
        conversations.append_visitor_message(CONV, "پرسش نفر تازه")
        assert conversations.get_summary(CONV) == ""

        row = _conversation_row()
        assert row["summary"] == ""
        # The line has moved past the previous visitor's last message, so the
        # next summary is built only from what this person said.
        assert row["summary_upto_id"] >= last_old_message

    def test_an_unknown_conversation_is_still_just_an_empty_summary(self, db):
        assert conversations.get_summary("no-such-conversation") == ""
        assert conversations.get_summary("") == ""

    def test_the_model_is_not_handed_a_stale_summary(self, db):
        """The same rule seen from the caller. app/routers/chat.py
        _history_for() puts the summary in the oldest slot of the block the
        selection tier reads; on a stale conversation that slot must be
        empty."""
        from app.routers import chat as chat_router
        _previous_visitor_talked()

        _age_conversation(CONV, minutes=8)
        fresh = chat_router._history_for(CONV, "fa")
        assert [h["response"] for h in fresh] == [SUMMARY]

        _age_conversation(CONV, minutes=40)
        assert chat_router._history_for(CONV, "fa") == []


# ── 3 and 4, in source: one function forgets the transcript ──────────────

def _js_function(source: str, header: str) -> str:
    """One JavaScript function's text, from its header to its closing brace.

    Matched on indentation: the closing brace of a function declared at N
    spaces is the first line that is exactly those N spaces and '}'. Crude on
    purpose. It keeps these assertions inside the function under test, so a
    matching string somewhere else in a 1400 line file cannot make one pass.
    """
    start = source.index(header)
    indent = " " * (len(header) - len(header.lstrip()))
    end = source.index(f"\n{indent}}}", start)
    return source[start:end]


def test_the_transcript_is_forgotten_in_exactly_one_place():
    """Both halves of forgetting live in one function now.

    The count assertions are the point: the localStorage key and the
    two-exclusion selector each appear ONCE in the file. A second copy is how
    the two callers drifted apart in the first place, and the selector is the
    one that bricks the chat when it is copied wrong (removing #loading-bubble
    makes the next addMessage() throw NotFoundError).
    """
    core = CORE_JS.read_text(encoding="utf-8")
    assert "function forgetTranscript()" in core
    body = _js_function(core, "function forgetTranscript()")
    assert "removeItem(CHAT_HISTORY_KEY)" in body
    assert ":not(#welcome-message):not(#loading-bubble)" in body

    assert core.count("removeItem(CHAT_HISTORY_KEY)") == 1
    assert core.count(":not(#welcome-message):not(#loading-bubble)") == 1


def test_the_new_chat_button_goes_through_it():
    core = CORE_JS.read_text(encoding="utf-8")
    handler = core[core.index("'new-chat-btn'"):]
    handler = handler[:handler.index("\n    });")]
    assert "forgetTranscript()" in handler


def test_signing_out_goes_through_it():
    """Sign-out must forget at least as much as "New chat" does.

    The guard is asserted too: core.js is loaded before registration.js by the
    theme footer, but this script also runs on pages that have no chat, and a
    ReferenceError inside the click handler would stop the sign-out request
    from ever being sent.
    """
    reg = REGISTRATION_JS.read_text(encoding="utf-8")
    body = _js_function(reg, "    function logout()")
    assert "forgetTranscript()" in body
    assert "typeof forgetTranscript === 'function'" in body


def test_the_logout_button_is_keyed_on_the_session_not_on_a_name():
    """A visitor who registered on /verify has no name. The button they need
    must not depend on one."""
    reg = REGISTRATION_JS.read_text(encoding="utf-8")
    body = _js_function(reg, "    function paintSession()")
    assert "server.signed_in || displayName(p)" in body


# ── 3 and 4, in the browser that actually runs the handlers ──────────────

ORIGIN = "http://padyar.test"

SUGGESTIONS_STUB = [
    {"title": "ساعت کاری", "title_en": "Opening hours"},
    {"title": "محل برگزاری", "title_en": "Venue"},
]

# A visitor who registered on /verify: a verified phone number and NOTHING
# else. This empty-name profile is the whole of defect 3.
NAMELESS_PROFILE = {"first_name": "", "last_name": "", "job": "",
                    "position": "", "interests": "", "phone": "0912***0001"}

ANSWER = {"type": "text", "text": "پاسخ نفر قبلی"}

_PAGE_HTML = None


def _rendered_page(db_path, monkeypatch) -> str:
    """The active theme's chat page, exactly as the app serves it."""
    global _PAGE_HTML
    if _PAGE_HTML is not None:
        return _PAGE_HTML
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/")
    assert r.status_code == 200, r.status_code
    _PAGE_HTML = r.text
    return _PAGE_HTML


def _disk_path(url_path: str):
    """Map a URL path onto a file in the repository, or None."""
    url_path = url_path.split("?")[0].lstrip("/")
    parts = url_path.split("/")
    if len(parts) >= 3 and parts[0] == "themes":
        candidate = ROOT / "themes" / parts[1] / "static" / "/".join(parts[2:])
    else:
        candidate = ROOT / url_path
    try:
        candidate = candidate.resolve()
        candidate.relative_to(ROOT)
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


class Kiosk:
    """One open kiosk page, plus what the stubbed server was asked for."""

    def __init__(self, page, calls, errors):
        self.page = page
        self.calls = calls
        self.errors = errors

    def hits(self, path):
        return self.calls.count(path)


@pytest.fixture
async def browser():
    async_playwright = pytest.importorskip(
        "playwright.async_api").async_playwright
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 — no browser installed is a skip
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        await b.close()


@pytest.fixture
async def signed_in_kiosk(browser, tmp_path, monkeypatch):
    """The real chat page, with a signed-in visitor who has no name.

    The registration module is reported ON, because that is what makes
    registration.js ask the server who is here; with it off the script never
    probes and the header is never painted. /api/auth/logout flips the stub,
    so the session refresh that follows it answers the way the real server
    would.
    """
    html = _rendered_page(str(tmp_path / "kiosk.db"), monkeypatch)
    contexts = []

    async def factory():
        session = {"signed_in": True}
        calls = []
        errors = []

        async def handle(route, request):
            path = request.url[len(ORIGIN):].split("?")[0] or "/"
            calls.append(path)

            def send(body, status=200):
                return route.fulfill(status=status,
                                     content_type="application/json",
                                     body=json.dumps(body))

            if path == "/":
                return await route.fulfill(status=200,
                                           content_type="text/html", body=html)
            if path == "/api/suggestions":
                return await send(SUGGESTIONS_STUB)
            if path == "/api/voice-status":
                return await send({"voice_enabled": False, "tts_enabled": False})
            if path == "/api/auth/registration-status":
                return await send({"enabled": True, "code_length": 6})
            if path == "/api/auth/session":
                return await send({
                    "signed_in": session["signed_in"],
                    "profile": NAMELESS_PROFILE if session["signed_in"] else {},
                })
            if path == "/api/auth/logout":
                session["signed_in"] = False
                return await send({"signed_in": False})
            if path == "/chat":
                return await send(ANSWER)

            disk = _disk_path(path)
            if disk is not None:
                ctype = (mimetypes.guess_type(disk.name)[0]
                         or "application/octet-stream")
                return await route.fulfill(status=200, content_type=ctype,
                                           body=disk.read_bytes())
            return await route.fulfill(status=404, content_type="text/plain",
                                       body="")

        context = await browser.new_context()
        contexts.append(context)
        page = await context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route(f"{ORIGIN}/**", handle)
        await page.goto(f"{ORIGIN}/")
        await page.wait_for_function("typeof initChat === 'function'")
        await page.wait_for_function(
            "document.documentElement.dataset.visitor === 'in'")
        return Kiosk(page, calls, errors)

    yield factory
    for context in contexts:
        await context.close()


async def _click(page, selector):
    """Fire the element's own click handler from inside the page.

    Playwright's .click() first checks the element is in the viewport, and the
    themes lay the header out inside a fixed frame that makes these controls
    report as outside it. The listener under test is the same one.
    """
    await page.evaluate("sel => document.querySelector(sel).click()", selector)


async def _say(page, text):
    """Type a question and send it, the way the send button does."""
    await page.evaluate(
        "text => { document.getElementById('user-input').value = text;"
        "          sendMessage(false); }", text)


async def _wait_for_text(page, needle):
    await page.wait_for_function(
        "needle => document.getElementById('chat-view-content')"
        "                 .innerText.includes(needle)", arg=needle)


async def _bubbles(page):
    """The transcript's real messages: not the welcome, not the loader."""
    return await page.evaluate(
        "() => Array.from(document.querySelectorAll('#chat-view-content"
        " .message:not(#welcome-message):not(#loading-bubble)'))"
        "        .map(el => el.innerText.trim().slice(0, 30))")


async def test_a_visitor_with_no_name_still_gets_a_sign_out_button(
        signed_in_kiosk):
    """DEFECT 3. The button used to be keyed on `displayName(p)`, and the
    /verify page posts only a phone number. Those visitors were signed in for
    weeks with no way out of it that they could see."""
    kiosk = await signed_in_kiosk()

    button = await kiosk.page.wait_for_selector("#visitor-logout",
                                                state="attached", timeout=5000)
    # It has to say something a person with no technical knowledge reads as
    # "leave". A button labelled with a name they never gave says nothing.
    label = (await button.inner_text()).strip()
    assert label in ("خروج از سیستم", "Log out"), label
    assert await button.get_attribute("aria-label")
    assert kiosk.errors == [], kiosk.errors


async def test_signing_out_takes_the_conversation_off_the_screen(
        signed_in_kiosk):
    """DEFECT 4, both halves. The bubbles on screen AND the copy in
    localStorage that loadHistory() replays on the next page load, which at a
    kiosk is the next visitor."""
    kiosk = await signed_in_kiosk()
    page = kiosk.page

    await _say(page, "غرفه رباتیک کجاست؟")
    await _wait_for_text(page, "پاسخ نفر قبلی")
    assert await _bubbles(page), "the question never reached the transcript"
    stored = await page.evaluate("localStorage.getItem('inotex_chat_history')")
    assert stored and "غرفه رباتیک" in stored

    await page.wait_for_selector("#visitor-logout", state="attached")
    await _click(page, "#visitor-logout")

    await page.wait_for_function(
        "() => document.querySelectorAll('#chat-view-content"
        " .message:not(#welcome-message):not(#loading-bubble)').length === 0",
        timeout=5000)
    left_over = await page.evaluate(
        "localStorage.getItem('inotex_chat_history')")
    assert left_over in (None, "", "[]"), left_over
    assert kiosk.hits("/api/auth/logout") == 1, kiosk.calls


async def test_the_chat_still_works_after_signing_out(signed_in_kiosk):
    """The loading bubble is part of the theme's static markup and carries the
    `.message` class. Remove it and the next addMessage() throws
    NotFoundError, so a visitor arriving at a "clean" kiosk finds a chat that
    renders nothing. This is why the selector lives in ONE function."""
    kiosk = await signed_in_kiosk()
    page = kiosk.page

    await _say(page, "ساعت کاری چیست؟")
    await _wait_for_text(page, "پاسخ نفر قبلی")
    await page.wait_for_selector("#visitor-logout", state="attached")
    await _click(page, "#visitor-logout")
    await page.wait_for_function(
        "() => document.querySelectorAll('#chat-view-content"
        " .message:not(#welcome-message):not(#loading-bubble)').length === 0")

    assert await page.evaluate("document.getElementById('loading-bubble') !== null")
    await page.evaluate("addMessage('سلام دوباره', 'bot', false, true)")
    await _wait_for_text(page, "سلام دوباره")
    assert kiosk.errors == [], kiosk.errors


async def test_the_button_goes_away_once_the_server_says_anonymous(
        signed_in_kiosk):
    """The header must agree with the server. A sign-out that leaves the
    button behind reads as "it did not work"."""
    kiosk = await signed_in_kiosk()
    page = kiosk.page

    await page.wait_for_selector("#visitor-logout", state="attached")
    await _click(page, "#visitor-logout")

    await page.wait_for_function(
        "document.documentElement.dataset.visitor === 'out'", timeout=5000)
    assert await page.evaluate(
        "document.getElementById('visitor-logout') === null")
