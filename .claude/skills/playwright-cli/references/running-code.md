# Running Custom Playwright Code (`sync_playwright`)

For scripted exploration or one-off automation outside of pytest, drive a browser with a
standalone Python script using `sync_playwright()`. Run it with the project interpreter:

```bash
.venv/bin/python scratch_explore.py
```

## Skeleton

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)   # headless=True for CI
    context = browser.new_context()
    page = context.new_page()

    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    # ... your code ...

    context.close()
    browser.close()
```

The same `page` API used in tests is available here. Below are common scenarios, adapted
to the Python sync API.

## Geolocation

```python
context = browser.new_context(
    geolocation={"latitude": 35.6892, "longitude": 51.3890},  # Tehran
    permissions=["geolocation"],
)
```

## Permissions

```python
context.grant_permissions(["clipboard-read", "clipboard-write"])
context.grant_permissions(["geolocation"], origin="http://127.0.0.1:8000")
context.clear_permissions()
```

## Media emulation

```python
page.emulate_media(color_scheme="dark")      # also "light"
page.emulate_media(reduced_motion="reduce")
page.emulate_media(media="print")
```

## Wait strategies

```python
page.wait_for_load_state("networkidle")
page.locator(".loading").wait_for(state="hidden")
page.wait_for_function("() => window.appReady === true")
page.locator(".result").wait_for(timeout=10000)
page.wait_for_url("**/secure-panel-inotex**")
```

## Frames / iframes

```python
frame = page.frame_locator("iframe#my-iframe")
frame.locator("button").click()

for f in page.frames:
    print(f.url)
```

## File downloads (e.g. admin backup / export)

```python
with page.expect_download() as dl_info:
    page.get_by_role("link", name="دانلود پشتیبان").click()
download = dl_info.value
download.save_as("./backup.db")
print(download.suggested_filename)
```

## Clipboard

```python
context.grant_permissions(["clipboard-read"])
text = page.evaluate("() => navigator.clipboard.readText()")

page.evaluate("t => navigator.clipboard.writeText(t)", "سلام کلیپ‌بورد")
```

## Page information

```python
print(page.title())
print(page.url)
html = page.content()
print(page.viewport_size)
```

## Evaluate JavaScript and pass args

```python
info = page.evaluate("""() => ({
  userAgent: navigator.userAgent,
  language: navigator.language,
})""")

multiplier = 5
count = page.evaluate("m => document.querySelectorAll('li').length * m", multiplier)
```

## Error handling

```python
from playwright.sync_api import TimeoutError as PWTimeout

try:
    page.get_by_role("button", name="ارسال").click(timeout=1000)
    result = "clicked"
except PWTimeout:
    result = "element not found"
```

## Complex workflow: log in once and save state

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

Reuse that state later via `browser.new_context(storage_state="tests/e2e/.auth/admin.json")`
(see [storage-state.md](storage-state.md)).
