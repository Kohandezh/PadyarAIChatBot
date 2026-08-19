---
name: playwright-cli
description: Drive a real browser and write Playwright e2e tests in Python (pytest-playwright) against the running PadyarAIChatbot FastAPI app. Covers the standard Playwright CLI (codegen, install), the pytest `page` fixture, locators, assertions, screenshots, and headed/headless runs.
allowed-tools: Bash(playwright:*) Bash(pytest:*) Bash(.venv/bin/python:*) Edit Write Read
---

# Browser Automation with Playwright (Python)

This project is **Python / FastAPI**. There is no custom `playwright-cli` binary here — "playwright-cli" now means the **standard Playwright CLI** (`playwright ...`) plus **pytest-playwright** for tests. You drive a real browser two ways:

1. **`playwright codegen <url>`** — opens a browser, records your clicks/typing, and prints runnable Python code. This is the closest thing to the old record-and-replay workflow.
2. **A Python script** using `sync_playwright()` — for scripted exploration or one-off automation (see [references/running-code.md](references/running-code.md)).

Tests are written as `pytest` functions that receive a `page` fixture from **pytest-playwright**.

## Installation & setup

The test tooling is **already installed** and tracked in **`requirements-dev.txt`** (`pytest`, `pytest-playwright`; kept out of `requirements.txt` so customer installs don't pull in Playwright + browser binaries). Chromium is already downloaded. On a fresh checkout:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium   # download the browser binary
```

(Browsers downloaded by `playwright install` live in a cache, not in the repo — don't commit them.)

## Start the app first

E2E tests hit the running server. Start it in a separate terminal / background:

```bash
.venv/bin/python main.py                 # default http://127.0.0.1:8000
PORT=8010 .venv/bin/python main.py       # HOST/PORT env vars are honored
```

Key URLs in this app:

- Public chat UI: `http://127.0.0.1:8000/`
- Admin login page: `http://127.0.0.1:8000/secure-panel-inotex/login`
- Admin login POST (JSON): `/admin/login`
- Admin pages: dataset, questions, settings, backup (under `/secure-panel-inotex`)

> The public chat endpoint is protected by an HMAC chat token injected into the page, an `Origin`/`Referer` allowlist, and a rate limit (2 requests / 30s per IP). Real-browser e2e works because the loaded page carries a valid token and `localhost` is an allowed origin — but pace requests to avoid the rate limit.

## Record a flow with codegen

```bash
.venv/bin/playwright codegen http://127.0.0.1:8000/secure-panel-inotex/login --target python
```

A browser opens; your interactions are transcribed to Python (`page.goto(...)`, `page.get_by_role(...)`, `page.get_by_label(...).fill(...)`, etc.). Copy the generated body into a `tests/e2e/` test. Use `--output tests/e2e/raw_capture.py` to write straight to a file.

## The `page` fixture (pytest-playwright)

```python
from playwright.sync_api import Page, expect

def test_admin_login_page_loads(page: Page):
    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    expect(page.locator("#username")).to_be_visible()
```

Useful pytest-playwright CLI flags:

```bash
.venv/bin/pytest tests/e2e                       # run e2e tests (headless)
.venv/bin/pytest tests/e2e --headed              # watch the browser
.venv/bin/pytest tests/e2e --headed --slowmo 500 # slow each action by 500ms
.venv/bin/pytest tests/e2e --browser chromium    # also: firefox, webkit
.venv/bin/pytest tests/e2e -k login              # filter by test name
.venv/bin/pytest tests/e2e --base-url http://127.0.0.1:8000  # then page.goto("/...")
```

With `--base-url` set, relative paths work: `page.goto("/secure-panel-inotex/login")`.

## Common locators

Prefer role/label/text locators — they survive refactors better than CSS:

```python
page.get_by_role("button", name="ورود به سیستم")   # Persian button text
page.get_by_label("نام کاربری")
page.get_by_text("داشبورد")
page.get_by_placeholder("رمز عبور")
page.locator("#username")                           # CSS / id when needed
page.get_by_test_id("create-doc-button")            # data-testid (set test_id_attribute if custom)
```

This app's admin login fields have real ids: `#username`, `#password`, `#sec-answer`, and the submit button is the form's submit (text "ورود به سیستم").

## Common assertions

`expect()` auto-waits and retries until the timeout:

```python
expect(page.locator("#username")).to_be_visible()
expect(page).to_have_url(re.compile(r"/secure-panel-inotex"))
expect(page.get_by_text("داشبورد")).to_be_visible()
expect(page.locator("#login-error")).to_have_text("")
expect(page.get_by_role("textbox", name="نام کاربری")).to_have_value("admin")
```

## Screenshots

```python
page.screenshot(path="artifacts/login.png")                # viewport
page.screenshot(path="artifacts/full.png", full_page=True) # whole page
page.locator("#login-form").screenshot(path="artifacts/form.png")  # one element
```

From the CLI you can also capture during a run with `--screenshot=on` (pytest-playwright writes failures to `test-results/`).

## Inspecting attributes not in the snapshot

When you need an `id`, `class`, or `data-*` you can't see, read it from the element (see [references/element-attributes.md](references/element-attributes.md)):

```python
el = page.locator("#login-form button[type=submit]")
print(el.get_attribute("class"))
print(el.evaluate("e => getComputedStyle(e).display"))
```

## Headed vs headless

- **Headless** (default) for CI and fast runs.
- **Headed** (`--headed`) when debugging or recording video. Add `--slowmo` to watch each step.

## Example: admin login form (scripted)

```python
import re
from playwright.sync_api import Page, expect

def test_admin_login_flow(page: Page):
    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")          # security answer
    page.get_by_role("button", name="ورود به سیستم").click()
    expect(page).to_have_url(re.compile(r"/secure-panel-inotex"))
    expect(page.get_by_text("داشبورد")).to_be_visible()
```

## Specific tasks

- **Writing & running pytest-playwright tests** [references/playwright-tests.md](references/playwright-tests.md)
- **Request mocking (`page.route`)** [references/request-mocking.md](references/request-mocking.md)
- **Running custom Playwright code (`sync_playwright`)** [references/running-code.md](references/running-code.md)
- **Browser context isolation & reuse** [references/session-management.md](references/session-management.md)
- **Storage state (cookies, localStorage)** [references/storage-state.md](references/storage-state.md)
- **Test generation from codegen** [references/test-generation.md](references/test-generation.md)
- **Tracing** [references/tracing.md](references/tracing.md)
- **Video recording** [references/video-recording.md](references/video-recording.md)
- **Page Object Model (POM)** [references/page-objects.md](references/page-objects.md)
- **Inspecting element attributes** [references/element-attributes.md](references/element-attributes.md)
