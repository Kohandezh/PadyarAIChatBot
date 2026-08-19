# Video Recording

Record a browser session as video (WebM) for debugging, documentation, or proof of work.
In Python Playwright, video is a **context option**: pass `record_video_dir` (and
optionally `record_video_size`) when creating the context. One video is written per page
when the context closes.

## Basic recording (`sync_playwright`)

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(
        record_video_dir="recordings/",
        record_video_size={"width": 1280, "height": 800},
    )
    page = context.new_page()

    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin")
    page.locator("#sec-answer").fill("آبی")
    page.get_by_role("button", name="ورود به سیستم").click()
    page.wait_for_url("**/secure-panel-inotex**")

    # Video is flushed to disk when the context closes
    context.close()
    print(page.video.path())     # path to the .webm file
    browser.close()
```

To rename the file to something descriptive after closing:

```python
import shutil
src = page.video.path()
# shutil.move (not os.replace) — falls back to copy+delete across filesystems,
# which os.replace can't do (raises OSError: Invalid cross-device link in CI/Docker).
shutil.move(src, "recordings/admin-login-2026-06-13.webm")
```

## With pytest-playwright

Let the runner record automatically — videos go to `test-results/`:

```bash
.venv/bin/pytest tests/e2e --video on                 # always
.venv/bin/pytest tests/e2e --video retain-on-failure  # only failing tests
```

Or set it per-test via the `browser_context_args` fixture:

```python
import pytest

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args,
            "record_video_dir": "recordings/",
            "record_video_size": {"width": 1280, "height": 800}}
```

## Producing a polished "hero" recording

Playwright's Python API records the raw session; there are no built-in chapter/overlay
helpers. To make a narrated demo, slow the actions and type character-by-character, and
inject your own on-page overlays with `page.evaluate(...)`:

```python
def type_slowly(locator, text, delay_ms=60):
    locator.press_sequentially(text, delay=delay_ms)   # human-paced typing

def show_overlay(page, html, ms=2000):
    # overlays are pointer-events:none so they won't block clicks
    handle = page.evaluate_handle(
        """(html) => {
            const el = document.createElement('div');
            el.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:99999';
            el.innerHTML = html;
            document.body.appendChild(el);
            return el;
        }""",
        html,
    )
    page.wait_for_timeout(ms)
    return handle  # call handle.evaluate("el => el.remove()") to dismiss

# Usage inside a recorded session:
show_overlay(page, "<div style='position:absolute;top:16px;right:16px;"
                   "padding:8px 14px;background:rgba(0,0,0,.7);color:#fff;"
                   "border-radius:8px;font-family:Vazirmatn'>ورود مدیر</div>", 1500)
type_slowly(page.locator("#username"), "admin")
type_slowly(page.locator("#password"), "admin")
page.wait_for_timeout(800)
page.get_by_role("button", name="ورود به سیستم").click()
```

Use `page.wait_for_timeout(...)` to pace the recording so steps are watchable. Keep this
app's RTL/Persian UI in mind: use the Vazirmatn font in overlays and right-align labels.

## Video vs tracing

| Feature  | Video                 | Tracing                                  |
| -------- | --------------------- | ---------------------------------------- |
| Output   | .webm file            | .zip (Trace Viewer)                      |
| Shows    | visual recording      | DOM snapshots, network, console, actions |
| Use case | demos, documentation  | debugging, analysis                      |
| Size     | larger                | smaller                                  |

## Limitations

- Recording adds slight overhead.
- Video is only flushed when the context closes — always `context.close()` to get the file.
- Large recordings consume disk space; clean up `recordings/` periodically.
