# Writing & Running pytest-playwright Tests

Tests are `pytest` functions that take the `page` fixture from **pytest-playwright** and
assert with `expect()`. They run against the app started with `.venv/bin/python main.py`.

## A test file

`tests/e2e/test_admin_login.py`:

```python
import re
from playwright.sync_api import Page, expect

BASE = "http://127.0.0.1:8000"


def test_login_lands_on_dashboard(page: Page):
    page.goto(f"{BASE}/secure-panel-inotex/login")
    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")
    page.get_by_role("button", name="ورود به سیستم").click()

    expect(page).to_have_url(re.compile(r"/secure-panel-inotex"))
    expect(page.get_by_text("داشبورد")).to_be_visible()
```

## Running

```bash
# Make sure the app is running first (separate terminal):
#   .venv/bin/python main.py

.venv/bin/pytest tests/e2e                 # all e2e tests, headless
.venv/bin/pytest tests/e2e -k login        # filter by name
.venv/bin/pytest tests/e2e --headed        # watch the browser
.venv/bin/pytest tests/e2e --headed --slowmo 400
.venv/bin/pytest tests/e2e --browser firefox    # chromium (default) | firefox | webkit
.venv/bin/pytest tests/e2e --base-url http://127.0.0.1:8000   # then page.goto("/...")
.venv/bin/pytest tests/e2e -x -vv          # stop on first failure, verbose
```

pytest-playwright also accepts `--video=on`, `--screenshot=on`,
`--tracing=on` (or `retain-on-failure`) — artifacts land in `test-results/`.

## Debugging a failing test

1. **Re-run headed + slow** to watch what happens:

   ```bash
   .venv/bin/pytest tests/e2e -k failing_test --headed --slowmo 600
   ```

2. **Pause with the Inspector** — set `PWDEBUG=1`; the test pauses and opens the
   Playwright Inspector so you can step and pick locators:

   ```bash
   PWDEBUG=1 .venv/bin/pytest tests/e2e -k failing_test
   ```

3. **Capture a trace** for post-mortem (see [tracing.md](tracing.md)):

   ```bash
   .venv/bin/pytest tests/e2e -k failing_test --tracing retain-on-failure
   .venv/bin/playwright show-trace test-results/.../trace.zip
   ```

4. **Reproduce the locator with codegen** against the live page, then update the
   assertion or locator in the test. Most failures are a stale locator or a missing
   `expect(...)` auto-wait — but it may also be a real app bug; use judgement.

After fixing, re-run the single test, then the whole `tests/e2e` suite.
