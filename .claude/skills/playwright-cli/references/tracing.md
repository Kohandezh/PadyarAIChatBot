# Tracing

A Playwright trace captures DOM snapshots, screenshots, network activity, and console
logs for every action — the best tool for debugging a flaky or failing e2e test. View
traces in the Trace Viewer.

## With pytest-playwright

The simplest path — let the test runner record traces:

```bash
.venv/bin/pytest tests/e2e --tracing on                  # always
.venv/bin/pytest tests/e2e --tracing retain-on-failure   # only on failures
```

Trace zips land under `test-results/`. Open one:

```bash
.venv/bin/playwright show-trace test-results/<...>/trace.zip
```

## In a `sync_playwright` script

Start/stop tracing on the **context**:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()

    context.tracing.start(screenshots=True, snapshots=True, sources=True)

    page = context.new_page()
    page.goto("http://127.0.0.1:8000/secure-panel-inotex/login")
    page.locator("#username").fill("admin")
    page.get_by_role("button", name="ورود به سیستم").click()

    context.tracing.stop(path="trace.zip")
    context.close()
    browser.close()
```

Then:

```bash
.venv/bin/playwright show-trace trace.zip
```

## What a trace captures

| Category    | Details                                            |
| ----------- | -------------------------------------------------- |
| Actions     | clicks, fills, navigations, keyboard input         |
| DOM         | full snapshot before/after each action             |
| Screenshots | visual state at each step                          |
| Network     | requests, responses, headers, bodies, timing       |
| Console     | all console messages                               |
| Sources     | the line of test code that triggered each action   |

## Trace vs video vs screenshot

| Feature           | Trace        | Video       | Screenshot       |
| ----------------- | ------------ | ----------- | ---------------- |
| Format            | .zip (viewer)| .webm       | .png             |
| DOM inspection    | Yes          | No          | No               |
| Network details   | Yes          | No          | No               |
| Step-by-step      | Yes          | Continuous  | Single frame     |
| Best for          | Debugging    | Demos       | Quick capture    |

## Best practices

1. **Trace the whole flow**, not just the failing step — start tracing before the first
   action so the viewer shows the lead-up to the problem.
2. **Use `retain-on-failure`** in routine runs so passing tests don't pile up trace zips.
3. **Clean up old traces** — they're sizable:

   ```bash
   find test-results -name 'trace.zip' -mtime +7 -delete
   ```
