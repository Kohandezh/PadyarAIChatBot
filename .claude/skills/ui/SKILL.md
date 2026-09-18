---
name: ui
description: Build or change UI in PadyarAIChatbot. Jinja2 partials, vanilla JS and CSS custom properties, Persian and RTL. Load this BEFORE editing a theme partial, static/chat/, templates/admin/, static/admin/, or any colour.
---

# UI in PadyarAIChatbot

There is no React, no Tailwind, no build step and no component library in the chat UI.
Pages are Jinja2 templates. Behaviour is plain JavaScript. Colours are CSS custom
properties fed by database settings.

Read this whole file before you touch a pixel.

## Rule zero: the product rule beats every style rule

From `CLAUDE.md`. It is not advice, it is the product:

- Anyone must be able to use this, from a child to an elderly person.
- Every screen is understandable in under 3 seconds.
- Every action takes under 3 clicks.
- No jargon in user-facing text. No technical words a visitor would have to look up.
- If a feature needs an explanation, simplify it or drop it.

Before you ship a screen, ask: would my grandmother know what to do here without being
told? If no, the design is wrong, not the user.

Also read `docs/engineering/UI_UX.md`. It is the binding UI standard for this repo and it
lists the review checklist (states, keyboard, focus, mobile, destructive actions).

## The two surfaces

They share almost nothing. Know which one you are in.

| | Chat UI (visitors) | Admin UI (operator) |
|---|---|---|
| Templates | `themes/<name>/partials/*.html` over `themes/base/partials/*.html` | `templates/admin/*.html` |
| Rendered by | `app/services/themes.py` (`render_theme_index`) | FastAPI Jinja2Templates |
| Structural CSS | `static/chat/base.css` | `static/vendor/bootstrap/bootstrap.rtl.min.css` + Tabler |
| Visual CSS | `themes/<name>/static/style.css` | `static/admin/css/variables.css`, `base.css`, `login.css` |
| JS | `static/chat/core.js` (+ `static/companion/*.js`) | `static/admin/js/<page>.js` |
| Jinja autoescape | **off** (see Escaping below) | on |
| Language | Persian, `lang="fa" dir="rtl"`, switchable to English | Persian only, RTL |

Installed themes today: `base` (not selectable, supplies defaults) and `inotex` (the
default, the only selectable one). Older docs mention `haj`, `minimal` and
`liquid-glass`. Those were deleted in commit `d668911`. Do not write code or docs that
assume they exist.

## Chat UI: how a page gets built

`themes/base/partials/index.html` is the master template. It includes, in order:

```
head.html
(inline colour-mode boot script)
menu.html          <- hamburger drawer / desktop sidebar
.app-layout
  header.html      <- tab switcher, accessibility controls
  .view-container
    messages.html  <- text chat
    suggestions.html
    video.html     <- avatar tab
  input.html       <- textarea, mic, send
footer.html        <- loads core.js, theme JS overrides, calls initChat()
```

A theme overrides one partial by putting a file with the same name in its own
`partials/` folder. `render_theme_index()` builds a Jinja2 `FileSystemLoader` search
path, child first: `[theme/partials, parent/partials, base/partials]`. A theme with a
`"parent"` key in its `theme.json` inserts that parent before `base`.

So: to change one part of the chat, copy only that partial into the theme and edit it.
Never fork `index.html` to change a button.

`inotex` currently overrides: `header.html`, `menu.html`, `messages.html`,
`suggestions.html`, `video.html`, `input.html`, `footer.html`. It inherits `head.html`
and `index.html` from base.

### Adding a theme

1. `themes/<name>/theme.json` with `name`, `display_name`, `description`, `version`.
   Add `"selectable": false` only if it is a defaults-only theme like `base`.
2. `themes/<name>/static/style.css`.
3. `screenshot.png` for the admin theme picker.
4. `partials/` with only the files that differ.
5. It is discovered at startup. No registration code.

## Colours: never type a hex code into a rule

Colours flow through four steps. Learn the chain, then use the last step.

1. **`settings` table**, keys prefixed `whitelabel_`. The operator edits them in
   Settings > Branding (`/secure-panel-admin/settings/branding`).
2. **`app/services/branding.py`**. `WL_DEFAULTS` is the single source of truth: every key
   and its shipped default. `WL_FIELD_TO_KEY` maps the admin form field names onto keys.
   An empty saved value collapses back to the default.
3. **`--wl-*` custom properties.** `chat_branding_context()` emits one `<style>` tag on
   `:root` with these, injected by `head.html` as `{{ wl_style }}`:

   ```
   --wl-primary  --wl-accent  --wl-yellow-light  --wl-navy  --wl-teal
   --wl-dark-teal  --wl-background  --wl-white  --wl-footer-color
   --wl-chat-background   (a url("...") token)
   --wl-video-background  (a url("...") token)
   ```

4. **Theme tokens.** `themes/inotex/static/style.css` maps its own names onto those, with
   the official palette as a `var()` fallback so an unset install still looks right:

   ```css
   --inotex-primary: var(--wl-accent, #FCB715);
   --inotex-blue:    var(--wl-primary, #2D5CA7);
   ```

   The primary/accent crossover is deliberate and documented in that file. Do not "fix" it.

   It then maps those onto semantic tokens, and **these are what rules should use**:

   ```
   --color-page-background     --color-surface-primary    --color-surface-sunken
   --color-surface-raised      --color-surface-interactive
   --color-action-primary      --color-action-primary-hover
   --color-accent-information  --color-accent-success     --color-accent-success-dark
   --color-text-primary        --color-text-muted         --color-text-on-yellow
   --color-border-interactive  --color-border-subtle      --color-focus-ring
   ```

   Non-colour tokens in the same block: `--brick-unit`, `--stud-size`,
   `--compact-radius`, `--panel-radius`, `--raised-depth`, `--panel-gap`,
   `--border-width`, `--motion-instant`, `--motion-fast`, `--motion-normal`,
   `--motion-deliberate`, `--chat-measure`.

**So "change a colour" means one of two things.** Either the operator changes it in
Settings > Branding (no code), or you re-point a token. Writing `#2D5CA7` into a rule
breaks white-label for every customer install, because their saved brand colour will not
reach your rule.

### Adding a new branding key

Four edits, all of them required, or the page will not update:

1. Add the key and its default to `WL_DEFAULTS` in `app/services/branding.py`.
2. Add the form field name to `WL_FIELD_TO_KEY`.
3. Emit it in `chat_branding_context()`'s `wl_style` string (use `_css_url()` for a URL,
   `esc()` for a colour or text).
4. Add the input to the Branding admin page.

`wl_cache_key()` is a tuple of every value in `WL_DEFAULTS`, so a new key joins the
rendered-page cache key automatically. That cache is why an admin save shows up on the
next request. Skip step 1 and your key is invisible to the cache, so saves appear to do
nothing.

## Structure versus visuals

`static/chat/base.css` owns layout: flex, sizing, positions, animations, breakpoints. It
is shared by every theme.

`themes/<name>/static/style.css` loads after it and owns only visual properties: colour,
background, border, shadow, radius.

If you need a layout change for one theme, do it in that theme's CSS. Change `base.css`
only when every theme needs it. `base.css` is the contract the base and any future theme
rely on.

Real class and id names you will meet in `base.css` (use these, do not invent):

```
.app-layout  .view-container  .tab-view(.active)  #text-view  #video-view
#chat-view-content  .message(.bot|.user)  .bubble  #welcome-message
.typing-indicator .dot  #loading-bubble  .questions-list  .show-more-btn
.avatar-container  #avatar-video  .video-actions  .action-btn
#input-area  .input-wrapper  #user-input  .mic-btn(.recording)  #send-btn
.header-tools  #new-chat-btn  .menu-toggle  .menu-backdrop  .menu-drawer(.open|.collapsed)
.menu-section  .menu-row  .menu-row-label  .menu-footer  #menu-history
.menu-history-item  .menu-newchat-btn  .menu-logout-btn  .menu-account-btn
.menu-sidebar-header  .menu-sidebar-logo  .menu-sidebar-toggle-btn
.menu-sidebar-toggle-logo  .menu-sidebar-toggle-icon
```

Breakpoints in `base.css`: `max-width: 768px` (phone), `min-width: 769px`, and
`min-width: 992px` where `body` becomes a flex row and `.menu-drawer` stops being an
overlay and becomes a real sidebar (collapsible to a 76px rail).

At 992px and up, DOM order is visual order. `menu.html` is included before `.app-layout`
on purpose. `body` is `dir="rtl"`, so the first flex child sits at the physical right,
matching where the mobile overlay anchors. Reorder those includes and the sidebar jumps
to the wrong side on desktop only.

### The sidebar logo, and why base.css must not change

`base.css` assumes two elements: `.menu-sidebar-logo` (shown only when expanded) and
`.menu-sidebar-toggle-btn` (a compact stand-in on the collapsed rail that swaps to the
collapse icon on hover). `inotex` does it differently: it has no `.menu-sidebar-logo`,
and keeps the full brand mark inside `.menu-sidebar-toggle-btn` in both states, via
overrides in its own `style.css`. If another theme wants that look, put the override in
that theme's CSS. `base` still relies on the two-element layout.

## Chat JavaScript

`static/chat/core.js` holds all chat behaviour: send, type, tabs, video, voice,
accessibility, the drawer, history. It exposes a `ChatConfig` object that a theme sets
in its `footer.html` **before** calling `initChat()`:

```js
ChatConfig.addMessageFn = function (content, type, save, instant) { ... };
ChatConfig.switchTabFn = function (tabName) { ... };
ChatConfig.playVideoTransitionFn = function (videoUrl, muted) { ... };
initChat();
```

Other hooks core.js checks: `ChatConfig.isTextOnly`, `ChatConfig.sendGateFn`,
`ChatConfig.signupRequiredFn`, `ChatConfig.signInRequiredFn`.

Write theme behaviour through these hooks. Do not copy core.js into a theme, and do not
add a theme-specific branch inside core.js.

## Persian, RTL and bilingual text

- The document is `<html lang="fa" dir="rtl">`. English is a runtime switch, not a
  separate page: `setLang()` in core.js flips `document.documentElement.lang`.
- All visitor-facing copy lives in the `I18N` table in `static/chat/core.js`, keyed
  `fa` and `en`. Static markup gets `data-i18n="key"` (text) or `data-i18n-title="key"`
  (title and aria-label). core.js re-localises those on every language switch.
- Hardcoding Persian in a partial is a real bug this repo has already shipped and fixed.
  An English visitor read Persian, and an English screen reader announced Persian. See
  `tests/e2e/test_chat_localisation.py`.
- Font is Vazirmatn, loaded from `static/vendor/vazirmatn/`. Use
  `var(--font-family, "Vazirmatn", sans-serif)`, never a raw font stack.
- Use logical CSS where you can (`padding-inline-start`, `margin-inline-end`). When you
  must use physical sides, remember the page is RTL and check both directions.
- Digits: the UI mixes Persian and Latin digits. Match the surrounding screen.

## Escaping: the chat template env has autoescape OFF

`app/services/themes.py` builds the theme Jinja2 environment with `autoescape=False`.
Every value handed into a theme template must already be safe. That is why
`chat_branding_context()` pre-escapes everything it returns.

Three different escapings, for three different positions:

| Position | Correct escaping | Why |
|---|---|---|
| HTML text or attribute | `html.escape(v, quote=True)` | normal case |
| Inside `<style>` | CSS string escaping plus a `</` guard (`_css_url`) | `<style>` is raw text, entities are not decoded there |
| Inside `<script>` | `json.dumps` plus `</` to `<\/` | entities are not decoded in a script block either |

Put a raw value into a theme template and you have shipped stored XSS. The admin
environment has `autoescape=True` and takes raw values, so never pre-escape there.

## Admin UI

- Tabler UI on Bootstrap 5 RTL. Vendored under `static/vendor/`. No CDN.
- `templates/admin/base.html` is the shell, `templates/admin/layout.html` the sidebar
  plus content wrapper. Pages extend `layout.html`.
- The sidebar is `<aside class="navbar navbar-vertical" data-bs-theme="dark">`. Links are
  `.nav-item` with `{% if active_page == '...' %}active{% endif %}` and a FontAwesome
  icon in `.nav-link-icon`.
- Admin brand colours are plain values in `static/admin/css/variables.css`
  (`--primary-color`, `--secondary-color`, `--success-color`, `--info-color`,
  `--warning-color`, `--danger-color`, `--dark-sidebar`, `--light-bg`, `--card-shadow`).
  The admin panel is **not** white-labelled beyond the app name (`{{ wl_app_name }}`).
- Page JS goes in `static/admin/js/<page>.js`. Shared helpers already exist in
  `utils.js`, `state.js`, `auth.js`, `pager.js`. Use `pager.js` for any list, because the
  constitution requires pagination on unbounded collections.
- A link to a module page must be wrapped in the module's `{% if %}` gate. An install
  that did not buy the module must not see a link that 404s.

### Adding an admin page

1. `templates/admin/<name>.html` extending `layout.html`.
2. `static/admin/js/<name>.js`.
3. A route in `app/routers/public.py` (page) plus API routes in the owning router.
4. A sidebar link in `templates/admin/layout.html`, module-gated if optional.

Non-technical staff use this panel. The 3-second and 3-click rules apply here too.

## Before you call it done

1. `python -m py_compile` on any Python you touched.
2. Check every state: loading, empty, success, error, disabled, long content, many rows.
3. Check narrow width. Phone is the common case at an exhibition booth.
4. Keyboard: tab order, visible focus (`--color-focus-ring`), Enter and Escape.
5. Check both languages if the chat UI changed. Switch with the EN button.
6. Check light mode if the chat UI changed. See the `dark-mode` skill.
7. Drive it in a real browser. See the `playwright-cli` skill, and put regression tests
   in `tests/e2e/` using Playwright's **async** api.
8. Run the `docs/engineering/UI_UX.md` review list and fix what it finds.

## Do not

- Do not write a hex colour into a rule. Use a token.
- Do not add a gradient or shadow inline in markup. Define it as a token in the theme's
  `:root` (or use `--raised-depth`) and reference it.
- Do not put visual CSS in `static/chat/base.css` or layout CSS in a theme file.
- Do not add a theme-specific branch to `core.js`. Use a `ChatConfig` hook.
- Do not hardcode user-facing text in a partial. Add an `I18N` key and `data-i18n`.
- Do not introduce React, Tailwind, a bundler, or an npm dependency. There is none here
  and adding one is an architecture decision, not a UI change.
- Do not add inline code comments that only restate the code. If you find some, remove
  them. The comments already in this repo explain **why** something is the way it is, and
  several of them record a bug that came back. Read them before deleting one.
