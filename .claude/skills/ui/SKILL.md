---
name: ui
description: Implement or adjust UI in the PadyarAIChatbot. Decide the surface — the WordPress-style chat THEME system (Jinja2 partials + per-theme style.css) or the admin panel built on Tabler/Bootstrap 5 RTL — then build it the project's way using brand CSS variables (var(--brand-primary), --tblr-primary, variables.css vars), RTL + Vazirmatn, and the grandmother-test simplicity bar. Provide the target surface/page and optionally a reference screenshot or mockup.
---

This project has **no React, no Tailwind, no Figma, no globals.css, and no shared component library.** It has two server-rendered UI surfaces, each with its own styling system. Your first job is to decide which one you're touching, then work strictly within its conventions.

## Product bar (applies to every change)

From `CLAUDE.md`: this app must be usable by anyone from a kid to an elderly person. Apply the "grandmother test" to every UI change:

- Every screen understandable in under 3 seconds. Every action in under 3 clicks.
- No jargon, no technical terms, no confusing settings in user-facing UI.
- Default everything to "just work." Hide advanced options behind a toggle.
- If a feature needs an explanation, simplify or remove it.

The entire UI is **Persian / RTL** and uses the **Vazirmatn** font (vendored at `static/vendor/vazirmatn/`). Never hardcode `dir="ltr"` or a Latin font, and write CSS that respects logical/RTL direction.

## Step 1 — pick the surface

| You're changing...                                            | Surface                | Styling system                                  |
| ------------------------------------------------------------- | ---------------------- | ----------------------------------------------- |
| The public chatbot the end-user talks to (bubbles, video, mic, header, welcome) | **Chat theme system**  | Jinja2 partials + per-theme `style.css`         |
| Any page behind admin login (dashboard, dataset, settings…)   | **Admin panel**        | Tabler (Bootstrap 5 RTL) markup + ES-module JS  |

If unsure, ask: does an end-user with no login see it? → chat theme. Does it live under `templates/admin/`? → admin.

## Surface A — Public chat (theme system)

Themes are WordPress-style. They live in `themes/<name>/`. `themes/base/partials/` holds the default Jinja2 partials; a theme **overrides** a partial by placing a same-named file in its own `partials/`. Jinja's `FileSystemLoader` resolves child-theme → (optional parent) → base. Current themes: `liquid-glass` (default, frosted glass) and `minimal`. The active theme is stored in the SQLite `settings` table and switched from the admin **Themes** page. Themes are auto-discovered at startup — no registration.

**Partials** (`themes/base/partials/`): `index.html` (master, assembles the rest via `{% include %}`), `head.html`, `header.html` (logo, tab switcher, accessibility controls), `messages.html` (text chat + welcome + loading bubble), `video.html` (video view + avatar + actions), `input.html` (textarea, mic, send), `footer.html` (loads core.js, sets ChatConfig overrides, calls `initChat()`).

**The three-bucket rule — put each change in the right place:**

1. **Structure / layout / positioning / animation** → `static/chat/base.css` (shared by every theme). Only touch this if the change is genuinely structural and should apply to all themes.
2. **Visual look** (colors, backgrounds, borders, shadows, blur) → the theme's own `static/style.css`. This is where 90% of theme work goes. Never put colors in `base.css`.
3. **Markup changes for one theme** → override the relevant partial in that theme's `partials/` directory. Leave `themes/base/partials/` as the neutral default.

**Chat JS** lives entirely in `static/chat/core.js`. Do not fork it per theme. A theme customizes behavior by setting callbacks on `ChatConfig` **before** calling `initChat()` in its `footer.html`:

- `ChatConfig.addMessageFn` — how a message bubble is rendered/appended
- `ChatConfig.switchTabFn` — text ↔ video tab switching
- `ChatConfig.playVideoTransitionFn` — the video transition animation

**Colors must come from variables, never literals.** Branding is stored in the SQLite `settings` table under `whitelabel_*` keys (hex), surfaced as CSS custom properties by the FastAPI `/theme.css` endpoint (`--brand-primary`, `--brand-accent`, `--brand-sidebar`, …). In a theme's `style.css`, reference `var(--brand-primary)` / `var(--brand-accent)` so the customer's white-label colors flow through. Add a fallback for safety, e.g. `color: var(--brand-primary, #4f46e5);`.

### Adding a new theme (mirrors CLAUDE.md)

1. Create `themes/<name>/` with `theme.json`, `screenshot.png`, and `static/style.css`.
2. Optionally add `partials/` with override files for any partial that differs from base.
3. To inherit another theme instead of base, add `"parent": "<theme>"` to `theme.json`.
4. It's auto-discovered at startup — no registration. Activate it from the admin Themes page.
5. For JS tweaks, set the `ChatConfig.*` callbacks in the theme's `footer.html` before `initChat()`.

## Surface B — Admin panel (Tabler / Bootstrap 5 RTL)

Admin pages are server-rendered Jinja2 templates in `templates/admin/`, extending `layout.html` (sidebar + main content). Styling is **Tabler**, vendored at `static/vendor/tabler/css/tabler.rtl.min.css`. Tabler natively styles standard Bootstrap markup — `.btn`, `.form-control`, `.table`, `.card`, `.modal`, etc. — so **write plain Bootstrap markup and let Tabler style it.** Don't reinvent components with custom CSS. The sidebar uses Tabler's `navbar-vertical` with `data-bs-theme="dark"`.

Admin JS is **ES modules** in `static/admin/js/` (one file per page). Admin brand variables live in `static/admin/css/variables.css` (e.g. `--primary-color`, `--dark-sidebar`, `--card-shadow`); `static/admin/css/base.css` is a thin bridge that applies Vazirmatn and binds the brand color to Tabler's `--tblr-primary`. Use `var(--tblr-primary)` (or the `variables.css` vars) for brand-colored elements — never hardcode a hex.

### Adding a new admin page (mirrors CLAUDE.md)

1. Create `templates/admin/<name>.html` extending `layout.html`, using Tabler/Bootstrap markup.
2. Create `static/admin/js/<name>.js` as an ES module for page logic.
3. Add a route to serve the page (in `app/routers/public.py` or the appropriate router).
4. Add a sidebar link in `templates/admin/layout.html`.

Branding (`{{ app_name }}`, `{{ logo_url }}`, `{{ branding_css }}`) is injected into every template via the `branding_context` context processor — use those variables, don't re-read settings in the template.

## Reference input

After invoking this skill, provide:

1. The target surface or page (theme name / partial path, or admin page name / template path).
2. Optionally, a **reference screenshot or mockup image** to match (this project has no Figma).

## Rules

- Use brand CSS variables (`var(--brand-primary)`, `var(--brand-accent)`, `var(--tblr-primary)`, `variables.css` vars) instead of hardcoded colors so white-label settings flow through.
- Keep structural CSS in `static/chat/base.css`; keep visual CSS in the theme's `static/style.css`. Don't blur the two.
- Don't fork `static/chat/core.js` per theme — use `ChatConfig` callbacks.
- Respect RTL and Vazirmatn everywhere.
- Prefer Tabler's native Bootstrap styling over custom admin CSS.
- Apply the grandmother test before shipping.
- NEVER add inline comments to code. If you see unnecessary inline comments, remove them as part of your cleanup.
