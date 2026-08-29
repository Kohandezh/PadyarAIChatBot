"""What a real browser does to the chat UI when the visitor switches language.

Both defects here are DOM behaviour, so a source-string assertion cannot see
them. The page under test is the REAL rendered chat page (the active theme,
served once through TestClient) loaded into Chromium, with every asset served
off disk and the two data endpoints stubbed. Nothing here reaches a network.

WHAT WAS BROKEN:

  1. `renderOptions()` marked its numbered choices with the same
     `.questions-msg` class the FAQ block uses, and `rebuildQuestionsIfVisible()`
     grabs the FIRST `.questions-msg` in the document and REMOVES it. Nothing
     calls `showQuestions()` on page load, so on a fresh session the options
     block IS the first one: the visitor asked for AI companies, got five
     tappable names, tapped EN, and the list was destroyed.

  2. The new-chat button's label, `title` and `aria-label` were hardcoded
     Persian in all four theme headers. An English visitor read Persian, and
     an English screen reader announced Persian.

Playwright's ASYNC api, not the `page` fixture pytest-playwright ships. That
fixture is sync, pytest.ini sets `asyncio_mode = auto`, and Playwright's sync
api refuses to start inside a running event loop ("Please use the Async API
instead"). It also leaves the loop running on the way out, so every later test
in the suite that calls `asyncio.run()` fails too: measured 2026-08-28, that
one fixture took `pytest -q` from 15 failures to 141. See
tests/test_suite_isolation.py, which is the guard against it coming back.
"""
import json
import mimetypes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# A fake origin: every request is fulfilled by the route handler below, so the
# host never has to exist. http:// (not file://) because core.js writes the
# chosen language to localStorage, which a file:// page may not have.
ORIGIN = "http://padyar.test"

# Two suggested questions, so `showQuestions()` renders a REAL FAQ block. With
# an empty list it renders a plain message instead and the test could pass for
# the wrong reason.
# Titles only: /api/suggestions serves the chip labels and nothing else, so a
# stub that still carried ids, bodies or video paths would let the page pass a
# test against data the real server no longer sends.
SUGGESTIONS_STUB = [
    {"title": "ساعت کاری", "title_en": "Opening hours"},
    {"title": "محل برگزاری", "title_en": "Venue"},
]

OPTION_TITLES = ["شرکت آلفا", "شرکت بتا"]


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
async def chat_page(browser, tmp_path, monkeypatch):
    html = _rendered_page(str(tmp_path / "ui.db"), monkeypatch)

    async def handle(route, request):
        path = request.url[len(ORIGIN):].split("?")[0] or "/"
        if path == "/":
            return await route.fulfill(status=200, content_type="text/html",
                                       body=html)
        if path == "/api/suggestions":
            return await route.fulfill(status=200,
                                       content_type="application/json",
                                       body=json.dumps(SUGGESTIONS_STUB))
        disk = _disk_path(path)
        if disk is not None:
            ctype = mimetypes.guess_type(disk.name)[0] or "application/octet-stream"
            return await route.fulfill(status=200, content_type=ctype,
                                       body=disk.read_bytes())
        return await route.fulfill(status=404, content_type="text/plain", body="")

    # A fresh context per test, so the language this test stores in
    # localStorage cannot decide which language the next test starts in.
    context = await browser.new_context()
    page = await context.new_page()
    await page.route(f"{ORIGIN}/**", handle)
    await page.goto(f"{ORIGIN}/")
    await page.wait_for_function("typeof renderOptions === 'function'")
    yield page
    await context.close()


async def _tap_language_switch(page):
    """Fire the real click handler on the EN/FA button.

    `.click()` in the page rather than Playwright's: the theme lays the header
    out inside a fixed frame, so the button reports as outside the viewport and
    Playwright refuses to click it. The listener under test is the same one.
    """
    await page.evaluate("document.getElementById('lang-btn').click()")


async def _option_chips(page):
    """The numbered choice chips currently in the transcript."""
    return await page.evaluate(
        "() => Array.from(document.querySelectorAll('.questions-list li'))"
        "        .map(li => li.textContent)")


# ── Defect 12: switching language must not delete the offered choices ────

async def test_numbered_options_survive_a_language_switch(chat_page):
    """The visitor asked for AI companies and got five tappable names. Tapping
    EN must translate the interface, not throw the answer away."""
    await chat_page.evaluate(
        "titles => renderOptions(titles.map((t, i) => ({n: i + 1, title: t})))",
        OPTION_TITLES)
    before = await _option_chips(chat_page)
    assert before == ["1. شرکت آلفا", "2. شرکت بتا"], before

    await _tap_language_switch(chat_page)
    await chat_page.wait_for_function("document.documentElement.lang === 'en'")

    after = await _option_chips(chat_page)
    assert after == before, (
        "the numbered choices were destroyed by the language switch")


async def test_the_faq_block_is_still_rebuilt_in_the_new_language(chat_page):
    """The other half of the same selector: the FAQ list must keep switching
    language. Skipping the options block must not skip this one too."""
    await chat_page.evaluate("showQuestions()")
    await chat_page.wait_for_function(
        "document.querySelectorAll('.questions-list li').length > 0")
    assert await _option_chips(chat_page) == ["ساعت کاری", "محل برگزاری"]

    await _tap_language_switch(chat_page)
    await chat_page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.questions-list li'))"
        "        .some(li => li.textContent === 'Opening hours')")
    assert await _option_chips(chat_page) == ["Opening hours", "Venue"]


# ── Defect 13: the new-chat button speaks the visitor's language ─────────

async def test_the_new_chat_button_is_localised_like_every_other_control(chat_page):
    """An English screen reader must not announce a Persian label.

    Phase 3 (docs/features/hamburger-menu/SPEC.md) moved this into the
    sidebar as a labelled row — its text content is now the visible label,
    not empty, and both that text and title/aria-label must localize
    together.
    """
    button = chat_page.locator("#new-chat-btn")
    assert (await button.text_content()).strip() == "گفتگوی جدید"
    assert await button.get_attribute("title") == "گفتگوی جدید"
    assert await button.get_attribute("aria-label") == "گفتگوی جدید"

    await _tap_language_switch(chat_page)
    await chat_page.wait_for_function("document.documentElement.lang === 'en'")

    assert (await button.text_content()).strip() == "New chat"
    assert await button.get_attribute("title") == "New chat"
    assert await button.get_attribute("aria-label") == "New chat"
