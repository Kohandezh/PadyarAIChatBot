"""The sign-up gate, in a real browser, now that identity lives on the server.

WHAT THIS FILE HOLDS DOWN
-------------------------
1. A stranger's first message is HELD, not answered, and the sign-up card
   opens over the chat. Nothing they typed is lost.
2. After a successful verification the held message is really delivered. It
   reaches POST /chat, once, carrying the words the visitor actually typed and
   nothing about who they are.
3. localStorage on its own does NOT make the app think anyone is signed in.
   That is the defect this whole change exists for: `isSignedIn()` used to
   mean "localStorage has a name in it", so four seconds in a console let
   anyone past the gate. The answer now comes from GET /api/auth/session,
   which reads an HttpOnly cookie this page cannot read, write or forge.
4. A 401 from /chat reopens sign-up instead of showing an error, which is the
   net under the cases the client gate cannot see (a session that expired
   mid-conversation, a page that loaded before registration was switched on).
5. Logging out asks the SERVER to revoke. Clearing localStorage only ever hid
   a button.

Everything is asserted from OUTSIDE the module. registration.js is an IIFE and
deliberately exports nothing, so these tests drive it the way a visitor does:
type, tap, and watch what reaches the network.

The page under test is the REAL rendered chat page (the inotex theme, which is
the one that loads static/companion/registration.js), served once through
TestClient and loaded into Chromium. Every asset comes off disk and every API
call is fulfilled by the route handler below, so nothing here reaches a
network and no database is written.

Playwright's ASYNC api, not the `page` fixture pytest-playwright ships. That
fixture is sync, pytest.ini sets `asyncio_mode = auto`, and Playwright's sync
api refuses to start inside a running event loop. It also leaves the loop
running on the way out, so every later test that calls `asyncio.run()` fails
too. See tests/test_suite_isolation.py, which is the AST guard against it
coming back, and the header of tests/e2e/test_chat_localisation.py.
"""
import json
import mimetypes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# A fake origin: every request is fulfilled by the route handler, so the host
# never has to exist. http:// rather than file:// because the page writes to
# localStorage, which a file:// page may refuse.
ORIGIN = "http://padyar.test"

HELD_QUESTION = "غرفه هوش مصنوعی کجاست؟"

# What the sign-up card offers. Empty lists are legal and the form degrades to
# free text, but a real list keeps the in-chat questions on their normal path.
OPTIONS_STUB = {
    "jobs": [{"id": "ai", "label": "هوش مصنوعی"}],
    "positions": [{"id": "ceo", "label": "مدیرعامل"}],
    "interests": [{"id": "startup", "label": "استارتاپ"}],
    "flags": [],
}

VERIFIED_PROFILE = {
    "first_name": "سارا",
    "last_name": "محمدی",
    "job": "",
    "position": "",
    "interests": "",
}

# The blob the old code kept: a whole profile plus the challenge id, which was
# treated as the login. A browser that still holds one must not be let in by
# it, and the module must delete it on sight.
LEGACY_BLOB = {
    "first_name": "مهاجم",
    "last_name": "ناشناس",
    "job": "مدیر",
    "position": "مدیرعامل",
    "interests": "استارتاپ",
    "challenge_id": "f" * 32,
}


def _rendered_page(db_path: str, monkeypatch) -> str:
    """The active theme's chat page, exactly as the app serves it."""
    import app.config as config

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/")
    assert r.status_code == 200, r.status_code
    assert "registration.js" in r.text, (
        "the active theme does not load the registration module, so this file "
        "would be testing an empty page")
    return r.text


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


class Server:
    """The stubbed backend, and the record of what the page asked it.

    `signed_in` is mutable on purpose. It is the session row: the page cannot
    reach it, cannot see it, and only learns about it by asking. Verification
    flips it, which is exactly where the real endpoint mints the cookie.
    """

    def __init__(self, signed_in=False):
        self.signed_in = signed_in
        self.chat_bodies = []
        self.chat_status = 200
        self.profile_bodies = []
        self.session_calls = 0
        self.logout_calls = 0

    def session_payload(self):
        if not self.signed_in:
            return {"signed_in": False, "profile": {}}
        return {"signed_in": True, "profile": dict(VERIFIED_PROFILE)}


@pytest.fixture
async def browser():
    async_playwright = pytest.importorskip(
        "playwright.async_api").async_playwright
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 (no browser installed is a skip)
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        await b.close()


@pytest.fixture
async def open_chat(browser, tmp_path, monkeypatch):
    """Open the chat page with the backend stubbed. Returns (page, server).

    A factory and not a plain fixture because two tests need to decide what
    the server will say, or what this browser already holds in localStorage,
    BEFORE the page's own scripts run.
    """
    html = _rendered_page(str(tmp_path / "ui.db"), monkeypatch)
    contexts = []

    async def factory(init_script: str = "", signed_in: bool = False):
        server = Server(signed_in=signed_in)

        async def handle(route, request):
            path = request.url[len(ORIGIN):].split("?")[0] or "/"
            body = {}
            if request.method == "POST":
                try:
                    body = json.loads(request.post_data or "{}")
                except (ValueError, TypeError):
                    body = {}

            if path == "/":
                return await route.fulfill(status=200,
                                           content_type="text/html", body=html)
            if path == "/api/suggestions":
                return await _json(route, [])
            if path == "/api/voice-status":
                return await _json(route, {"voice_enabled": False,
                                           "tts_enabled": False})
            if path == "/api/auth/registration-status":
                return await _json(route, {"enabled": True})
            if path == "/api/registration/options":
                return await _json(route, OPTIONS_STUB)
            if path == "/api/auth/session":
                server.session_calls += 1
                return await _json(route, server.session_payload())
            if path == "/api/auth/logout":
                server.logout_calls += 1
                server.signed_in = False
                return await _json(route, {"ok": True})
            if path == "/api/auth/otp/request":
                return await _json(route, {
                    "challenge_id": "c" * 32,
                    "destination_masked": "0912***4567",
                    "expires_in": 120, "resend_in": 60,
                })
            if path == "/api/auth/otp/verify":
                # Where the real endpoint mints the session and sets the
                # cookie. From here on the server answers "yes, I know you".
                server.signed_in = True
                return await _json(route, {"profile": dict(VERIFIED_PROFILE),
                                           "message": "تأیید شد"})
            if path == "/api/auth/profile":
                server.profile_bodies.append(body)
                return await _json(route, {"profile": dict(VERIFIED_PROFILE)})
            if path == "/chat":
                server.chat_bodies.append(body)
                if server.chat_status == 401:
                    return await route.fulfill(
                        status=401, content_type="application/json",
                        body=json.dumps({"detail": {
                            "code": "registration_required",
                            "message": "برای ادامه لطفاً ثبت‌نام کنید."}}))
                return await _json(route, {"type": "text", "text": "پاسخ",
                                           "video_url": None, "options": []})

            disk = _disk_path(path)
            if disk is not None:
                ctype = (mimetypes.guess_type(disk.name)[0]
                         or "application/octet-stream")
                return await route.fulfill(status=200, content_type=ctype,
                                           body=disk.read_bytes())
            return await route.fulfill(status=404, content_type="text/plain",
                                       body="")

        # A fresh context per page: no cookie and no localStorage may leak
        # from one test into the next, which is the very thing under test.
        context = await browser.new_context()
        contexts.append(context)
        if init_script:
            await context.add_init_script(init_script)
        page = await context.new_page()
        await page.route(f"{ORIGIN}/**", handle)
        await page.goto(f"{ORIGIN}/")
        # The module asks the server who this is before it installs the gate,
        # so a message sent earlier takes the "session unknown" branch, which
        # is not what any of these tests is about. `data-visitor` on <html> is
        # the module's mirror of the server's answer and reads "unknown" until
        # it lands.
        await page.wait_for_function(
            "() => document.documentElement.dataset.visitor "
            "      && document.documentElement.dataset.visitor !== 'unknown'",
            timeout=15000)
        return page, server

    yield factory
    for context in contexts:
        await context.close()


async def _json(route, payload):
    return await route.fulfill(status=200, content_type="application/json",
                               body=json.dumps(payload))


async def _send(page, text):
    """Type a message and press send, through the page's own handlers.

    `.click()` inside the page rather than Playwright's: the inotex theme lays
    the chat out in a fixed frame, so controls report as outside the viewport
    and Playwright refuses to touch them. The listeners under test are the
    same ones. See tests/e2e/test_chat_localisation.py, which hit this first.
    """
    await page.evaluate(
        """text => {
            const input = document.getElementById('user-input');
            input.value = text;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            document.getElementById('send-btn').click();
        }""", text)


async def _click(page, selector):
    await page.evaluate("sel => document.querySelector(sel).click()", selector)


async def _set_value(page, selector, value):
    """Fill a field and tell the page about it, the way typing would."""
    await page.evaluate(
        """args => {
            const node = document.querySelector(args.sel);
            node.value = args.value;
            node.dispatchEvent(new Event('input', { bubbles: true }));
        }""", {"sel": selector, "value": value})


async def _wait_until(page, predicate, what, timeout_ms=15000):
    """Poll a PYTHON-side condition, using the page's clock to wait.

    The interesting conditions here are on the stub server (what reached the
    network), not on the DOM, so `wait_for_function` cannot see them. Asserting
    on the transcript instead would be wrong: the held-message notice and the
    stubbed answer share words, so a text match passes before the send.
    """
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return
        await page.wait_for_timeout(100)
        waited += 100
    raise AssertionError(f"timed out waiting for {what}")


async def _bubbles(page):
    """Every message currently in the transcript, as plain text."""
    return await page.evaluate(
        "() => Array.from(document.querySelectorAll('#chat-view-content .message'))"
        "        .map(m => (m.textContent || '').trim())"
        "        .filter(Boolean)")


async def _finish_signup(page):
    """Name, phone, code, then answer all three in-chat questions.

    All three (job, position, interests) are mandatory now and there is no
    skip button any more, so each one is answered for real through the same
    send path a visitor uses (type + press send).
    """
    await page.wait_for_selector("#reg-name")
    await _set_value(page, "#reg-name", "سارا محمدی")
    await _set_value(page, "#reg-phone", "09120000000")
    await _click(page, ".reg-submit")

    # Six digits in one field is the whole code: the input handler submits by
    # itself once the last one lands, exactly as an autofilled SMS does.
    await page.wait_for_selector(".reg-code-input")
    await _set_value(page, ".reg-code-input", "123456")

    # The card says "verified", then closes on a timer, and the assistant asks
    # its three questions in the chat instead.
    await page.wait_for_function(
        "() => document.querySelector('.reg-overlay') === null")
    for answer in ("هوش مصنوعی", "مدیرعامل", "استارتاپ"):
        await page.wait_for_selector(".reg-ask")
        await _send(page, answer)


# ── The gate holds a stranger's first message ────────────────────────────

async def test_a_strangers_first_message_is_held_and_signup_opens(open_chat):
    """It must not be answered, it must not be thrown away, and the visitor
    must be shown what to do about it."""
    page, server = await open_chat()

    await _send(page, HELD_QUESTION)
    await page.wait_for_selector(".reg-overlay")

    # Nothing was sent. This is why the client gate stays: it is the nicer of
    # the two paths, because the question never leaves the browser at all.
    assert server.chat_bodies == [], server.chat_bodies

    # And the visitor was told why, rather than watching their words vanish.
    said = " ".join(await _bubbles(page))
    assert "ثبت‌نام" in said, said

    # The message box is empty because the module is holding the words, not
    # because it dropped them. The next test proves they come back.
    assert await page.evaluate(
        "() => document.getElementById('user-input').value") == ""


# ── Verification delivers what was held ──────────────────────────────────

async def test_the_held_message_is_delivered_after_verification(open_chat):
    """The visitor asked, then signed up. They must get an answer to the
    question they asked before signing up, not to nothing at all."""
    page, server = await open_chat()

    await _send(page, HELD_QUESTION)
    await page.wait_for_selector(".reg-overlay")
    await _finish_signup(page)

    await _wait_until(page, lambda: len(server.chat_bodies) == 1,
                      "the held message to reach /chat")

    assert len(server.chat_bodies) == 1, server.chat_bodies
    assert server.chat_bodies[0]["message"] == HELD_QUESTION

    # Hole 1 from the report: the visitor's job, position and interests used
    # to ride in this body, self-asserted, and the server believed them. They
    # come from the session now, so nothing about who is asking belongs here.
    assert "visitor" not in server.chat_bodies[0], server.chat_bodies[0]

    # Hole 2: the challenge id used to be re-sent as proof of identity.
    assert server.profile_bodies, "the in-chat answers were never saved"
    for body in server.profile_bodies:
        assert "challenge_id" not in body, body

    # The question appears once. The gate held it before it was ever printed,
    # so a second bubble would mean the delivery echoed it again.
    transcript = await _bubbles(page)
    assert sum(1 for b in transcript if HELD_QUESTION in b) == 1, transcript


# ── localStorage is a label, not a login ─────────────────────────────────

async def test_local_storage_alone_does_not_sign_anyone_in(open_chat):
    """The defect in one test.

    This browser is primed with everything the old code accepted as proof: a
    display name, and the legacy blob with its challenge id in it. The server
    says anonymous. The server wins.
    """
    seed = (
        "localStorage.setItem('padyar-visitor-name', "
        + json.dumps(json.dumps({"first_name": "سارا", "last_name": "محمدی"}))
        + ");\n"
        "localStorage.setItem('inotex-visitor', "
        + json.dumps(json.dumps(LEGACY_BLOB)) + ");"
    )
    page, server = await open_chat(seed)

    assert server.session_calls >= 1, "the page never asked the server who it was"

    # The stale name is dropped once the server has answered, so the header
    # cannot go on showing a visitor who is not there.
    assert await page.evaluate(
        "() => localStorage.getItem('padyar-visitor-name')") is None
    assert await page.evaluate(
        "() => document.getElementById('visitor-logout')") is None

    # The legacy blob is removed on sight. It carried a challenge id that used
    # to work as a password, and a shared booth phone must not keep one.
    assert await page.evaluate(
        "() => localStorage.getItem('inotex-visitor')") is None

    # Now the way the defect was actually reachable: type a name straight into
    # storage AFTER the page has settled. That is what "four seconds in a
    # console" meant, and it must buy exactly nothing.
    await page.evaluate(
        "() => localStorage.setItem('padyar-visitor-name',"
        " JSON.stringify({first_name: 'مهاجم', last_name: 'ناشناس'}))")

    await _send(page, HELD_QUESTION)
    await page.wait_for_selector(".reg-overlay")
    assert server.chat_bodies == [], server.chat_bodies


# ── The server's 401 is a door, not an error ─────────────────────────────

async def test_a_server_401_reopens_signup_and_keeps_the_message(open_chat):
    """The net under what the client gate cannot see: a session that died
    mid-conversation, or a page that loaded before registration was switched
    on. The visitor gets the sign-up card, never a raw error."""
    # Signed in as far as this page knows, so the gate stands down and the
    # message really is sent, which is the situation being tested.
    page, server = await open_chat(signed_in=True)
    server.chat_status = 401

    await _send(page, HELD_QUESTION)
    await page.wait_for_selector(".reg-overlay")

    assert len(server.chat_bodies) == 1, server.chat_bodies

    # The words are still on screen and still held, so signing up delivers
    # them rather than asking the visitor to type them out again.
    said = " ".join(await _bubbles(page))
    assert HELD_QUESTION in said, said
    assert "ثبت‌نام" in said, said

    # And not the generic "the assistant is unavailable" message, which is
    # what a 401 fell through to before this branch existed.
    assert "در دسترس نیست" not in said, said

    # The page corrected itself, so the NEXT message is held locally instead
    # of making the same refused round trip.
    await _click(page, ".reg-close")
    await _send(page, "سؤال دوم")
    assert len(server.chat_bodies) == 1, server.chat_bodies


# ── Logging out is the server's decision ─────────────────────────────────

async def test_logout_asks_the_server_to_revoke(open_chat):
    """Clearing localStorage never ended anything: the session row and its
    cookie lived on, so the next person on a shared booth phone inherited the
    last one's identity. The row has to die."""
    page, server = await open_chat(signed_in=True)
    await page.wait_for_selector("#visitor-logout")

    await _click(page, "#visitor-logout")
    await page.wait_for_function(
        "() => document.getElementById('visitor-logout') === null")

    assert server.logout_calls == 1
    assert server.signed_in is False

    # And the gate is back up: this browser is a stranger again.
    await _send(page, HELD_QUESTION)
    await page.wait_for_selector(".reg-overlay")
    assert server.chat_bodies == [], server.chat_bodies
