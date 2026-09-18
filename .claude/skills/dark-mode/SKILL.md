---
name: dark-mode
description: Light/dark mode in the chat UI. There is no app-wide dark-mode system here. The inotex theme is dark by default and switches to light with body.light-mode, stored per visitor. Load before adding or fixing a light-mode rule, or before giving a new theme a toggle.
---

# Light and dark mode in PadyarAIChatbot

## Read this first: what actually exists

Be honest about the scope. There is **no global dark-mode system** in this repo.

- There is no `prefers-color-scheme` media query anywhere in the app's own CSS.
- There is no `data-theme` attribute, and no `.dark` utility class.
- The **admin panel has no light/dark switch at all**. Its sidebar is fixed to
  `data-bs-theme="dark"` in `templates/admin/layout.html`, and the rest of the page is
  Tabler's light default. That is a deliberate fixed look, not a mode.
- Exactly one theme has a light mode: **`inotex`**, the default chat theme. It is **dark
  by default** and switches to light by adding the class `light-mode` to `<body>`.

So "dark mode" in this repo means one thing: the per-visitor light-mode toggle in the
chat UI. Do not invent a second system. If a task asks for app-wide dark mode, say that
it does not exist and scope the work to this pattern, or raise it as an architecture
decision first (`docs/engineering/DECISIONS.md`).

Installed themes today are `base` and `inotex` only. Older docs mention `haj`,
`liquid-glass` and `minimal` with their own toggles. Those themes were deleted in commit
`d668911`. Do not write code against them.

## The pattern, in four files

### 1. Boot script, before the first paint

`themes/base/partials/index.html`, the first thing inside `<body>`:

```js
var stored = localStorage.getItem('inotex-light-mode');   /* '1' | '0' */
var glass  = localStorage.getItem('inotex-theme');        /* 'light' | 'dark' */
var light = stored !== null ? stored === '1'
          : glass !== null ? glass === 'light'
          : window.matchMedia('(prefers-color-scheme: light)').matches;
if (light) document.body.classList.add('light-mode');
```

Why it sits there and not in `footer.html`: the toggle wiring loads at the bottom of the
page, which is far too late. The page would paint dark and then flip. Resolving the class
on the first line inside `<body>` makes the very first paint correct.

The whole block is wrapped in `try/catch`. If storage is blocked, the dark default stands
and the page still works.

`inotex-theme` is a leftover key from the deleted `liquid-glass` theme. It is harmless
(nothing writes it any more) but it is dead. Leave it alone unless the task is cleanup,
and if you do remove it, check no install has that key in a visitor's browser.

### 2. The control

`themes/inotex/partials/menu.html`, inside the hamburger drawer:

```html
{% if menu_show_theme_toggle %}
  <button class="theme-btn" id="theme-btn" type="button" aria-pressed="false"
          title="حالت روشن" data-i18n-title="themeToggle" aria-label="Switch to light mode">
    <span class="theme-icon-moon">...</span>
    <span class="theme-icon-sun">...</span>
  </button>
{% endif %}
```

`menu_show_theme_toggle` comes from `app/services/menu_settings.py`. It is a `settings`
row the admin can switch off, and it defaults to on. It only ever **hides** a row a theme
already has. It never adds one.

The row label is `<span data-i18n="themeLabel">روشن / تاریک</span>`, and `themeLabel` is a
real key in the `I18N` table in `static/chat/core.js`.

`data-i18n-title="themeToggle"` on the button is **not**. There is no `themeToggle` key.
The button's `title` and `aria-label` are set by the footer script below, which is why
they are still correct in both languages. If you want to change that wording, change it
there. Adding a `themeToggle` key to `I18N` would not be read by anything.

### 3. The wiring

`themes/inotex/partials/footer.html`, in its own IIFE. Four rules it enforces:

```js
const KEY = 'inotex-light-mode';

function apply(light, persist) {
    document.body.classList.toggle('light-mode', light);
    btn.setAttribute('aria-pressed', light ? 'true' : 'false');
    const fa = document.documentElement.lang !== 'en';
    btn.title = light ? (fa ? 'حالت تاریک' : 'Dark mode')
                      : (fa ? 'حالت روشن' : 'Light mode');
    btn.setAttribute('aria-label', btn.title);
    if (persist) localStorage.setItem(KEY, light ? '1' : '0');
}
```

1. **Only a tap persists.** The boot pass calls `apply(..., false)`. Writing the resolved
   value on load would freeze whatever the device said on the first visit and store it as
   if the visitor had chosen it, so their device switching later would never reach the
   page again.
2. **A saved choice wins over the device.** Nothing saved means follow the device.
3. **Follow the device live, but only while nothing is stored**, via a `change` listener
   on the `matchMedia` query.
4. **The control labels itself**, bilingually, and reports state through `aria-pressed`.
   The icon swap is CSS, not JS (rule 4 below).

### 4. The CSS

`themes/inotex/static/style.css`, near the bottom. The main block **re-points the
semantic tokens** instead of restyling components:

```css
body.light-mode {
    --color-page-background: #EEF3FC;
    --color-surface-primary: #FFFFFFCC;
    --color-surface-sunken: #FFFFFFE6;
    --color-surface-interactive: #DCE7FA;
    --color-text-primary: #14203A;
    --color-text-muted: #4A5B7C;
    --color-border-interactive: #9FBBE8;
    --color-border-subtle: #2D5CA733;
    --raised-depth: 0 1px 0 rgba(255,255,255,.9), 0 10px 26px rgba(20,40,90,.14);

    background-color: var(--color-page-background);
    color: var(--color-text-primary);
}
```

Every component that already uses a token follows for free. The hamburger drawer is the
worked example: it reads `--color-surface-primary`, so it adapts with no rule of its own.

After that block come roughly 30 narrow overrides, only for the things a token cannot
carry: `.view-container` (the dark brick photo is replaced by a frosted wash),
`backdrop-filter` on `header.inx-header` and `.switcher`, link and bold colours inside
`.bubble`, form field surfaces, the overlay scrim, and the icon swap:

```css
body.light-mode .theme-btn .theme-icon-moon { display: none; }
body.light-mode .theme-btn .theme-icon-sun  { display: inline-flex; }
```

## The rules to follow

**Re-point tokens first.** Add a per-component `body.light-mode .thing { ... }` rule only
when the difference cannot be expressed as a token value. Each extra rule is one more
place that can go stale when the component changes.

**Never hardcode a brand colour in a light-mode rule.** Light mode re-points the
`--color-*` semantic layer. It must not reach past it and overwrite `--wl-*` or
`--inotex-*`, because those carry the customer's saved branding
(`app/services/branding.py`). See the `ui` skill for the full token chain.

**Light mode is per visitor, never install-wide.** This app runs on a shared booth kiosk.
The preference goes in `localStorage` only. It must never touch the `active_theme`
setting, or one visitor preferring light changes what every later visitor sees. The same
reasoning is why `padyar_conv` and the chat history key get cleared on "New chat"
(`tests/test_kiosk_privacy.py`).

**Do not create a third storage key.** `inotex-light-mode` is the key. If a new theme
needs a light mode, reuse this key or the boot script will not see it.

**Cover both modes in one pass.** A rule written only for dark leaves a light visitor
with invisible text. Check contrast in both, not just the one on your screen.

## Giving a new theme a light mode

1. Make sure the theme's semantic tokens are in a `:root` block and that its components
   read the tokens, not raw colours. If they do not, fix that first. Everything else
   depends on it.
2. Add a `body.light-mode { ... }` block that re-points those tokens.
3. Copy the toggle button into the theme's `menu.html`, inside
   `{% if menu_show_theme_toggle %}`, keeping the id `theme-btn` and both icon spans.
4. Copy the light/dark IIFE into the theme's `footer.html`. Keep the key
   `inotex-light-mode` so the boot script in `themes/base/partials/index.html` already
   applies it before first paint.
5. Add only the narrow overrides the tokens cannot express.

If a theme has no light mode, it simply has no toggle and no `light-mode` rules. The
class the boot script might add means nothing in its CSS, and that is fine by design.

## Verify it

1. Open the chat, open the drawer, tap the toggle. Both directions.
2. Reload. The choice survives.
3. Clear `localStorage`, set the OS to light, reload. The page opens light with **no
   flash of dark**. A flash means something moved the boot script or added a rule the
   script does not set.
4. Switch language to EN and check the button's `title` and `aria-label` follow.
5. Check contrast on text, borders and focus rings in the light palette.
6. Check both tabs (chat and video). Light mode paints `.view-container`, which is shared
   by both, so it applies whichever tab is open.
7. Turn the row off in the admin menu settings and confirm the drawer row disappears and
   the page still renders in whatever mode was stored.

In a browser test, force the device preference with Playwright and assert on the class.
Browser tests in this repo use the **async** api (see the `playwright-cli` skill):

```python
context = await browser.new_context(color_scheme="light")
page = await context.new_page()
await page.goto(f"{ORIGIN}/")
assert "light-mode" in await page.evaluate("document.body.className")
```

Or flip it at runtime with `await page.emulate_media(color_scheme="dark")`.
