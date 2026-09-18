# Tracing

Capture a detailed execution trace for debugging. A trace holds DOM snapshots,
screenshots, network activity and console logs for every step.

## From the CLI

```bash
playwright-cli tracing-start

playwright-cli open http://127.0.0.1:8000
playwright-cli fill "#user-input" "ساعت کاری نمایشگاه چیست؟"
playwright-cli click "#send-btn"

playwright-cli tracing-stop
```

## Output files

Tracing creates a `traces/` directory with:

### `trace-{timestamp}.trace`

The action log: every click, fill and navigation, a DOM snapshot before and after each
one, screenshots, timings, console messages and source locations.

### `trace-{timestamp}.network`

Every request and response, with headers, bodies, timing (DNS, connect, TLS, TTFB,
download), sizes, and failures.

### `resources/`

Cached images, fonts, stylesheets and scripts, so the page can be reconstructed on replay.

## What a trace captures

| Category | Details |
|---|---|
| Actions | Clicks, fills, hovers, keyboard input, navigations |
| DOM | Full snapshot before and after each action |
| Screenshots | Visual state at each step |
| Network | All requests, responses, headers, bodies, timing |
| Console | Every `console.log`, `warn` and `error` |
| Timing | Precise timing per operation |

## When to reach for it in this repo

**A click that does nothing.** The DOM snapshot at the moment of the click shows whether
the element was there, visible and hittable. The chat header lays itself out inside a
fixed frame, and buttons there can report as outside the viewport, which is why
`tests/e2e/test_chat_localisation.py` fires `#lang-btn` through `page.evaluate` instead
of a Playwright click. A trace is how you find out you are in that situation.

**An answer that never arrives.** The network log shows whether `/chat` was called, what
was posted, and what came back. The tiered pipeline can answer locally or call a model, so
the request body and the status tell you which tier ran.

**A screen that renders wrong only after several steps.** Step-by-step DOM replay beats
re-running the flow and hoping to catch the moment.

## In a Python test

```python
context = await browser.new_context()
await context.tracing.start(screenshots=True, snapshots=True, sources=True)
page = await context.new_page()
# ... the failing steps ...
await context.tracing.stop(path="/tmp/trace.zip")
await context.close()
```

Open it with:

```bash
.venv/bin/python -m playwright show-trace /tmp/trace.zip
```

Write the trace to `/tmp` or the session scratchpad, never into the repo. Do not leave
tracing switched on in a committed test. It slows every run and CI does not read the
output.

## Practice

**Start tracing before the problem, not at it.** Trace the whole flow. The cause is
usually two steps earlier than the failure.

**Clean up.** Traces are large.

```bash
find .playwright-cli/traces -mtime +7 -delete
```

## Limits

- Tracing adds overhead.
- Large traces use significant disk space.
- Some dynamic content does not replay perfectly. Video playback in the avatar tab is one
  case: the trace shows the element and the network fetch, not the frames.

## Trace, video, screenshot

| | Trace | Video | Screenshot |
|---|---|---|---|
| Format | `.trace` file | `.webm` | `.png` / `.jpeg` |
| DOM inspection | Yes | No | No |
| Network details | Yes | No | No |
| Step-by-step replay | Yes | Continuous | Single frame |
| File size | Medium | Large | Small |
| Best for | Debugging | Demos | Quick capture |
