# Branding Tab Backgrounds

Status: Implemented (2026-08-30, owner request)
Owner: Sina (Malik-e product)

## Scenario

An operator at a fresh install wants the chatbot to feel like their own
event. They open **Settings > Branding**, upload one photo for behind the
conversation and one for behind the avatar video (or the same photo for
both), press «ذخیره برندینگ», and the very next visitor sees the new
backgrounds — no deploy, no theme edit. A visitor arriving at the kiosk
lands on the **video tab** first, and the video stage shows the background
and the avatar video, nothing else.

## What shipped

1. **The background moved from `.app-layout` to `.view-container`**
   (`themes/inotex/static/style.css`). The header and the composer keep
   their own panel surfaces; the photo frames the view area. Light mode
   keeps its frosted wash on the same selector (no photo in daylight —
   unchanged pixels).
2. **Two new white-label keys** (see `app/services/branding.py`,
   `WL_DEFAULTS`):
   - `whitelabel_chat_background_url` — behind the chat tab
   - `whitelabel_video_background_url` — behind the video tab
   Both default to `/themes/inotex/static/bg-bricks.jpg` (pixel-identical
   install). Emitted in `wl_style` as `--wl-chat-background` /
   `--wl-video-background` CSS `url("…")` tokens (CSS-string escaped, not
   html.escape — `<style>` is raw text). The theme paints them via
   `--inotex-chat-bg` / `--inotex-video-bg` tokens; the video tab overrides
   under `body.video-mode`.
3. **Admin form** (`templates/admin/settings_branding.html` +
   `static/admin/js/settings.js`): two URL fields with an upload button and
   a live preview each, exactly the logo pattern. Uploads ride the existing
   `/admin/api/upload_logo` (magic-byte validated, 2 MB cap). Validation on
   save follows the logo rule: site-relative or absolute `http(s)`, never
   protocol-relative.
4. **Video tab is the landing tab**: `video.html` carries `tab-view
   active`, `messages.html` does not, and the header's checked radio is
   «ویدیو». `core.js` reads the markup (`initialView`) and confirms — no
   JS change needed.
5. **The video placeholder card is gone** — markup (inotex + base
   partials), the `.video-placeholder*` CSS, the show/hide JS in
   `footer.html`, and the now-unused `videoReady`/`videoReadyHint` i18n
   strings in `core.js`.

## Reader–writer pairs (all closed)

- setting key ↔ branding form field ↔ `wl_style` token ↔ theme CSS consumer
- cache: new keys join `wl_cache_key()` automatically → an admin save flips
  the rendered-page cache key
- upload button ↔ `/admin/api/upload_logo` ↔ URL field ↔ save POST body

## Tests

- `tests/test_public_ui.py` — background tokens + `.view-container` target,
  landing tab markup, placeholder fully removed
- `tests/test_branding.py` — roundtrip (14 keys), URL validation for both
  fields, `--wl-*` url() tokens rendered on `/`, admin form prefill

## Known bounds (deliberate)

- Light mode keeps its frosted wash; a custom background shows in dark
  mode (the install's default look). If the owner wants custom backgrounds
  in light mode too, that is a follow-up.
- Empty field collapses to the shipped photo (same contract as the welcome
  text) — there is no "no image at all" state.
