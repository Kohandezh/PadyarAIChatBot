"""F2, output half: markup stored in an answer must never execute in the chat.

`dataset.text` used to be admin-only prose. Since the exhibition lead-capture
flow an outside contact can propose that text, and the reviewer approves it
from a screen that escapes it correctly — so `<img src=x onerror=...>` reads as
ordinary Persian during review and then renders in every visitor's session, on
the same origin as /verify and the admin panel.

This test writes the payload through the ADMIN DATASET EDITOR, not the leads
endpoint. The leads endpoint gained a plain-text rule that rejects markup at
the door; testing through the path that stays open is what proves the output
sanitiser rather than the input rule.

It runs a real uvicorn process and a real browser, because the defect lives in
`element.innerHTML = marked.parse(text)` and no Python assertion can execute
that line. It covers both render paths and every selectable theme:

  * the typewriter path (`typeWriter` re-renders once typing finishes), and
  * the instant path (history replayed from localStorage after a reload).

What it proves: with the payload in the knowledge base, no injected handler
runs, and no <img>, <script> or javascript: link reaches the DOM.
What it does not prove: that no OTHER surface renders `dataset.text` unsafely.
The audit for that is a grep for innerHTML across static/, not this test.

Async Playwright, not the `page` fixture: pytest-playwright's sync API keeps a
running event loop in the main thread for the rest of the session, and every
later test that calls asyncio.run() then dies with "cannot be called from a
running event loop". Measured: 98 unrelated failures in the full suite. The
async API runs inside pytest-asyncio's own loop and leaves nothing behind.
"""
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError, async_playwright

ROOT = Path(__file__).resolve().parents[2]

ADMIN_USER = "e2eadmin"
ADMIN_PASS = "e2e-password-not-a-secret"
ADMIN_SEC_ANSWER = "e2e"

QUESTION = "تست پاکسازی خروجی"

# One string that carries every shape we care about: an executing handler, a
# script tag, a javascript: URL, and the legitimate formatting the product's
# own answers use (heading, list, bold, link). A sanitiser that eats the
# second half is not shippable either, so both are asserted.
PAYLOAD = (
    '<img src=x onerror="window.__xssFired=true">'
    '<script>window.__xssFired=true</script>'
    '[bad](javascript:window.__xssFired=true)\n\n'
    '## عنوان\n\n- یک\n- دو\n\n**پررنگ** [سایت](https://inotex.com/)\n'
)

# Every theme a customer can switch to, paired with the motion preference the
# browser has to report for that theme's own render to be observable.
# "minimal" has no head.html of its own, so it is also what covers
# themes/base/partials/head.html.
#
# The motion column is not cosmetic. Two themes read the media query
# themselves, and they need opposite answers:
#
#   liquid-glass needs "reduce". Its footer paints a full-viewport canvas with
#   `filter: blur(18px)` on every animation frame and runs a sprung
#   border-beam loop alongside it. In headless chromium that starves
#   typeWriter's 20ms setTimeout down to ~235ms per character, so the
#   184-character payload took 43s to finish typing and the 30s wait for the
#   heading expired on every run. "reduce" parks both loops (the theme checks
#   the query itself) and the same typeWriter finishes in 4.4s. The render
#   path is untouched: liquid-glass's addMessageFn has no reduced-motion
#   branch, so the answer still goes typeWriter -> renderMarkdown.
#
#   haj must NOT get "reduce". Its addMessageFn checks HAJ_REDUCED and skips
#   the typewriter outright when it is set. Under "reduce" both halves of this
#   test would take the instant branch, the typewriter path would never run,
#   and the test would stay green while covering half of what it claims.
#
# The anchor itself is the same everywhere: all four themes put the answer's
# markdown in one element, so `.message.bot h2` finds the right container on
# each. No per-theme selector is needed.
SELECTABLE_THEMES = [
    ("inotex", "no-preference"),
    ("liquid-glass", "reduce"),
    ("minimal", "no-preference"),
    ("haj", "no-preference"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """A real uvicorn process on a throwaway SQLite DB.

    A subprocess, not TestClient: the browser has to fetch /chat over HTTP for
    the page's own JS to render the answer.
    """
    tmp = tmp_path_factory.mktemp("e2e")
    port = _free_port()
    env = {
        **os.environ,
        "DB_BACKEND": "sqlite",
        "DB_PATH": str(tmp / "chat.db"),
        "LOGS_DB_PATH": str(tmp / "logs.db"),
        # The bundled INOTEX entries would compete with the probe for the
        # match. An empty knowledge base makes the answer deterministic.
        "SEED_DEFAULT_CONTENT": "false",
        "ADMIN_USERNAME": ADMIN_USER,
        "ADMIN_PASSWORD": ADMIN_PASS,
        "ADMIN_SECURITY_ANSWER": ADMIN_SEC_ANSWER,
        "BCRYPT_ROUNDS": "4",
        # No registration module: its companion card claims the send box, and
        # this test needs the plain chat path.
        "ENABLED_MODULES": "video",
        "OPENAI_API_KEY": "test-dummy-key",
        "COOKIE_SECURE": "false",
        # The default is 20 requests per minute per IP. Seeding polls /chat
        # until the index catches up, and four themes then ask again from the
        # same address, so the production limit would answer 429 and the test
        # would fail for a reason that is not the one it is about.
        "CHAT_RATE_LIMIT": "500",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", "replace")
                pytest.fail(f"server died during startup:\n{out}")
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code < 500:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            pytest.fail("server did not come up within 60s")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def admin(live_server):
    """Logged-in admin client. The payload goes in the way a real admin would."""
    c = httpx.Client(base_url=live_server, timeout=30)
    res = c.post("/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS,
                       "sec_answer": ADMIN_SEC_ANSWER})
    assert res.status_code == 200, res.text
    # Admin mutations carry a session-bound CSRF token (app/auth/csrf.py).
    # The admin panel's fetchAuth() attaches it; here we do it by hand.
    token = c.get("/admin/csrf").json()["csrf_token"]
    c.headers["X-CSRF-Token"] = token
    yield c
    c.close()


@pytest.fixture(scope="module")
def seeded(admin):
    """The probe entry, written through the admin dataset editor's own API."""
    res = admin.post("/admin/api/dataset", json={
        "id": "xss-probe", "title": QUESTION, "text": PAYLOAD, "video_url": "",
    })
    assert res.status_code == 200, res.text
    # A curated question makes the retrieval hit exact, so the answer under
    # test is the probe and not a low-confidence fallback.
    res = admin.post("/admin/api/questions", json={
        "question": QUESTION, "dataset_id": "xss-probe", "video_url": "",
    })
    assert res.status_code == 200, res.text

    # The dataset write reindexes in a background executor, so the entry is in
    # the DB before it is in the search index. The first version of this test
    # raced it and got the "AI unavailable" answer instead of the probe. Poll
    # the real endpoint until the probe is the answer.
    _wait_until_answered(admin.base_url)
    return True


def _wait_until_answered(base: str, timeout: float = 60.0):
    """Ask /chat the probe question until the retrieval index has the entry."""
    page = httpx.get(f"{base}/", timeout=10).text
    token = re.search(r'name="chat-token" content="([^"]+)"', page).group(1)
    headers = {
        "X-Chat-Token": token,
        "Origin": str(base).rstrip("/"),
        # validate_request_origin rejects a short or absent user agent.
        "User-Agent": "pytest-playwright-e2e/1.0",
    }
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        res = httpx.post(f"{base}/chat", json={"message": QUESTION, "lang": "fa"},
                         headers=headers, timeout=30)
        if res.status_code == 200 and "عنوان" in res.json().get("text", ""):
            return
        last = f"{res.status_code} {res.text[:200]}"
        time.sleep(1)
    pytest.fail(f"the probe never became the answer; last response: {last}")


async def _rendered_answer(page):
    """The element the answer was rendered into, whatever the theme calls it.

    Themes name their text layer differently (.bubble, .bubble-text,
    .liquidGlass-text) and wrap it in chrome that legitimately contains its own
    <img> avatar. Anchoring on the heading the payload produced gives the exact
    container under test on every theme.

    On the typewriter path the heading is also the completion signal: typeWriter
    types the raw text as text nodes and only calls renderMarkdown at the end,
    so a visible <h2> means the render under test has already happened.
    """
    heading = await page.wait_for_selector(".message.bot h2", timeout=30_000)
    return (await heading.evaluate_handle("el => el.parentElement")).as_element()


async def _assert_clean(answer, page):
    """Nothing executable survived into the rendered answer."""
    assert await page.evaluate("window.__xssFired === true") is False, \
        "an injected handler ran"
    assert await answer.query_selector("img") is None, "an <img> reached the DOM"
    assert await answer.query_selector("script") is None, "a <script> reached the DOM"
    assert await answer.query_selector("[onerror], [onload], [onclick]") is None, \
        "an inline event handler attribute survived"
    hrefs = await answer.eval_on_selector_all(
        "a", "els => els.map(e => e.getAttribute('href'))")
    assert not [h for h in hrefs if (h or "").strip().lower().startswith("javascript:")], \
        f"a javascript: link survived: {hrefs}"


async def _assert_formatting_survived(answer):
    """The product's own answers use these. Losing them is also a failure."""
    assert await answer.query_selector("h2") is not None, "heading was eaten"
    assert len(await answer.query_selector_all("li")) == 2, "list was eaten"
    assert await answer.query_selector("strong") is not None, "bold was eaten"
    assert await answer.query_selector('a[href="https://inotex.com/"]') is not None, \
        "link was eaten"


@pytest.mark.parametrize("theme,motion", SELECTABLE_THEMES,
                         ids=[name for name, _ in SELECTABLE_THEMES])
async def test_stored_markup_never_executes_in_the_chat(live_server, admin, seeded,
                                                        theme, motion):
    res = admin.post("/admin/api/themes/activate", json={"name": theme})
    assert res.status_code == 200, res.text

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except PlaywrightError as exc:
            # A customer install has no browser binaries. Skipping is honest;
            # failing would say the sanitiser is broken when it is untested.
            pytest.skip(f"chromium not installed (playwright install chromium): {exc}")
        try:
            # See SELECTABLE_THEMES for why the motion preference is per-theme.
            context = await browser.new_context(reduced_motion=motion)
            page = await context.new_page()
            await page.goto(live_server, wait_until="domcontentloaded")
            # The sanitiser is only there if the theme's head.html loads it. A
            # theme that overrides head.html and forgets the line fails here.
            assert await page.evaluate("typeof DOMPurify") == "function", \
                f"theme '{theme}' does not load DOMPurify"

            await page.fill("#user-input", QUESTION)
            await page.click("#send-btn")

            # typeWriter types the raw text as text nodes first and only
            # re-renders as markdown when it finishes. The heading appearing
            # is that moment.
            answer = await _rendered_answer(page)
            await _assert_clean(answer, page)
            await _assert_formatting_survived(answer)

            # Second render path: history replayed from localStorage on load,
            # which skips the typewriter and renders in one shot.
            await page.reload(wait_until="domcontentloaded")
            answer = await _rendered_answer(page)
            await _assert_clean(answer, page)
            await _assert_formatting_survived(answer)
        finally:
            await browser.close()
