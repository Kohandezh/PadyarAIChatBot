---
name: e2e-test-gen
description: Generate browser tests for the chat UI or the admin panel by exercising the running app via playwright-cli, then assembling the result into an async pytest file under tests/e2e/. Playwright's ASYNC api is mandatory in this repo and enforced by tests/test_suite_isolation.py.
allowed-tools: Bash(playwright-cli:*) Bash(.venv/bin/python:*) Edit Write Read
---

# Browser test generation for PadyarAIChatbot

When asked to generate a browser test for a flow ("generate an e2e test for
sign-up", "test what happens when the visitor switches language"), follow this
workflow. Explore the real app with the CLI first. Do not try to write the test
from reading `core.js`.

## THE ONE RULE YOU CANNOT BREAK

**Every browser test uses Playwright's ASYNC api, with a `browser` fixture the
test file defines itself.** Never ask for pytest-playwright's `page`,
`browser`, `context`, `browser_context` or `playwright` fixtures.

Why, measured on 2026-08-28:

- `pytest.ini` sets `asyncio_mode = auto`, so every test runs inside an event
  loop.
- Playwright's sync api refuses to start inside a running loop ("Please use the
  Async API instead").
- Worse, pytest-playwright hands its sync driver out through a
  SESSION-scoped fixture, so nothing tears it down between files. It leaves the
  loop running, and every later test that calls `asyncio.run()` then fails too.
- One single sync browser test took `pytest -q` from **15 failures to 141**.
  The first browser test landed under `tests/e2e/`, and pytest collects
  directories before sibling files, so it ran first and poisoned everything
  after it.

`tests/test_suite_isolation.py` is the guard. It parses every file under
`tests/` with an AST check and fails the suite if any test or fixture asks for
one of those names. Defining your OWN fixture with the name `browser` shadows
the plugin's and is the pattern both existing e2e files use.

This is not style. It is the single most expensive mistake available in this
suite.

## Prerequisites

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
```

For the exploration phase, start the dev server:

```bash
.venv/bin/python main.py      # serves http://127.0.0.1:8000
```

There is no separate frontend build and no `localhost:3000`. The chat UI is
vanilla HTML/CSS/JS served by the same FastAPI app. The admin panel is Jinja2
plus Tabler UI (Bootstrap 5, RTL).

## Workflow

### Step 1: Open the app

```bash
playwright-cli open http://127.0.0.1:8000
playwright-cli snapshot
```

The root path redirects to the active theme's chat page. For the admin panel,
open `http://127.0.0.1:8000/secure-panel-admin` and log in through the form,
because the panel is cookie-plus-CSRF protected.

### Step 2: Explore the target flow

```bash
playwright-cli goto http://127.0.0.1:8000/
playwright-cli snapshot
```

Use the snapshot refs (`e5`, `e12`) to find the interactive elements.

### Step 3: Exercise the flow

Run each action through `playwright-cli`. Every command prints a
`Ran Playwright code:` block. Collect those blocks.

```bash
playwright-cli fill e12 "ساعت کاری نمایشگاه چند است؟"
playwright-cli click e15
```

### Step 4: Capture assertions

```bash
playwright-cli --raw generate-locator e20
playwright-cli --raw eval "el => el.textContent" e20
```

### Step 5: Close the browser

```bash
playwright-cli close
```

### Step 6: Translate the recorded code into this repo's test shape

The CLI prints JavaScript-flavoured Playwright code. Rewrite it as async Python
and put it in the harness below. The rewrite is mechanical:

| CLI output | This repo |
|---|---|
| `await page.getByRole('button', {name: 'X'}).click()` | `await page.locator("#the-id").click()` |
| `await page.getByRole('textbox').fill('X')` | `await page.locator("#user-input").fill("X")` |
| `expect(locator).toBeVisible()` | `assert await page.locator("#x").is_visible()` |
| `expect(page).toHaveURL(...)` | `assert page.url.endswith(...)` |

## Selectors

**This repo has no `data-testid` anywhere, and you should not introduce one.**
The chat UI is vanilla HTML and `static/chat/core.js` already drives it by
element id. Those ids ARE the stable contract. Adding a parallel testid system
would give the same element two names.

Prefer, in this order:

1. **The element's existing id.** The base theme partials define them:
   `#user-input`, `#send-btn`, `#mic-btn`, `#new-chat-btn`, `#loading-bubble`,
   `#welcome-message`, `#menu-drawer`, `#menu-toggle`, `#lang-btn`,
   `#chat-suggestions`, `#video-view`, `#avatar-video`, `#text-view`.
2. **A semantic class that core.js itself renders**, for example
   `.questions-list li` for the numbered choice chips.
3. **Add an id to the partial** when the element you need has none. Put it in
   `themes/base/partials/*.html` so every theme inherits it, and name it
   `<feature>-<element>` in kebab-case, matching the ids already there.

**Never select on visible text.** The UI is Persian and switches to English at
the tap of `#lang-btn`. A text selector is a test that breaks the moment
someone edits a label, and the language switch is itself one of the flows under
test.

Be careful with `.click()` on the header controls. The themes lay the header
out inside a fixed frame, so Playwright reports some buttons as outside the
viewport and refuses to click. The existing tests fire the real listener from
inside the page instead:

```python
await page.evaluate("document.getElementById('lang-btn').click()")
```

## The test harness this repo uses

Both existing e2e files (`tests/e2e/test_chat_localisation.py`,
`tests/e2e/test_visitor_session_e2e.py`) share one harness. Copy it.

The page under test is the REAL rendered chat page, produced once through
`TestClient`, then served into Chromium by a `page.route` handler. Every asset
comes off disk and every API call is answered by the handler, so the test
reaches no network and writes no database.

```python
"""What a real browser does when <the flow>.

<Say what broke and why a source-string assertion cannot see it.>

Playwright's ASYNC api, not the `page` fixture pytest-playwright ships. That
fixture is sync, pytest.ini sets `asyncio_mode = auto`, and the sync api
refuses to start inside a running event loop. It also leaves the loop running
on the way out, so every later test that calls `asyncio.run()` fails too:
measured 2026-08-28, that one fixture took `pytest -q` from 15 failures to 141.
See tests/test_suite_isolation.py, which is the guard against it coming back.
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

    # A fresh context per test, so what this test stores in localStorage
    # cannot decide how the next test starts.
    context = await browser.new_context()
    page = await context.new_page()
    await page.route(f"{ORIGIN}/**", handle)
    await page.goto(f"{ORIGIN}/")
    await page.wait_for_function("typeof renderOptions === 'function'")
    yield page
    await context.close()
```

Points that are easy to get wrong:

- `pytest.importorskip("playwright.async_api")` and the `pytest.skip` on launch
  failure. A developer with no browser installed must see a skip, not a red
  run.
- A **fresh context per test**. Kiosk is the threat model here: state left in
  `localStorage` is exactly the bug class these tests exist to catch, so no
  test may inherit another test's storage.
- `await page.wait_for_function(...)` before the first assertion, so the test
  starts after the page's own scripts have booted. Do not sleep.
- The route handler returns 404 for anything it does not know. That makes a
  forgotten stub visible instead of silently hanging.

## Writing the tests

```python
async def test_numbered_options_survive_a_language_switch(chat_page):
    """The visitor asked for AI companies and got five tappable names. Tapping
    EN must translate the interface, not throw the answer away."""
    await chat_page.evaluate(
        "titles => renderOptions(titles.map((t, i) => ({n: i + 1, title: t})))",
        OPTION_TITLES)
    before = await _option_chips(chat_page)
    assert before == ["1. شرکت آلفا", "2. شرکت بتا"], before

    await chat_page.evaluate("document.getElementById('lang-btn').click()")
    await chat_page.wait_for_function("document.documentElement.lang === 'en'")

    after = await _option_chips(chat_page)
    assert after == before, (
        "the numbered choices were destroyed by the language switch")
```

Rules for the generated test:

- `async def test_...`, no `@pytest.mark.asyncio` (asyncio auto mode).
- Plain `assert`, with a message when the failure would be a mystery.
- Wait on a condition with `page.wait_for_function`, never a sleep.
- Assert what the visitor can observe: text content, attributes, which element
  exists, which request went out. The chat modules are IIFEs that export
  nothing on purpose, so drive them the way a visitor does.
- Assert the requests too when the flow is about identity. Intercepting in
  `handle` lets you record what reached `/chat` and check it carries the words
  the visitor typed and nothing about who they are.
- One assertion group per defect. Say in the docstring which defect it holds
  down.

## File naming

```
tests/e2e/test_<flow>.py      # a whole visitor flow
tests/test_<feature>.py       # one browser defect inside an existing feature
```

Both are collected by the default run. That is deliberate: a directory excluded
from the default run is a directory CI never runs, and the bug that made
«گفتگوی جدید» wipe `#loading-bubble` and leave a dead chat screen is invisible
to a source-string assertion. `tests/test_kiosk_privacy.py` is an example of
the second form, with its own async `browser` fixture next to the feature's
other tests.

## Running

```bash
.venv/bin/python -m pytest tests/e2e/test_myflow.py -q
```

Do not run the full suite locally as a gate. This machine has 15 tests that
always fail here (they need a live PostgreSQL or the network) and always pass
on CI. The local pre-commit check is only
`python -m py_compile` on the files you touched.

**CI on GitHub is the pass/fail gate.** `.github/workflows/ci.yml` installs
chromium and runs the browser tests as part of the default `test` job, so a
browser test you add runs on every push and every PR. After pushing:

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

Read `docs/engineering/TESTING.md` as well. It says a passing backend test is
not sufficient evidence that a browser-visible feature works, which is why this
skill exists.
