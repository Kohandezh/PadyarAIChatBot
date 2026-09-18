# Turning a CLI Session into a Python Test

`playwright-cli` prints Playwright **TypeScript** after every action. This repo is
Python. You translate. The CLI output is a record of what you did, not code you paste.

## Workflow

```bash
python main.py &
playwright-cli open http://127.0.0.1:8000
playwright-cli snapshot
# snapshot shows: e1 [textbox], e2 [button "ارسال"] ...

playwright-cli fill e1 "ساعت کاری نمایشگاه چیست؟"
# Ran Playwright code:
# await page.getByRole('textbox').fill('ساعت کاری نمایشگاه چیست؟');

playwright-cli click e2
# Ran Playwright code:
# await page.getByRole('button', { name: 'ارسال' }).click();
```

Then write the Python by hand.

## Translation table

| TypeScript (CLI output) | Python (what you write) |
|---|---|
| `page.getByRole('button', { name: 'X' })` | `page.get_by_role("button", name="X")` |
| `page.getByText('X')` | `page.get_by_text("X")` |
| `page.getByLabel('X')` | `page.get_by_label("X")` |
| `page.locator('#send-btn')` | `page.locator("#send-btn")` |
| `await page.goto(url)` | `await page.goto(url)` |
| `.fill('x')` | `.fill("x")` |
| `.click()` | `.click()` |
| `await page.waitForFunction(...)` | `await page.wait_for_function(...)` |
| `await page.waitForURL(...)` | `await page.wait_for_url(...)` |
| `await expect(x).toBeVisible()` | `assert await x.is_visible()` |
| `await expect(x).toHaveText('y')` | `assert (await x.text_content()).strip() == "y"` |
| `await expect(x).toHaveValue('y')` | `assert await x.input_value() == "y"` |
| `await expect(x).toBeChecked()` | `assert await x.is_checked()` |
| `page.context().storageState(...)` | `await page.context.storage_state(...)` |
| `route.fulfill({ status: 200, body: b })` | `await route.fulfill(status=200, body=b)` |

Rules of thumb: camelCase becomes snake_case, an options object becomes keyword
arguments, and `page.context()` becomes the property `page.context`.

`expect` from `@playwright/test` does not exist in the Python sync-free style this repo
uses. There is a `playwright.async_api.expect`, but the existing tests assert with plain
`assert` and explicit waits, so match that.

## The result

```python
async def test_the_bot_answers_a_question(chat_page):
    await chat_page.fill("#user-input", "ساعت کاری نمایشگاه چیست؟")
    await chat_page.click("#send-btn")
    await chat_page.wait_for_function(
        "document.querySelectorAll('.message.bot .bubble').length > 1")

    bubbles = await chat_page.evaluate(
        "() => Array.from(document.querySelectorAll('.message.bot .bubble'))"
        "        .map(b => b.textContent)")
    assert any("۹" in b or "9" in b for b in bubbles)
```

See `references/page-objects.md` for the `chat_page` fixture. It is not optional: the test
must define its own **async** browser fixture. Never request `page`, `browser` or
`context` from pytest-playwright.

## Picking locators in this app

There are **no `data-testid` attributes**, so `getByTestId` finds nothing. In order of
preference:

1. **A stable id.** `#user-input`, `#send-btn`, `#mic-btn`, `#new-chat-btn`,
   `#menu-toggle`, `#theme-btn`, `#lang-btn`, `#chat-view-content`, `#loading-bubble`,
   `#welcome-message`, `#avatar-video`, `#text-view`, `#video-view`, `#menu-history`.
2. **`data-i18n` / `data-i18n-title`.** Use these for anything that must survive a
   language switch, because the visible text changes and the attribute does not.
3. **Role plus accessible name.** The name is Persian by default.
4. **A structural class** from `static/chat/base.css`. Last resort, since a theme can
   restyle it.

Get a stable locator for a ref straight from the CLI:

```bash
playwright-cli --raw generate-locator e5
```

## Capturing expected values

Read them out of the live page instead of guessing, especially for Persian strings:

```bash
playwright-cli --raw eval "el => el.textContent" e5
playwright-cli --raw eval "el => el.value" e5
playwright-cli --raw eval "el => el.getAttribute('aria-label')" e5
playwright-cli --raw eval "document.documentElement.lang"
playwright-cli --raw snapshot            # accessibility tree, whole page
playwright-cli --raw snapshot e5         # scoped to one element
```

Copy the exact characters into the test. A Persian `ی` and an Arabic `ي` look the same
and are different code points.

## Wait on conditions, never on time

`asyncio.sleep` in a browser test is a flake waiting to happen. Wait for the thing you
actually mean:

```python
await page.wait_for_function("document.documentElement.lang === 'en'")
await page.wait_for_function("typeof renderOptions === 'function'")
await page.locator("#loading-bubble").wait_for(state="hidden")
await page.wait_for_url("**/secure-panel-admin")
```

## What a good browser test in this repo looks like

- It has a docstring naming the defect it holds down, in plain words. Read the headers of
  `tests/e2e/test_chat_localisation.py` and `tests/test_kiosk_privacy.py`. Every one of
  them describes a bug that a source-string assertion could not see. That is the bar for
  adding a browser test at all: if a plain unit test can catch it, write the unit test.
- It asserts from outside the module, the way a visitor drives the page.
- It runs in the default `pytest` run. Do not mark it skip or move it out of `tests/e2e/`.
- It uses a fresh browser context, so nothing leaks into the next test.
