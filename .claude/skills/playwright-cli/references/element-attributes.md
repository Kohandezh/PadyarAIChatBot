# Inspecting Element Attributes

When the snapshot does not show an element's `id`, `class`, `data-*` attributes or other
DOM properties, use `eval` to read them.

`eval` takes JavaScript because it runs inside the CLI's driver process. Your test file
stays Python.

## From the CLI

```bash
playwright-cli snapshot
# the snapshot shows a button as e7 but not its id or attributes

playwright-cli eval "el => el.id" e7
playwright-cli eval "el => el.className" e7
playwright-cli eval "el => el.getAttribute('aria-label')" e7
playwright-cli eval "el => el.getAttribute('aria-pressed')" e7
playwright-cli eval "el => getComputedStyle(el).display" e7
```

Add `--raw` to get only the value, ready to pipe or copy into a test.

## What to look for in this app

There are **no `data-testid` attributes**. These are the ones that matter here:

| Attribute | Why it matters |
|---|---|
| `id` | The chat UI is built around stable ids. First choice for a locator |
| `data-i18n` | Marks translatable text. Stable across a language switch, unlike the text |
| `data-i18n-title` | Same, for `title` and `aria-label` |
| `aria-pressed` | State of a toggle (`#theme-btn`, the video sound button) |
| `aria-label` / `title` | Must be localised. A hardcoded Persian label is a real bug this repo has shipped |
| `lang` on `<html>` | `fa` or `en`. The single source of truth for which language is active |
| `class` on `<body>` | Carries `light-mode` and `video-mode` |

Useful one-liners:

```bash
playwright-cli --raw eval "document.documentElement.lang"
playwright-cli --raw eval "document.documentElement.dir"
playwright-cli --raw eval "document.body.className"
playwright-cli --raw eval "getComputedStyle(document.documentElement).getPropertyValue('--wl-primary')"
playwright-cli --raw eval "getComputedStyle(document.body).getPropertyValue('--color-text-primary')"
```

The last two are how you check that a branding value or a light-mode token really reached
the page, rather than trusting that the CSS looks right.

## The same reads in Python

```python
el = page.locator("#theme-btn")
assert await el.get_attribute("aria-pressed") == "false"
assert await el.get_attribute("id") == "theme-btn"

lang = await page.evaluate("document.documentElement.lang")
classes = await page.evaluate("document.body.className")
token = await page.evaluate(
    "getComputedStyle(document.body).getPropertyValue('--color-text-primary')")
```

`page.evaluate` takes a JavaScript source string in Python too. That is Playwright's api,
not a leak from the CLI.
