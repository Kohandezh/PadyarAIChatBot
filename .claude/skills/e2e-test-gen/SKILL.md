---
name: e2e-test-gen
description: Generate Python Playwright (pytest-playwright) e2e tests for the PadyarAIChatbot by running the FastAPI app, exercising a flow in a real browser (codegen or a scripted sync_playwright session), then assembling the captured code into test files under tests/e2e/.
allowed-tools: Bash(playwright:*) Bash(pytest:*) Bash(.venv/bin/python:*) Edit Write Read
---

# E2E Test Generation (Python / pytest-playwright)

When asked to generate an e2e test for a flow (e.g. "generate an e2e test for admin login" or "for the chat happy path"), follow this workflow. Don't read template/JS source just to write tests — run the **real app** in a browser and capture the Playwright Python code, then stabilize and assemble it.

This project is Python/FastAPI. There is no `playwright-cli` binary — use the standard `playwright` CLI (`codegen`) plus **pytest-playwright**.

## Prerequisites

The tooling is **already installed** (`pytest`, `pytest-playwright` in `requirements-dev.txt`) and Chromium is downloaded. On a fresh checkout:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
```

Run the app (it must be reachable while you explore and while tests run):

```bash
.venv/bin/python main.py            # http://127.0.0.1:8000
# or pin a port: PORT=8010 .venv/bin/python main.py
```

## Workflow

### Step 1: Record the flow in a real browser

Use `codegen` against the running app. It opens a browser and transcribes your actions to Python:

```bash
.venv/bin/playwright codegen http://127.0.0.1:8000/secure-panel-inotex/login \
  --target python --output tests/e2e/_capture_login.py
```

Click/type through the flow; close the browser to finish. The output file holds the raw `page.*` calls. (For scripted exploration instead of clicking, see [../playwright-cli/references/running-code.md](../playwright-cli/references/running-code.md).)

### Step 2: Review the captured code

codegen prefers role/label/placeholder locators, e.g.:

```python
page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
page.get_by_placeholder("نام کاربری").fill("admin")
page.get_by_placeholder("رمز عبور").fill("admin")
page.get_by_placeholder("رنگ مورد علاقه؟").fill("آبی")
page.get_by_role("button", name="ورود به سیستم").click()
```

### Step 3: Stabilize selectors

Replace fragile selectors with stable ones, in this order of preference:

1. **Stable ids this app already exposes** — the admin login form has `#username`, `#password`, `#sec-answer`. Prefer these over placeholder text (placeholders are Persian UI copy that may change).
2. **`data-testid`** — if you add test ids to a template, use `page.get_by_test_id(...)`. To enable a custom attribute name, set `test_id_attribute` in `conftest.py` (default is `data-testid`).
3. **Role + accessible name** — fine for structural buttons/headings that are unlikely to be reworded.

Avoid: deep CSS chains, Tailwind/Bootstrap utility classes, and locating by long Persian body text.

### Step 4: Add assertions

codegen records actions, not checks. Use `expect()` (auto-waits) after each key step:

```python
from playwright.sync_api import expect
expect(page).to_have_url(re.compile(r"/secure-panel-inotex"))
expect(page.get_by_text("داشبورد")).to_be_visible()
```

### Step 5: Assemble the test file under `tests/e2e/`

Wrap the stabilized code in a `def test_*(page)` function and delete the throwaway capture file.

## Worked example: admin login

`tests/e2e/test_admin_login.py`:

```python
import re
from playwright.sync_api import Page, expect

BASE = "http://127.0.0.1:8000"

def test_admin_login_lands_on_dashboard(page: Page):
    page.goto(f"{BASE}/secure-panel-inotex/login")

    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")          # security answer
    page.get_by_role("button", name="ورود به سیستم").click()

    # Login POSTs JSON to /admin/login, then redirects into the panel
    expect(page).to_have_url(re.compile(r"/secure-panel-inotex"))
    expect(page.get_by_text("داشبورد")).to_be_visible()


def test_navigate_admin_pages(page: Page):
    # Assumes a logged-in session (see conftest fixture below)
    page.goto(f"{BASE}/secure-panel-inotex")
    for label in ("دیتاست", "سوالات", "تنظیمات", "پشتیبان‌گیری"):
        page.get_by_role("link", name=label).click()
        expect(page).to_have_url(re.compile(r"/secure-panel-inotex"))
```

To avoid logging in for every test, capture the session once and reuse `storage_state` (see [../playwright-cli/references/storage-state.md](../playwright-cli/references/storage-state.md)). A `conftest.py` fixture:

```python
import pytest

@pytest.fixture(scope="session")
def admin_storage_state(browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")
    page.get_by_role("button", name="ورود به سیستم").click()
    page.wait_for_url("**/secure-panel-inotex**")
    state_path = "tests/e2e/.auth/admin.json"
    context.storage_state(path=state_path)
    context.close()
    return state_path

@pytest.fixture
def admin_page(browser, admin_storage_state):
    context = browser.new_context(storage_state=admin_storage_state)
    page = context.new_page()
    yield page
    context.close()
```

Then a test takes `admin_page` instead of `page` and is already authenticated.

## Worked example: public chat happy path

The public chat page carries a valid HMAC token and `localhost` is an allowed origin, so a real-browser test works. Mind the rate limit — **2 requests / 30s per IP**, so keep one message per test (or `page.wait_for_timeout(30000)` between sends).

`tests/e2e/test_chat.py`:

```python
from playwright.sync_api import Page, expect

BASE = "http://127.0.0.1:8000"

def test_chat_returns_a_response(page: Page):
    page.goto(f"{BASE}/")

    # Type a question and send (selectors will depend on the active theme —
    # confirm them with codegen against the running chat UI first).
    box = page.get_by_role("textbox")
    box.fill("سلام")
    page.get_by_role("button", name="ارسال").click()

    # A response bubble appears (auto-waits up to the default timeout)
    expect(page.locator(".message").last).to_be_visible(timeout=15000)
```

> Chat selectors come from the active theme partials, not fixed ids. Run `codegen` on `http://127.0.0.1:8000/` to capture the real input/send/message selectors for the installed theme before finalizing the test.

## Running the suite

```bash
.venv/bin/pytest tests/e2e                 # headless
.venv/bin/pytest tests/e2e --headed -k chat
```

## File & naming conventions

- Tests live in `tests/e2e/` (create it; this repo has no test suite yet).
- File names: `tests/e2e/test_<feature>.py` (pytest discovers `test_*`).
- Functions: `def test_<behavior>(page)` — or `(admin_page)` for authenticated flows.
- Shared fixtures (auth, base URL): `tests/e2e/conftest.py`.
- `data-testid` naming (if you add them): `<feature>-<element>` kebab-case, e.g. `chat-send-button`, `dataset-add-row`.
