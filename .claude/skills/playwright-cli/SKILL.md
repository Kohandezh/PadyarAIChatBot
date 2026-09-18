---
name: playwright-cli
description: Drive a real browser against this app with playwright-cli, then write the regression test in Python. Covers the repo's mandatory async Playwright rule, tests/e2e/, and the CLI reference (selectors, mocking, storage, tracing, video).
allowed-tools: Bash(playwright-cli:*) Bash(.venv/bin/python:*) Bash(python:*)
---

# Browser automation and browser tests

Two different things. Keep them apart.

- **`playwright-cli`** is an interactive tool for exploring the app in a browser right
  now. You type commands, it acts and prints a snapshot. Nothing is committed.
- **A browser test** is Python, lives in `tests/e2e/`, and runs in CI.

You explore with the CLI, then you write the test in Python by hand. The CLI prints
TypeScript, so you translate. `references/test-generation.md` shows the mapping.

## The one rule you must not break

**Every browser test in this repo uses Playwright's ASYNC api.**

```python
async def test_something():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ...
```

The sync pytest fixtures `page`, `browser`, `context`, `browser_context` and `playwright`
are **banned**. `tests/test_suite_isolation.py` fails the suite with an AST check if one
appears.

This is not a style preference. `pytest.ini` sets `asyncio_mode = auto`, so a loop is
already running, and Playwright's sync api refuses to start inside one. Worse,
`pytest-playwright` hands the sync driver out through a **session-scoped** fixture, so it
outlives the file that asked for it and every later test calling `asyncio.run()` fails
too. Measured 2026-08-28: one single sync browser test took `pytest -q` from 15 failures
to 141.

So: define your own async browser fixture in the test file. Never request a
pytest-playwright fixture. See `references/page-objects.md` for the fixture this repo
actually uses.

## Repo facts

| | |
|---|---|
| Language | Python. There is no TypeScript, no node project, no `playwright.config.ts` |
| Tests live in | `tests/e2e/` (collected by the default `pytest` run) |
| Run one file | `.venv/bin/python -m pytest tests/e2e/test_chat_localisation.py -q` |
| Browser binary | `.venv/bin/python -m playwright install chromium` |
| Test deps | `requirements-dev.txt` (`pytest`, `pytest-asyncio`, `pytest-playwright`) |
| Dev server | `python main.py`, serves `http://127.0.0.1:8000` |
| Admin panel | `http://127.0.0.1:8000/secure-panel-admin` (login at `/secure-panel-admin/login`) |
| Language | Persian, `<html lang="fa" dir="rtl">`. English is a runtime switch |
| Test IDs | There are **none**. No `data-testid` anywhere. Use ids, roles, or `data-i18n` |
| Gate | CI on GitHub decides pass/fail, not a local run |

Most tests in `tests/e2e/` never start the dev server. They render the page once through
FastAPI's `TestClient`, then serve it and every asset to Chromium from disk through a
`page.route` handler. Nothing reaches a network. Start the real server only when you need
the real backend.

## Quick start with the CLI

```bash
python main.py &                                      # dev server on 127.0.0.1:8000
playwright-cli open http://127.0.0.1:8000
playwright-cli snapshot
playwright-cli click e15                              # refs come from the snapshot
playwright-cli close
```

After each command the CLI prints a snapshot of the browser state. Use the refs (`e15`)
from that snapshot to target elements.

`playwright-cli` is a standalone binary. If it is missing, install it with
`npm install -g @playwright/cli@latest`. It is a developer tool only. It is not a
dependency of this app and must never be added to `requirements.txt`.

## Command reference

Run `playwright-cli <command> --help` for full options.

**Core interaction**

```bash
playwright-cli open [url]                # also: --browser=chrome|firefox|webkit|msedge,
                                         #       --persistent, --profile=DIR, --config=FILE
playwright-cli goto <url>
playwright-cli click|dblclick|hover <ref>
playwright-cli fill <ref> <text> [--submit]
playwright-cli type <text>
playwright-cli select <ref> <value>
playwright-cli check|uncheck <ref>
playwright-cli drag <ref> <ref>
playwright-cli drop <ref> --path=FILE | --data="mime=value"
playwright-cli upload <file>
playwright-cli eval "<js>" [ref]         # read attributes/text not in the snapshot
playwright-cli dialog-accept ["text"] | dialog-dismiss
playwright-cli resize <w> <h>
playwright-cli close | close-all | kill-all
```

**Navigation and input**

```bash
playwright-cli go-back | go-forward | reload
playwright-cli press <Key> | keydown <Key> | keyup <Key>
playwright-cli mousemove <x> <y> | mousedown [right] | mouseup [right] | mousewheel <x> <y>
```

**Capture**

```bash
playwright-cli snapshot [target] [--filename=F] [--depth=N] [--boxes]
playwright-cli screenshot [ref] [--filename=F]
playwright-cli pdf --filename=F
```

**Tabs**

```bash
playwright-cli tab-list | tab-new [url] | tab-close [index] | tab-select <index>
```

**Storage, network, devtools**

```bash
playwright-cli state-save|state-load [file]                  # -> storage-state.md
playwright-cli cookie-* / localstorage-* / sessionstorage-*  # -> storage-state.md
playwright-cli route|unroute|route-list                      # -> request-mocking.md
playwright-cli console [level] | network                     # devtools logs
playwright-cli run-code "<js>" | --filename=F                # -> running-code.md
playwright-cli tracing-start|tracing-stop                    # -> tracing.md
playwright-cli video-start|video-chapter|video-stop          # -> video-recording.md
playwright-cli show --annotate                               # ask the user via the dashboard
playwright-cli generate-locator <ref> [--raw]
playwright-cli highlight <ref> [--style=...] [--hide]
playwright-cli attach --extension=chrome | --cdp=chrome|URL  # -> session-management.md
```

`eval` and `run-code` take **JavaScript**, because they run inside the CLI's own driver
process. That is a property of the tool, not of this repo. Your test file stays Python.

## Raw and JSON output

`--raw` strips the status, generated code and snapshot sections and returns only the
result value, which makes it pipeable. `--json` wraps every reply as JSON.

```bash
playwright-cli --raw eval "document.documentElement.lang"
playwright-cli --raw snapshot > before.yml
playwright-cli click e5
playwright-cli --raw snapshot > after.yml && diff before.yml after.yml
CONV=$(playwright-cli --raw cookie-get padyar_conv)
```

## Targeting elements in this app

Prefer refs from the snapshot. CSS selectors and Playwright locators also work.

There are **no `data-testid` attributes** in this repo, so `getByTestId` will find
nothing. What to use instead, in order of preference:

1. **A stable id.** The chat UI is built around them:
   `#user-input`, `#send-btn`, `#mic-btn`, `#new-chat-btn`, `#menu-toggle`,
   `#theme-btn`, `#lang-btn`, `#chat-view-content`, `#loading-bubble`,
   `#welcome-message`, `#avatar-video`, `#text-view`, `#video-view`, `#menu-history`.
2. **A role plus an accessible name.** Remember the name is Persian by default.
3. **`data-i18n` / `data-i18n-title`.** These mark every translatable control, so they
   are stable across a language switch where the visible text is not.
4. **A structural class** from `static/chat/base.css` (`.message.bot .bubble`,
   `.questions-list li`, `.menu-drawer.open`). Last resort, since a theme may restyle it.

```bash
playwright-cli click e15
playwright-cli click "#send-btn"
playwright-cli click "getByRole('button', { name: 'گفتگوی جدید' })"
playwright-cli click "[data-i18n='newChat']"
```

Never assert on Persian text you typed from memory. Copy it out of the template or the
`I18N` table in `static/chat/core.js`.

## Named sessions

Run several isolated browsers at once with `-s=<name>` (details in
`references/session-management.md`):

```bash
playwright-cli -s=visitor open http://127.0.0.1:8000
playwright-cli -s=admin   open http://127.0.0.1:8000/secure-panel-admin/login
playwright-cli list
playwright-cli close-all
```

This is the right way to check the kiosk threat model: one browser is the previous
visitor, another is the next one.

## Example: send a message in the chat

```bash
python main.py &
playwright-cli open http://127.0.0.1:8000
playwright-cli snapshot
playwright-cli fill "#user-input" "ساعت کاری نمایشگاه چیست؟"
playwright-cli click "#send-btn"
playwright-cli snapshot
playwright-cli --raw eval "document.querySelectorAll('.message.bot .bubble').length"
playwright-cli close
```

## After you find the bug

Reproducing it in the CLI is half the job. The other half is a test that fails without
the fix and passes with it, in `tests/e2e/`, using the async api. Then push and check CI:

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

## Specific tasks

- **Running and debugging browser tests** [references/playwright-tests.md](references/playwright-tests.md)
- **The async browser fixture this repo uses** [references/page-objects.md](references/page-objects.md)
- **Turning a CLI session into a Python test** [references/test-generation.md](references/test-generation.md)
- **Request mocking** [references/request-mocking.md](references/request-mocking.md)
- **Running custom Playwright code from the CLI** [references/running-code.md](references/running-code.md)
- **Browser session management** [references/session-management.md](references/session-management.md)
- **Storage state (cookies, localStorage)** [references/storage-state.md](references/storage-state.md)
- **Tracing** [references/tracing.md](references/tracing.md)
- **Video recording** [references/video-recording.md](references/video-recording.md)
- **Inspecting element attributes** [references/element-attributes.md](references/element-attributes.md)
