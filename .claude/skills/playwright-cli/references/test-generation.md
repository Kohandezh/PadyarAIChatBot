# Test Generation (from `playwright codegen`)

Generate Playwright **Python** test code by recording your interactions, then clean it up
into a pytest test.

## How it works

`playwright codegen` opens a real browser and transcribes everything you do into Python
`page.*` calls, preferring role/label/placeholder locators.

```bash
.venv/bin/playwright codegen http://127.0.0.1:8000/secure-panel-inotex/login \
  --target python --output tests/e2e/_capture.py
```

`--target python` emits the sync API. `--output` writes to a file; omit it to print to the
Inspector window where you can copy from.

## Example session output

After typing into the login form, codegen produces something like:

```python
page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
page.get_by_placeholder("نام کاربری").fill("admin")
page.get_by_placeholder("رمز عبور").fill("admin")
page.get_by_placeholder("رنگ مورد علاوه؟").fill("آبی")
page.get_by_role("button", name="ورود به سیستم").click()
```

## Turn it into a test

Wrap the body in a `def test_*(page)` function and add assertions (codegen records
actions, not checks):

```python
import re
from playwright.sync_api import Page, expect


def test_admin_login(page: Page):
    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    page.locator("#username").fill("admin")          # stabilized from placeholder
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")
    page.get_by_role("button", name="ورود به سیستم").click()

    # assertions added by hand:
    expect(page).to_have_url(re.compile(r"/secure-panel-inotex"))
    expect(page.get_by_text("داشبورد")).to_be_visible()
```

Delete the throwaway `_capture.py` afterward.

## Best practices

### 1. Prefer stable locators

This app exposes ids on the login form (`#username`, `#password`, `#sec-answer`) — prefer
those over Persian placeholder text, which is UI copy that can change. For other pages,
role + accessible name (`get_by_role("button", name=...)`) is usually fine; avoid CSS
class chains and locating by long body text.

### 2. Explore before recording

Take a quick look at the page (codegen, or `page.content()` in a `sync_playwright`
script) to learn the structure before committing to selectors.

### 3. Add assertions by hand

`expect()` auto-waits and retries. Useful matchers:

- `expect(locator).to_be_visible()`
- `expect(locator).to_have_text("…")`  /  `to_contain_text("…")`
- `expect(locator).to_have_value("…")`  /  `to_be_empty()`
- `expect(locator).to_be_checked()`
- `expect(page).to_have_url(re.compile(r"…"))`
- `expect(page).to_have_title("…")`

For text assertions, locate by id/role/test-id rather than by the text itself, so the
locator and the asserted text don't reference the same string. When the locator *is*
text-based, prefer `to_be_visible()`.

```python
expect(page.get_by_role("alert")).to_be_visible()
expect(page.locator("#dashboard-title")).to_have_text("داشبورد")
expect(page.locator("#username")).to_have_value("admin")
```
