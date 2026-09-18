# Running and Debugging Browser Tests

Browser tests here are pytest tests. There is no `npx playwright test` in this repo and
no `playwright.config.ts`.

## Setup, once

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
```

Without the browser binary the tests skip rather than fail. They call
`pytest.importorskip("playwright.async_api")` and then try a real launch, skipping with
`chromium unavailable: ...` if it is not there.

## Running

```bash
# one file
.venv/bin/python -m pytest tests/e2e/test_chat_localisation.py -q

# one test
.venv/bin/python -m pytest tests/e2e/test_chat_localisation.py::test_the_faq_block_is_still_rebuilt_in_the_new_language -q

# every browser test
.venv/bin/python -m pytest tests/e2e -q

# the guard that keeps the sync fixtures out
.venv/bin/python -m pytest tests/test_suite_isolation.py -q
```

`tests/e2e/` is part of the default `pytest` run on purpose. `tests/test_suite_isolation.py`
asserts that it still is, so nobody can quietly hide the browser tests instead of fixing
one.

**Do not run the full local suite as a gate.** This Mac has 15 tests that always fail
locally (they need a live PostgreSQL or a network) and always pass on CI. CI on GitHub is
the pass/fail signal:

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

## Debugging a failing browser test

### 1. See the browser

Launch headed and slow it down inside the test's own fixture:

```python
browser = await p.chromium.launch(headless=False, slow_mo=250)
```

Remember to put it back before committing.

### 2. Read the page's console

Browser-side errors do not reach pytest output unless you forward them:

```python
page.on("console", lambda m: print("CONSOLE:", m.type, m.text))
page.on("pageerror", lambda e: print("PAGEERROR:", e))
```

Run pytest with `-s` so the prints show.

### 3. Pause and poke

```python
await page.pause()          # opens Playwright Inspector, needs headless=False
```

### 4. Dump what the page actually rendered

```python
print(await page.content())
print(await page.evaluate("document.body.className"))
print(await page.locator(".message.bot .bubble").all_text_contents())
```

### 5. Screenshot the failure

```python
await page.screenshot(path="/tmp/failure.png", full_page=True)
```

### 6. Reproduce by hand

Often faster than instrumenting the test. Start the server and drive it with the CLI:

```bash
python main.py &
playwright-cli open http://127.0.0.1:8000
playwright-cli snapshot
```

The CLI prints the state after every command, so you see exactly where the real page
stops matching what the test expects.

## Common failures, and what they mean

| Symptom | Cause |
|---|---|
| `Please use the Async API instead` | The test asked for a sync fixture (`page`, `browser`, `context`). Never do that here. Read the top of the skill |
| Dozens of unrelated tests fail with "cannot be called from a running event loop" | A sync browser fixture ran earlier and left its loop alive. Find it. `tests/test_suite_isolation.py` names the file |
| `chromium unavailable` skip | Run `.venv/bin/python -m playwright install chromium` |
| A locator times out on Persian text | The page is in Persian by default and the text was typed from memory. Copy the exact string from the template or the `I18N` table in `static/chat/core.js` |
| The page loads blank | An asset 404'd in the `page.route` handler. Log the unmatched paths in the handler before falling through to the 404 branch |
| The test passes alone and fails in the suite | Shared state. Cookies, `localStorage`, or a module-level singleton. The kiosk threat model applies to tests too |

## Writing the test

Read `references/page-objects.md` for the fixture pattern, and
`references/test-generation.md` for turning a CLI session into Python.

Before you finish:

```bash
python -m py_compile app/main.py app/routers/chat.py    # if you touched app code
```
