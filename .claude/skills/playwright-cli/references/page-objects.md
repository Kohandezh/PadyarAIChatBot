# Browser Test Fixtures

There is no Page Object Model in this repo, and no `data-testid` attributes. Do not build
either one. This file documents the fixture pattern the existing browser tests use.

Working examples to copy from:

- `tests/e2e/test_chat_localisation.py`
- `tests/e2e/test_visitor_session_e2e.py`
- `tests/e2e/test_companion_pet_paints.py`
- `tests/test_kiosk_privacy.py`

## The shape

Three pieces, all inside the test file. No shared `conftest.py` entry for browsers.
`tests/conftest.py` exists and holds the shared non-browser fixtures. Never add a second
top-level `conftest.py`.

1. An **async `browser` fixture** the file defines itself.
2. A **page fixture** that renders the real page through `TestClient` once, then serves
   it and every asset to Chromium from disk through `page.route`.
3. Small `async def _helper(page)` functions for the actions a test repeats.

## 1. The browser fixture

```python
import pytest


@pytest.fixture
async def browser():
    async_playwright = pytest.importorskip(
        "playwright.async_api").async_playwright
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        await b.close()
```

`importorskip` plus the try/except means a machine with no browser skips instead of
failing. That matters because CI and this laptop do not always have the same binaries.

**Never** write `def test_x(page)` or `def test_x(browser)` expecting pytest-playwright to
supply it. Those fixtures are sync and session-scoped, and they break the whole suite.
`tests/test_suite_isolation.py` blocks them with an AST check.

## 2. The page fixture

The page under test is the **real rendered page**, not a hand-written HTML stub. Render
it once through FastAPI's `TestClient`, then answer every browser request yourself.

```python
ORIGIN = "http://padyar.test"   # fake host: nothing resolves it, the route handler answers


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


@pytest.fixture
async def chat_page(browser, tmp_path, monkeypatch):
    html = _rendered_page(str(tmp_path / "ui.db"), monkeypatch)

    async def handle(route, request):
        path = request.url[len(ORIGIN):].split("?")[0] or "/"
        if path == "/":
            return await route.fulfill(status=200, content_type="text/html", body=html)
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

    # A fresh context per test, so what this test stores in localStorage cannot
    # decide how the next test starts.
    context = await browser.new_context()
    page = await context.new_page()
    await page.route(f"{ORIGIN}/**", handle)
    await page.goto(f"{ORIGIN}/")
    await page.wait_for_function("typeof renderOptions === 'function'")
    yield page
    await context.close()
```

Why each part is there:

- **`http://` not `file://`.** `core.js` writes the chosen language to `localStorage`, and
  a `file://` page may refuse storage.
- **A fresh `new_context()` per test.** Cookies and `localStorage` are the kiosk bug in
  miniature. A shared context leaks one test's state into the next.
- **`wait_for_function`** on a real symbol from `core.js`, not a fixed sleep. It proves
  the script is loaded and parsed.
- **Every asset off disk.** No network, no flake, and you test the CSS and JS that are
  actually in the tree.

### Mapping a URL back to a file

`/themes/<name>/...` is a StaticFiles mount pointing at the theme's own `static/` folder,
so that prefix needs rewriting. Everything else under `/static/` maps straight through.
The `relative_to(ROOT)` check stops a `..` in a URL from reading outside the repo.

```python
ROOT = Path(__file__).resolve().parents[2]


def _disk_path(url_path: str):
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
```

## 3. Helpers instead of page objects

Plain module-level async functions. No classes, no inheritance.

```python
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
```

Give each helper a docstring saying **why** it is written that way. The one above records
a real constraint a later reader would otherwise "fix" back into a broken click.

## The test itself

```python
async def test_numbered_options_survive_a_language_switch(chat_page):
    await chat_page.evaluate("renderOptions(...)")
    before = await _option_chips(chat_page)

    await _tap_language_switch(chat_page)
    await chat_page.wait_for_function("document.documentElement.lang === 'en'")

    after = await _option_chips(chat_page)
    assert after == before
```

Note the `wait_for_function` after the action. Wait on a condition, never on a timeout.

## Asserting against Persian text

The UI is Persian by default. Copy the exact string from the template or from the `I18N`
table in `static/chat/core.js`. Do not type it from memory: one wrong character or an
Arabic `ي` instead of a Persian `ی` and the locator never matches.

```python
assert (await button.text_content()).strip() == "گفتگوی جدید"
assert await button.get_attribute("aria-label") == "گفتگوی جدید"
```

For controls that must survive a language switch, target `data-i18n` or the element id,
not the visible text.

## Where to assert from

Drive the page the way a visitor does. `static/companion/registration.js` is an IIFE that
exports nothing on purpose, so its tests type, tap, and watch what reaches the network.
Reaching into module internals would test the implementation instead of the behaviour.
