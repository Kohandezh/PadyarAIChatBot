"""A booth kiosk is ONE browser shared by strangers all day.

Two controls decide how much of one stranger's visit the NEXT stranger's
session can still reach:

1. `recent_turns()` — the history handed to the selection tier, and from there
   to the AI provider. `chat_logs` is the UNREDACTED store (log_chat writes the
   raw visitor query with no content policy applied), so this is the one place
   where a person's own words leave the machine.
2. The "New chat" button — the only control that fully closes the window by
   forgetting the padyar_conv cookie.

Both had the same failure shape: they behaved as if the browser belonged to one
person. The history window followed the sliding 24h cookie instead of a
conversation, and the button reported success without reading the HTTP status.

The browser half of this file then found three more, all of them in the same
handler and none of them visible to a source-string assertion:

- THE RESET BRICKED THE CHAT. The handler removed EVERY `.message` in the
  transcript. `#welcome-message` and `#loading-bubble` are part of the theme's
  static markup and carry that class, and `addMessage()` inserts before a
  `#loading-bubble` captured once at init — so the very next `addMessage()`
  threw NotFoundError and nothing rendered again until a page reload.
- THE BROWSER'S OWN COPY SURVIVED. The handler forgot the padyar_conv cookie
  but not `localStorage['inotex_chat_history']`, so `loadHistory()` replayed
  the previous visitor's typed messages to the next visitor on the next page
  load. The cookie is the server's copy of the transcript; that key is this
  browser's, and both have to go.
- THE OFFERED CHOICES SANK. `renderOptions()` appended its numbered choices
  instead of inserting them before the loading bubble, so a list of companies
  ended up under every later answer and stayed tappable there, still numbered
  from 1.

THE FIXTURE. These browser tests load the REAL rendered chat page (the active
theme, served once through TestClient) into Chromium, with every asset served
off disk and every server call answered by the route handler below. The
hand-written fixture HTML this file used to carry had neither
`#welcome-message` nor `#loading-bubble`, and the first of those three defects
is entirely about those two elements — so the tests stayed green all the way
through a broken release. Fixture markup that is a simplified version of the
real page can only test the simplification.
"""
import json
import mimetypes
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CORE_JS = ROOT / "static" / "chat" / "core.js"


# ── Defect 9: the history window ─────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite chat_logs, without booting the whole app."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "kiosk.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db, get_db_connection
    init_db()
    return get_db_connection


def _insert(conn, conversation_id, query, response, age_minutes=0):
    conn.execute(
        "INSERT INTO chat_logs (query, response, response_type, source,"
        " confidence, tokens, cost, conversation_id, entry_id, offer_state,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,"
        f" datetime('now','-{int(age_minutes)} minutes'))",
        (query, response, "text", "local", 0.9, 0, 0.0,
         conversation_id, "", ""))


def test_history_stops_at_the_conversation_not_at_the_cookie(db):
    """The padyar_conv cookie is re-set with a fresh max_age on EVERY answered
    turn (app/routers/chat.py), so at a busy booth it never expires and one
    conversation_id covers everybody who touches the kiosk that day.

    A 24h history window therefore handed the previous visitor's RAW messages
    to the AI provider on the next visitor's first question — including
    messages that a LOCAL tier had answered and that had never left the box.
    The window has to be a CONVERSATION's length, not the cookie's.
    """
    conn = db()
    _insert(conn, "conv-kiosk", "پرسش نفر قبلی", "پاسخ", age_minutes=200)
    _insert(conn, "conv-kiosk", "پرسش نفر تازه", "پاسخ")
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns
    queries = [t["query"] for t in recent_turns("conv-kiosk", limit=5)]
    assert queries == ["پرسش نفر تازه"], queries


def test_history_keeps_the_turns_of_one_real_conversation(db):
    """The window must not be so tight that it breaks the feature it feeds.
    A visitor who watches a video and then asks "و دومی؟" is still the same
    person, and their own previous turns must still be there.
    """
    conn = db()
    _insert(conn, "conv-kiosk", "پرسش اول", "پاسخ اول", age_minutes=8)
    _insert(conn, "conv-kiosk", "پرسش دوم", "پاسخ دوم", age_minutes=2)
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns
    queries = sorted(t["query"] for t in recent_turns("conv-kiosk", limit=5))
    assert queries == ["پرسش اول", "پرسش دوم"], queries


def test_history_window_is_not_longer_than_the_pick_window():
    """The same diff bounds the OFFER state at PICK_WINDOW_MINUTES for exactly
    this threat ("a bare 3 typed twenty minutes after somebody else's list").
    An offer is a list of public dataset ids; the history is the visitor's own
    words. The harmful artifact must not outlive the harmless one.
    """
    from app.config import HISTORY_WINDOW_MINUTES, PICK_WINDOW_MINUTES
    assert HISTORY_WINDOW_MINUTES <= PICK_WINDOW_MINUTES, (
        HISTORY_WINDOW_MINUTES, PICK_WINDOW_MINUTES)


# ── Defect 10: the "New chat" button ─────────────────────────────────────

def test_new_conversation_rejects_an_expired_token(tmp_path, monkeypatch):
    """The contract the browser has to respect: /api/chat/new-conversation
    validates the signed chat token with NO grace, so a kiosk page left open
    past CHAT_TOKEN_TTL (1h, the normal state at an exhibition) gets a 403 and
    the padyar_conv cookie is NOT deleted. `fetch` does not reject on 403, so
    a handler that only try/catches network errors cannot see this.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "newconv.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        token = security.generate_chat_token()
        # A negative TTL is "minted before the TTL window": the same state a
        # kiosk page open for more than CHAT_TOKEN_TTL is in.
        monkeypatch.setattr(security, "CHAT_TOKEN_TTL", -1)
        r = c.post("/api/chat/new-conversation",
                   headers={"Origin": "http://localhost", "X-Chat-Token": token},
                   json={})
    assert r.status_code == 403, r.text
    assert "padyar_conv" not in r.headers.get("set-cookie", "")


def test_new_conversation_succeeds_with_a_refreshed_token(tmp_path, monkeypatch):
    """The recovery path the button now uses: POST /api/chat-token accepts the
    expired token within CHAT_TOKEN_REFRESH_GRACE and mints a fresh one, and
    that fresh token clears padyar_conv. This is the same two-step the /chat
    403 retry already takes — the button reuses it instead of inventing one.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "newconv2.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    from fastapi.testclient import TestClient

    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        head = {"Origin": "http://localhost"}
        old = security.generate_chat_token()
        monkeypatch.setattr(security, "CHAT_TOKEN_TTL", -1)   # expired
        r = c.post("/api/chat-token", headers=dict(head, **{"X-Chat-Token": old}))
        assert r.status_code == 200, r.text
        fresh = r.json()["token"]

        monkeypatch.setattr(security, "CHAT_TOKEN_TTL", 3600)  # the fresh mint
        r2 = c.post("/api/chat/new-conversation",
                    headers=dict(head, **{"X-Chat-Token": fresh}), json={})
    security._chat_rate_limits.clear()

    assert r2.status_code == 200, r2.text
    cookie = r2.headers.get("set-cookie", "")
    assert "padyar_conv=" in cookie and "Max-Age=0" in cookie, cookie


# ── The real page, in the browser that actually runs the handler ─────────
#
# Playwright's ASYNC api, and a `browser` fixture defined HERE.
#
# pytest.ini sets `asyncio_mode = auto`, so the suite always has a running
# event loop and Playwright's sync api refuses to start inside one ("Please
# use the Async API instead"). It also leaves that loop running on the way
# out, so every later test that calls `asyncio.run()` fails too: measured
# 2026-08-28, one sync browser fixture took `pytest -q` from 15 failures to
# 141. tests/test_suite_isolation.py is the guard that keeps that from coming
# back, and it bans this file from ASKING for pytest-playwright's `page` /
# `browser` / `context` fixtures. Defining our own `browser` shadows the
# plugin's, and the async driver stops with the test that started it.

# A fake origin: every request is fulfilled by the route handler below, so the
# host never has to exist. http:// (not file://) because core.js writes the
# chosen language and the transcript to localStorage, which a file:// page may
# not have.
ORIGIN = "http://padyar.test"

# The token the stubbed /api/chat-token mints. The page loads with a REAL
# signed token in its meta tag; the stub treats anything that is not this
# value as expired, which is the server's own rule with the clock moved on.
FRESH_TOKEN = "FRESH"

# Two entries, so the FAQ block the handler shows after a successful reset is
# a REAL list. With an empty dataset it renders a plain "no questions" message
# instead and a test could pass for the wrong reason.
DATASET_STUB = [
    {"id": "faq-hours", "title": "ساعت کاری", "title_en": "Opening hours",
     "video_url": ""},
    {"id": "faq-venue", "title": "محل برگزاری", "title_en": "Venue",
     "video_url": ""},
]

PLAIN_ANSWER = {"type": "text", "text": "پاسخ نخست"}

_PAGE_HTML = None


def _rendered_page(db_path, monkeypatch) -> str:
    """The active theme's chat page, exactly as the app serves it.

    Rendered once for the whole module: booting the app through TestClient
    costs about a second and the markup does not depend on which test asked
    for it.
    """
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
    """Map a URL path onto a file in the repository, or None.

    `/themes/<name>/...` is a StaticFiles mount whose directory is the theme's
    own `static/` folder, so that prefix is rewritten; everything else under
    `/static/` maps straight through.
    """
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
    """One open kiosk page, plus everything the stubbed server saw.

    `calls` is a list of (path, chat token) in request order, so a test can
    assert WHICH token each attempt carried without the page having to record
    anything for it. `errors` collects uncaught page exceptions: defect 3
    surfaced as a NotFoundError thrown out of the click handler, and a test
    that only looks at the DOM would call that "the bubble is missing" instead
    of "the handler crashed".
    """

    def __init__(self, page, calls, errors):
        self.page = page
        self.calls = calls
        self.errors = errors

    def tokens_sent_to(self, path):
        return [token for seen, token in self.calls if seen == path]

    def hits(self, path):
        return len(self.tokens_sent_to(path))


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
async def open_kiosk(browser, tmp_path, monkeypatch):
    """Open the real chat page with every server call answered locally.

    Returns a factory rather than a page, because two of these tests need a
    kiosk whose token refresh FAILS and one needs two kiosks in a row. Each
    call gets its own browser context, so one test's localStorage (the
    transcript, the chosen language) can never reach the next.
    """
    html = _rendered_page(str(tmp_path / "kiosk.db"), monkeypatch)
    contexts = []

    async def factory(*, refresh_works=True, replies=None):
        pending = list(replies or [PLAIN_ANSWER])
        calls = []
        errors = []

        async def handle(route, request):
            path = request.url[len(ORIGIN):].split("?")[0] or "/"
            token = request.headers.get("x-chat-token", "")
            calls.append((path, token))

            def send(body, status=200):
                return route.fulfill(status=status,
                                     content_type="application/json",
                                     body=json.dumps(body))

            if path == "/":
                return await route.fulfill(status=200,
                                           content_type="text/html", body=html)
            if path == "/api/dataset":
                return await send(DATASET_STUB)
            if path == "/api/questions":
                return await send([])
            if path == "/api/voice-status":
                return await send({"voice_enabled": False, "tts_enabled": False})
            if path == "/api/auth/registration-status":
                # The registration module's send gate would swallow the message
                # before core.js ever renders it. Off, as on a plain install.
                return await send({"enabled": False})
            if path == "/chat":
                # One reply per turn, in order; the last one repeats, so a
                # test that only cares about the first turn passes one.
                return await send(pending.pop(0) if len(pending) > 1
                                  else pending[0])
            if path == "/api/chat-token":
                return await (send({"token": FRESH_TOKEN}) if refresh_works
                              else send({"detail": "no"}, status=403))
            if path == "/api/chat/new-conversation":
                # Exactly the server's rule: only an unexpired token is taken.
                return await (send({"ok": True}) if token == FRESH_TOKEN
                              else send({"detail": "no"}, status=403))

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
        return Kiosk(page, calls, errors)

    yield factory
    for context in contexts:
        await context.close()


async def _click(page, selector):
    """Fire the element's own click handler from inside the page.

    Playwright's `.click()` first checks the element is in the viewport, and
    the themes lay the header out inside a fixed frame that makes these
    controls report as outside it. The listener under test is the same one.
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


async def _settle(kiosk):
    """Wait for the button's request, then wait until the screen stops moving.

    NOT `wait_for_function` on the text a working handler produces. Every one
    of these defects ends with the visitor looking at the wrong screen, and a
    regression should print WHAT is on that screen — waiting for a string that
    a broken handler never writes turns each of them into a 30 second timeout
    with nothing in it. So: wait for the /api/chat/new-conversation call that
    the broken and the fixed handler both make, then read the transcript until
    three reads in a row agree.

    Quiescence rather than a fixed sleep because the bot bubbles are typed one
    character at a time (20ms each, via setTimeout), and a loaded machine
    stretches that. A handler that renders nothing goes quiet immediately, so
    a failure still lands in well under a second.
    """
    deadline = time.monotonic() + 15
    while (not kiosk.hits("/api/chat/new-conversation")
           and time.monotonic() < deadline):
        await kiosk.page.wait_for_timeout(25)
    await kiosk.page.wait_for_timeout(250)
    previous, unchanged = None, 0
    while unchanged < 3 and time.monotonic() < deadline:
        await kiosk.page.wait_for_timeout(120)
        current = await _transcript(kiosk.page)
        unchanged = unchanged + 1 if current == previous else 0
        previous = current


async def _transcript(page):
    return await page.evaluate(
        "document.getElementById('chat-view-content').innerText")


async def _transcript_children(page):
    """Every direct child of the transcript, in document order.

    Each row is "id-or-class | first words", which is enough to say WHERE a
    block landed and readable enough to print in a failure message.
    """
    return await page.evaluate(
        "() => Array.from(document.getElementById('chat-view-content').children)"
        "        .map(el => (el.id || el.className) + ' | '"
        "                   + el.innerText.trim().replace(/\\s+/g, ' ').slice(0, 30))")


def _row(children, needle):
    """The index of the one transcript child that contains `needle`."""
    hits = [i for i, row in enumerate(children) if needle in row]
    assert len(hits) == 1, (needle, children)
    return hits[0]


async def test_new_chat_button_recovers_from_an_expired_token(open_kiosk):
    """A kiosk page open since 09:00 has an expired token by 10:00. Pressing
    "New chat" must still forget the conversation: refresh the token the way
    the /chat 403 retry already does, then repeat the request.
    """
    kiosk = await open_kiosk(refresh_works=True)
    page_token = await kiosk.page.evaluate(
        "document.querySelector('meta[name=\"chat-token\"]').content")

    await _say(kiosk.page, "پرسش نفر قبلی")
    await _wait_for_text(kiosk.page, "پرسش نفر قبلی")

    await _click(kiosk.page, "#new-chat-btn")
    await _settle(kiosk)

    assert kiosk.tokens_sent_to("/api/chat/new-conversation") == [
        page_token, FRESH_TOKEN], kiosk.calls
    assert kiosk.hits("/api/chat-token") == 1, kiosk.calls

    text = await _transcript(kiosk.page)
    assert "گفتگوی تازه شروع شد" in text, text
    assert "پرسش نفر قبلی" not in text, text     # the previous visitor is gone


async def test_new_chat_button_never_claims_a_chat_it_did_not_clear(open_kiosk):
    """When the server refuses and the token refresh cannot save it, the
    conversation is still on the server. Wiping the screen and printing
    «گفتگوی تازه شروع شد.» would be a lie the visitor cannot detect — and this
    is the ONLY control that closes the shared-kiosk window.
    """
    kiosk = await open_kiosk(refresh_works=False)
    await _say(kiosk.page, "پرسش نفر قبلی")
    await _wait_for_text(kiosk.page, "پرسش نفر قبلی")

    await _click(kiosk.page, "#new-chat-btn")
    await _settle(kiosk)

    text = await _transcript(kiosk.page)
    assert "گفتگوی تازه شروع شد" not in text, text
    assert "پرسش نفر قبلی" in text, text      # the old chat is still on screen
    assert "الان نشد" in text, text           # and the visitor is told plainly


async def test_new_chat_leaves_a_working_chat_behind(open_kiosk):
    """The reset that bricked the chat — the worst of the three.

    The handler used to remove every `.message` in the transcript.
    `#welcome-message` and `#loading-bubble` are part of the theme's static
    markup and carry that class, and `addMessage()` inserts before a
    `#loading-bubble` captured once at init — so removing it made the very
    next `addMessage()` throw NotFoundError inside the handler. What the
    visitor saw: a blank pane, no confirmation that anything happened, and
    then every message they typed disappearing without a trace, until someone
    reloaded the page. At a booth nobody reloads the page.

    So: the confirmation has to arrive, the two structural bubbles have to
    survive, the NEXT question has to answer, and nothing may be thrown.
    """
    kiosk = await open_kiosk(replies=[PLAIN_ANSWER, {"type": "text",
                                                     "text": "پاسخ دوم"}])
    await _say(kiosk.page, "پرسش نفر قبلی")
    await _wait_for_text(kiosk.page, "پاسخ نخست")

    await _click(kiosk.page, "#new-chat-btn")
    await _settle(kiosk)

    # The exception first: it is thrown out of the click handler and it names
    # the cause ("insertBefore ... is not a child of this node"), where the
    # DOM assertions below can only report the symptom.
    assert kiosk.errors == [], kiosk.errors

    children = await _transcript_children(kiosk.page)
    assert any("گفتگوی تازه شروع شد" in row for row in children), children
    assert any(row.startswith("welcome-message") for row in children), children
    assert any(row.startswith("loading-bubble") for row in children), children

    await _say(kiosk.page, "پرسش نفر تازه")
    await _wait_for_text(kiosk.page, "پاسخ دوم")

    assert kiosk.errors == [], kiosk.errors


async def test_new_chat_forgets_the_transcript_this_browser_kept(open_kiosk):
    """The transcript is stored in TWO places, and the handler forgot only one
    of them.

    The padyar_conv cookie is the server's copy. `localStorage`'s
    `inotex_chat_history` is the browser's, and `loadHistory()` replays it on
    the next page load — which at a booth is the next visitor, reading the
    previous stranger's typed questions on a screen that says it is a new
    chat. The whole round trip, because that is where the leak happens: type,
    reset, reload.
    """
    kiosk = await open_kiosk()
    await _say(kiosk.page, "پرسش خصوصی نفر اول")
    await _wait_for_text(kiosk.page, "پاسخ نخست")

    await _click(kiosk.page, "#new-chat-btn")
    await _settle(kiosk)
    assert "گفتگوی تازه شروع شد" in await _transcript(kiosk.page)

    await kiosk.page.reload()
    await kiosk.page.wait_for_function("typeof initChat === 'function'")
    # The confirmation is written AFTER the key is cleared, so it is the one
    # line a correct reset leaves behind. Waiting for it means loadHistory()
    # has finished replaying whatever it found.
    await _wait_for_text(kiosk.page, "گفتگوی تازه شروع شد")

    text = await _transcript(kiosk.page)
    assert "پرسش خصوصی نفر اول" not in text, text
    assert "پاسخ نخست" not in text, text

    stored = await kiosk.page.evaluate(
        "localStorage.getItem('inotex_chat_history') || ''")
    assert "پرسش خصوصی نفر اول" not in stored, stored


async def test_option_chips_stay_with_the_answer_that_offered_them(open_kiosk):
    """`renderOptions()` used `appendChild` while every other message uses
    `insertBefore(msgDiv, loadingBubble)`.

    `#loading-bubble` is the last child of the transcript and everything is
    inserted in front of it, so appending put the numbered choices BELOW it —
    the bottom of the transcript, permanently. Ask a second question and its
    answer lands above the first question's chips, so the visitor reads answer
    two and then five still-tappable, still-numbered-from-1 company names
    belonging to a question they had already left behind. Tapping "3" there
    sends the wrong company.
    """
    kiosk = await open_kiosk(replies=[
        {"type": "text", "text": "پاسخ نخست",
         "options": [{"n": 1, "title": "شرکت آلفا"},
                     {"n": 2, "title": "شرکت بتا"}]},
        {"type": "text", "text": "پاسخ دوم"},
    ])
    await _say(kiosk.page, "کدام شرکت‌ها هوش مصنوعی دارند؟")
    await _wait_for_text(kiosk.page, "شرکت آلفا")

    await _say(kiosk.page, "ساعت کاری چیست؟")
    await _wait_for_text(kiosk.page, "پاسخ دوم")

    children = await _transcript_children(kiosk.page)
    assert children[-1].startswith("loading-bubble"), children
    assert _row(children, "شرکت آلفا") < _row(children, "پاسخ دوم"), children


async def test_the_new_chat_messages_speak_the_visitors_language(open_kiosk):
    """The smallest of them. The button's own label is localised through
    `data-i18n`, but the two messages the handler WRITES are built at click
    time from `t()`. An English visitor who resets the chat must be answered
    in English, whether it worked or not.
    """
    kiosk = await open_kiosk(refresh_works=True)
    await _click(kiosk.page, "#lang-btn")
    await kiosk.page.wait_for_function("document.documentElement.lang === 'en'")
    await _click(kiosk.page, "#new-chat-btn")
    await _settle(kiosk)
    assert "Started a new chat." in await _transcript(kiosk.page)

    refused = await open_kiosk(refresh_works=False)
    await _click(refused.page, "#lang-btn")
    await refused.page.wait_for_function(
        "document.documentElement.lang === 'en'")
    await _click(refused.page, "#new-chat-btn")
    await _settle(refused)
    assert "That didn't work." in await _transcript(refused.page)


# ── The strings, at the source ───────────────────────────────────────────

def test_the_failure_message_exists_in_both_languages():
    """No jargon, no status codes: the visitor is told it did not work and what
    to do. CLAUDE.md's grandmother test applies to every visitor-facing string.
    """
    js = CORE_JS.read_text(encoding="utf-8")
    assert js.count("newChatFailed:") == 2, "one fa string and one en string"


@pytest.mark.parametrize("theme", ["base", "inotex", "liquid-glass", "haj"])
def test_every_theme_header_localises_the_new_chat_button(theme):
    """The button is markup, repeated in four theme headers, and `setLang()`
    only translates what carries `data-i18n` / `data-i18n-title`. A theme that
    ships the button with its hardcoded Persian label still passes every
    browser test run against the DEFAULT theme, and the customer running that
    theme gets a Persian word in an English interface.

    Both attributes, because they feed different things: `data-i18n` sets the
    visible label and `data-i18n-title` sets `title` AND `aria-label`, which
    is what an English screen reader reads out.
    """
    header = (ROOT / "themes" / theme / "partials" / "header.html").read_text(
        encoding="utf-8")
    assert 'id="new-chat-btn"' in header, theme
    button = header[header.index('id="new-chat-btn"'):]
    button = button[:button.index(">")]
    assert 'data-i18n="newChat"' in button, button
    assert 'data-i18n-title="newChat"' in button, button
