# Browser & Context Isolation

In Python Playwright, isolation is done with **browser contexts** — each context is an
independent "incognito" session with its own cookies, storage, and cache. Use separate
contexts to run isolated flows (e.g. one authenticated admin, one anonymous chat user)
in the same script or test run.

## Multiple isolated contexts

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()

    # Context 1: authenticated admin
    admin = browser.new_context()
    admin_page = admin.new_page()
    admin_page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    # ... log in ...

    # Context 2: anonymous public chat user (separate cookies/storage)
    public = browser.new_context()
    public_page = public.new_page()
    public_page.goto("http://127.0.0.1:8000/")

    admin.close()
    public.close()
    browser.close()
```

Each context has independent: cookies, localStorage/sessionStorage, IndexedDB, cache,
and history. New pages in the same context share that state.

## In pytest-playwright

The `page`, `context`, and `browser` fixtures are provided automatically. To get a fresh
isolated context inside a test, create one from `browser`:

```python
def test_two_users(browser):
    ctx_a = browser.new_context()
    ctx_b = browser.new_context()
    page_a, page_b = ctx_a.new_page(), ctx_b.new_page()
    # ... drive each independently ...
    ctx_a.close()
    ctx_b.close()
```

Customize the per-test context via the `browser_context_args` fixture:

```python
import pytest

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args,
            "base_url": "http://127.0.0.1:8000",
            "locale": "fa-IR",
            "viewport": {"width": 1280, "height": 800}}
```

## Pre-authenticated contexts

Reuse a saved login by passing `storage_state` when creating the context
(see [storage-state.md](storage-state.md)):

```python
ctx = browser.new_context(storage_state="tests/e2e/.auth/admin.json")
```

## Headed / channel options

```python
browser = p.chromium.launch(headless=False, slow_mo=300)     # watch it run
browser = p.chromium.launch(channel="chrome")                # use installed Chrome
browser = p.firefox.launch()                                 # or p.webkit.launch()
```

## Persistent profile (on-disk state)

When you need a profile that survives across runs (cookies, cache on disk), launch a
persistent context instead of a transient one:

```python
context = p.chromium.launch_persistent_context(
    user_data_dir="./.pw-profile",
    headless=False,
)
page = context.new_page()
# ...
context.close()
```

## Connecting to an already-running browser (CDP)

If you started Chrome with `--remote-debugging-port=9222`, attach instead of launching:

```python
browser = p.chromium.connect_over_cdp("http://localhost:9222")
context = browser.contexts[0]
page = context.pages[0]
```

## Cleanup

Always close contexts and the browser when done (or rely on the `with sync_playwright()`
block / pytest fixtures to tear them down). In pytest-playwright, fixture-managed
contexts are closed automatically after each test.
