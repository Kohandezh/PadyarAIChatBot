# Brand-Dynamic Shell

Status: Implemented (2026-08-30, owner request)
Owner: Sina (Malik-e product)

## Scenario

A second install (elecomp) runs the same inotex theme. The operator sets
their own name, subtitle and logo in **Settings > Branding** and the whole
visitor-facing shell rebrands — including the first thing anyone sees: the
startup preloader. No event name is baked into the theme's rendered markup.

## What shipped

The theme's rendered markup carries no hardcoded event name. Every visible
brand position reads the white-label settings (existing keys — no new ones):

| Position | Before | Now |
| --- | --- | --- |
| Preloader label | `اینوتکس` (literal) | `{{ wl_subtitle }}` |
| Preloader mark | animated hexagon SVG only | `whitelabel_logo_url` img if set, else the same SVG |
| Companion panel eyebrow / title | `راهنمای رسمی اینوتکس ۲۰۲۶` / `از اینوتکس بپرسید` | `… {{ wl_subtitle }}` (year dropped — install content, not chrome) |
| Companion rail / hit / input aria-labels | `… اینوتکس` | `… {{ wl_subtitle }}` |
| Sidebar built-in mark aria-label | `INOTEX` | `{{ app_title }}` |
| core.js EN welcome | `…the INOTEX exhibition…` | assembled from `PADYAR_BRAND.app_name` |
| core.js EN suggestion list | hardcoded 10-item INOTEX list (`EN_SUGGESTED`) | deleted — dead code, superseded by the admin question bank (`title_en`) |
| core.js fa welcome fallback | mentioned اینوتکس | generic fallback (real text lives in `whitelabel_welcome_text`) |
| Powered-by credit (input.html) | hardcoded `قدرت گرفته از…` + `--color-text-muted` | `{{ wl_footer_text }}` + `--wl-footer-color` — text AND colour from Settings > Branding («متن پایین صفحه» / «رنگ متن پایین صفحه»), defaults keep today's credit pixel-for-pixel |
| light-mode subtitle colour | `body.light-mode .header-subtitle { color: #B07E06; }` | rule deleted (owner request) — the subtitle rides the palette again |

The preloader logo follows the same rule as the sidebar mark (menu.html):
a set `whitelabel_logo_url` replaces the built-in hexagon — one identity,
not two. `img.inx-loader-mark` is styled in the theme CSS (84px, contain).

## What deliberately stayed

- **`app/default_content.py`, dataset, questions** — per-install knowledge
  content, not chrome. Each install seeds its own.
- **Code identifiers** — `themes/inotex/`, `.inx-*` classes, localStorage
  keys (`inotex_lang`…), asset paths, `/secure-panel-inotex` admin prefix:
  routing/technical identity, not display text.
- **`WL_DEFAULTS` values** — they ARE the settings defaults; the INOTEX
  install keeps its pixels, other installs edit the settings.
- **`theme.json` description** — describes the theme itself in the admin
  picker, correctly.
- **Pet art** (`static/otp/pet/inotex-*`) — the companion's own artwork.

## Tests

- `tests/test_public_ui.py::test_theme_shell_carries_no_hardcoded_event_name`
  — no `اینوتکس` in comment-stripped footer/menu markup; loader label is
  `wl_subtitle`; loader mark has the `wl_logo_url` conditional.
- `test_core_js_has_fa_en_i18n_and_switch` — asserts the EN list and the
  event-name welcome are gone.
- `tests/test_branding.py::test_brand_line_and_marks_follow_the_settings` —
  default renders `INOTEX` label + hexagon; a set logo + subtitle swap BOTH
  marks and the label on the rendered page.
