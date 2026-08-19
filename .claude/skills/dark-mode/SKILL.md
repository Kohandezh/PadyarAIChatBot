---
name: dark-mode
description: Implement dark or alternate theming in the PadyarAIChatbot. For the public chat UI you build a dark variant as a THEME (or parent/child theme) whose style.css overrides visual properties — not a CSS class toggle. For the admin panel you use Tabler's data-bs-theme="dark" plus CSS variables. Tie colors to the whitelabel_* settings / /theme.css custom properties so dark values stay configurable, not hardcoded. Provide the target surface and optionally a reference screenshot.
---

This project has **no Tailwind, no globals.css, no Figma, and no React class-toggle dark mode.** "Dark mode" here means producing a dark *theme* on the chat surface, or flipping Tabler into its dark variant on the admin surface. Pick the surface first, then follow the matching recipe. Keep all colors tied to variables so a customer's white-label settings still control them.

## Surface A — Public chat: dark mode IS a theme

The chat UI uses a WordPress-style theme system (`themes/<name>/`, partials resolved child → parent → base). There is **no dark CSS class to toggle** — a dark look is a separate theme whose `static/style.css` overrides visual properties (backgrounds, text colors, borders, shadows). Structural CSS stays shared in `static/chat/base.css`; you only override visuals.

You have two clean approaches:

**1. A dark child theme (recommended when a light theme already exists).**

- Create `themes/<name>-dark/` with `theme.json` setting `"parent": "<light-theme>"` (e.g. parent `liquid-glass`), a `static/style.css` with the dark visual overrides, and a `screenshot.png`.
- Because of inheritance, you only re-declare the partials/visuals that change. Most of the work is dark color values in `style.css`.
- Auto-discovered at startup; activate it from the admin **Themes** page (active theme is stored in the SQLite `settings` table).

**2. A dark variant inside an existing theme** — adjust that theme's own `static/style.css` if the theme is meant to be dark by default. Don't add a `.dark` body class toggle; that's not how this system distributes looks.

**Tie dark colors to variables, don't hardcode.** Branding lives in the SQLite `settings` table under `whitelabel_*` keys (e.g. `whitelabel_primary_color`, `whitelabel_accent_color`, `whitelabel_sidebar_color`) and is exposed as CSS custom properties by the FastAPI `/theme.css` endpoint (`--brand-primary`, `--brand-accent`, `--brand-sidebar`, …). In the dark theme's `style.css`, reference `var(--brand-primary)` / `var(--brand-accent)` for accents so the customer's configured colors still drive the dark theme. Only the dark *surface* colors (page background, bubble backgrounds, text) are theme-local. Example:

```css
body { background: #0f1115; color: #e8e8ea; }
.message.bot { background: #1b1f27; }
.send-btn { background: var(--brand-primary, #4f46e5); }
```

Concrete files for a dark child theme:

- `themes/<name>-dark/theme.json` → `{ "name": "...", "parent": "liquid-glass" }`
- `themes/<name>-dark/static/style.css` → dark visual overrides
- `themes/<name>-dark/partials/` → only partials that differ (often none)
- `themes/<name>-dark/screenshot.png`

## Surface B — Admin panel: Tabler dark

The admin panel is Tabler (Bootstrap 5 RTL) in `templates/admin/`. Tabler ships a built-in dark mode via the `data-bs-theme="dark"` attribute and its CSS variables — the sidebar already uses `data-bs-theme="dark"` (Tabler's `navbar-vertical`).

To darken admin chrome:

- Set `data-bs-theme="dark"` on the relevant element (e.g. `<html>` or `<body>` in `templates/admin/layout.html`) so Tabler's dark variables apply to standard Bootstrap markup.
- Drive brand-colored elements through `var(--tblr-primary)` and the vars in `static/admin/css/variables.css` (`--primary-color`, `--dark-sidebar`, etc.); `static/admin/css/base.css` binds the brand color to `--tblr-primary`. Don't hardcode hex values — adjust the variables so white-label settings still win.
- Keep the toggle simple per the grandmother test: prefer a single clear control (or follow the active brand/theme setting) over a pile of options.

## Reference input

After invoking this skill, provide:

1. The target surface (a chat theme name to darken / create, or "admin panel").
2. Optionally, a **reference screenshot or mockup** of the desired dark look (no Figma in this project).

## Rules

- Chat dark mode = a theme (parent/child or its own), **never** a `.dark` class toggle.
- Override only **visual** properties in the theme's `static/style.css`; leave structure in `static/chat/base.css`.
- Tie accent/brand colors to `var(--brand-primary)` / `var(--brand-accent)` (chat) and `var(--tblr-primary)` / `variables.css` vars (admin) so `whitelabel_*` settings stay in control — dark values configurable, not hardcoded.
- Admin dark = Tabler `data-bs-theme="dark"` + CSS variables.
- Respect RTL and the Vazirmatn font.
- Keep it grandmother-simple — one obvious way to be in dark mode, not a maze of settings.
- NEVER add inline comments to code. If you see unnecessary inline comments, remove them as part of your cleanup.
