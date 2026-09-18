# Storage: Cookies and localStorage

This app keeps visitor state in both, and **the kiosk is the threat model**. One booth
browser is shared by strangers all day, so "the next person inherits the last person's
state" is the default bug. Most of what you will check here is whether something was
really forgotten.

## What this app stores

### Cookies (set by the server)

| Cookie | What it carries |
|---|---|
| `padyar_conv` | The server's handle on the current conversation. Cleared by "New chat" |
| `padyar_visitor` | The visitor session. HttpOnly, so page JS cannot read, write or forge it |
| `padyar_edit_s` | The company-contact edit session on `/edit/{token}` (leads module) |

Admin sessions use their own cookie with a sliding 1-hour expiry.

### localStorage (written by the page)

| Key | What it carries |
|---|---|
| `inotex_chat_history` | This browser's copy of the transcript. Replayed by `loadHistory()` |
| `inotex_lang` | `fa` or `en` |
| `inotex-light-mode` | `'1'` or `'0'`. See the `dark-mode` skill |
| `inotex-video-sound` | `'1'` or `'0'` |
| `inotex-theme` | Dead key from a deleted theme. Still read by the boot script |

Both halves matter. `padyar_conv` is the server's copy of the transcript,
`inotex_chat_history` is the browser's, and a reset that forgets one but not the other
replays the previous visitor's messages to the next one. That is a real bug this repo has
shipped and fixed (`tests/test_kiosk_privacy.py`).

## Checking a reset by hand

```bash
playwright-cli open http://127.0.0.1:8000
playwright-cli fill "#user-input" "سلام"
playwright-cli click "#send-btn"

playwright-cli cookie-list
playwright-cli localstorage-list

playwright-cli click "#new-chat-btn"

playwright-cli --raw cookie-get padyar_conv          # should be gone
playwright-cli --raw localstorage-get inotex_chat_history   # should be gone
```

## Cookies

```bash
playwright-cli cookie-list
playwright-cli cookie-list --domain=127.0.0.1
playwright-cli cookie-list --path=/api
playwright-cli cookie-get padyar_conv
playwright-cli cookie-set padyar_conv abc123
playwright-cli cookie-set padyar_conv abc123 --domain=127.0.0.1 --path=/ --httpOnly --sameSite=Lax
playwright-cli cookie-set remember token123 --expires=1735689600
playwright-cli cookie-delete padyar_conv
playwright-cli cookie-clear
```

Several cookies at once, via `run-code` (JavaScript):

```bash
playwright-cli run-code "async page => {
  await page.context().addCookies([
    { name: 'padyar_conv', value: 'c1', domain: '127.0.0.1', path: '/', httpOnly: true }
  ]);
}"
```

## localStorage

```bash
playwright-cli localstorage-list
playwright-cli localstorage-get inotex-light-mode
playwright-cli localstorage-set inotex-light-mode 1
playwright-cli localstorage-set inotex_lang en
playwright-cli localstorage-delete inotex_chat_history
playwright-cli localstorage-clear
```

## sessionStorage

```bash
playwright-cli sessionstorage-list
playwright-cli sessionstorage-get <key>
playwright-cli sessionstorage-set <key> <value>
playwright-cli sessionstorage-delete <key>
playwright-cli sessionstorage-clear
```

## Storage state files

Save and restore a whole browser state (cookies plus per-origin storage):

```bash
playwright-cli state-save                    # auto filename
playwright-cli state-save admin-auth.json
playwright-cli state-load admin-auth.json
playwright-cli open http://127.0.0.1:8000/secure-panel-admin
```

File format:

```json
{
  "cookies": [
    { "name": "padyar_conv", "value": "abc123", "domain": "127.0.0.1",
      "path": "/", "expires": 1735689600, "httpOnly": true,
      "secure": false, "sameSite": "Lax" }
  ],
  "origins": [
    { "origin": "http://127.0.0.1:8000",
      "localStorage": [ { "name": "inotex-light-mode", "value": "1" } ] }
  ]
}
```

## The same things in a Python test

Set state before the page loads. This is how you test the light-mode boot script, which
runs on the first paint and is too late to influence afterwards:

```python
context = await browser.new_context()
await context.add_init_script(
    "localStorage.setItem('inotex-light-mode', '1')")
page = await context.new_page()
await page.goto(f"{ORIGIN}/")
assert "light-mode" in await page.evaluate("document.body.className")
```

Read and clear at runtime:

```python
value = await page.evaluate("localStorage.getItem('inotex_chat_history')")
await page.evaluate("localStorage.clear()")

cookies = await page.context.cookies()
assert not [c for c in cookies if c["name"] == "padyar_conv"]
await page.context.clear_cookies()
```

Set a cookie:

```python
await page.context.add_cookies([
    {"name": "padyar_conv", "value": "c1", "url": ORIGIN},
])
```

**Always use a fresh `browser.new_context()` per test.** A shared context leaks one test's
language, light mode and history into the next, which is the same failure the kiosk has
with real people.

## Security notes

- Never commit a storage-state file containing a real session. Delete it when done.
- Never put a real admin password in a script, a test or a saved state file.
- `padyar_visitor` is HttpOnly on purpose. A browser test must not be able to forge a
  signed-in session by writing `localStorage`. That was the exact defect
  `tests/e2e/test_visitor_session_e2e.py` exists to hold down: `isSignedIn()` used to mean
  "localStorage has a name in it", so four seconds in a console got anyone past the gate.
  Identity now comes from `GET /api/auth/session`, which reads the cookie the page cannot
  see. If a change makes localStorage enough again, that test must fail.
