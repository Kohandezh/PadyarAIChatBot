# Storage State (cookies, localStorage)

Save a browser context's cookies + localStorage to a JSON file and reload it later to
skip the login flow. In this app the admin panel uses a **cookie session**, so saving
storage state after logging in lets every test start already-authenticated.

## Save storage state

```python
# after logging in within `context`
context.storage_state(path="tests/e2e/.auth/admin.json")
```

Standalone script that logs in once and saves state:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()

    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")
    page.get_by_role("button", name="ورود به سیستم").click()
    page.wait_for_url("**/secure-panel-inotex**")

    context.storage_state(path="tests/e2e/.auth/admin.json")
    context.close()
    browser.close()
```

## Restore storage state

```python
context = browser.new_context(storage_state="tests/e2e/.auth/admin.json")
page = context.new_page()
page.goto("http://127.0.0.1:8000/secure-panel-inotex")   # already logged in
```

### In pytest-playwright (the common case)

Build the state once per session, then hand each test a pre-authenticated context:

```python
import pytest

@pytest.fixture(scope="session")
def admin_storage_state(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")
    page.get_by_role("button", name="ورود به سیستم").click()
    page.wait_for_url("**/secure-panel-inotex**")
    path = "tests/e2e/.auth/admin.json"
    ctx.storage_state(path=path)
    ctx.close()
    return path

@pytest.fixture
def admin_page(browser, admin_storage_state):
    ctx = browser.new_context(storage_state=admin_storage_state)
    page = ctx.new_page()
    yield page
    ctx.close()
```

A test then takes `admin_page` and is already inside the panel.

## File format

The saved JSON looks like:

```json
{
  "cookies": [
    {
      "name": "admin_session",
      "value": "…",
      "domain": "127.0.0.1",
      "path": "/",
      "expires": 1735689600,
      "httpOnly": true,
      "secure": false,
      "sameSite": "Lax"
    }
  ],
  "origins": [
    {
      "origin": "http://127.0.0.1:8000",
      "localStorage": [{ "name": "theme", "value": "dark" }]
    }
  ]
}
```

## Cookies & localStorage at runtime

Read/modify directly on the context or page when you don't want a full state file:

```python
# cookies
cookies = context.cookies()
context.add_cookies([{
    "name": "foo", "value": "bar",
    "domain": "127.0.0.1", "path": "/",
}])
context.clear_cookies()

# localStorage (runs in the page)
page.evaluate("() => localStorage.setItem('theme', 'dark')")
value = page.evaluate("() => localStorage.getItem('theme')")
page.evaluate("() => localStorage.clear()")
```

## Security notes

- The app's admin session cookie is sensitive — **never commit** state files.
- Add `tests/e2e/.auth/` (and any `*.auth.json`) to `.gitignore`.
- Prefer test-only credentials; delete state files after the run.
- The admin session has a sliding 1-hour expiry, so regenerate state if it goes stale.
