# Idle-time avatar video

## Problem

The avatar's idle loop (`data-waiting-src` on `#avatar-video`) was hardcoded
per theme — `themes/inotex/partials/video.html` and `themes/haj/partials/video.html`
both pointed at the literal path `/media/videos/idle.mp4`. A customer who
wanted a different clip had to rename a file on disk; there was no admin
control, and no way to show more than one clip.

## What shipped

Settings → دستیار هوشمند → «ویدیوهای حالت انتظار»: an admin uploads one main
idle clip plus up to 3 extras. While nobody is chatting, the avatar loops the
main clip and — if extras are configured — core.js swaps to a random pick
from the pool every 30s, so the "avatar waiting for a question" loop is not
frame-identical on every visit.

## Design decisions

- **Storage**: two `settings` rows, `idle_video_main` (string) and
  `idle_video_extra` (JSON array, capped at 3) — same key-value pattern as
  `whitelabel_*` (`app/services/branding.py`), not a new table. See
  `app/services/idle_video.py`.
- **Upload reuse**: no new upload endpoint. `/admin/api/upload_video`
  (`app/routers/dataset.py`, gated by the `video` module) already does the
  MIME + magic-byte validation; the idle-video settings just reference a
  `video_url` it returned. `is_valid_video_url()` re-checks the referenced
  file actually sits in `VIDEO_DIR` before a save is accepted — the value is
  emitted raw (pre-escaped) into the public chat page, so it can never become
  an arbitrary URL.
- **Orphan safety**: deleting a video through `/admin/api/videos/{filename}`
  (`dataset.delete_video`) now also calls `idle_video.forget_video()`, so a
  removed upload can never linger as a broken idle clip.
- **Page cache**: the chat shell is cached per theme (`app/services/themes.py`,
  keyed by `wl_cache_key` for branding). The idle-video setup rides the same
  contract — `idle_video_cache_key()` is folded into both cache-key tuples —
  so an admin save is visible on the very next render, same as a branding
  save.
- **Rendering contract**: the theme Jinja env runs `autoescape=False`
  (`app/services/themes.py`), so `idle_video_context()` pre-escapes every
  value itself, exactly like `branding.chat_branding_context()`.
- **Backward compatibility**: `themes/inotex` and `themes/haj` keep their
  historical default (`{{ idle_video_main or '/media/videos/idle.mp4' }}`) —
  an install that never opens this settings section sees no change.
  `themes/base` (inherited by `minimal` and `liquid-glass`) had no default
  idle clip at all; it now honors the same setting with an empty default, so
  those themes gain the feature for free without changing their current
  (idle-video-less) behaviour when unset.
- **Rotation lives in core.js**, not per-theme JS: `startIdlePoolRotation()`
  reads `data-idle-pool` (JSON array, main + extras) off `#avatar-video` and,
  every 30s while `isResponsePlaying` is false, swaps `data-waiting-src` (and
  `.src`) to a random pool member. A pool of 0 or 1 entries is a no-op. Themes
  that already read `data-waiting-src` to restore the idle loop after an
  answer (inotex/haj footers) pick up the rotated clip automatically — no
  theme JS changed.
- **Admin UI**: 4 fixed boxes (main + 3 extra) in `templates/admin/settings_ai.html`,
  gated by `{% if 'video' in enabled_modules %}`. The extra boxes fill
  left-to-right and stay compact on removal (`static/admin/js/settings.js`) —
  there is no such thing as "extra slot 2 filled, slot 1 empty" in storage,
  so the UI enforces the same shape by locking slot N+1 until slot N has a
  clip.

## Files touched

- `app/services/idle_video.py` (new) — storage, validation, cache key,
  template context
- `app/models.py` — `IdleVideosRequest`
- `app/routers/admin.py` — `GET`/`POST /admin/api/idle-videos`
- `app/routers/dataset.py` — `delete_video` forgets a removed file
- `app/routers/public.py` — merges `idle_video_context()` into the chat render
- `app/services/themes.py` — cache-key tuples carry `idle_video_cache_key`
- `themes/base|inotex|haj/partials/video.html` — `data-waiting-src` /
  `data-idle-pool` from context instead of a hardcoded literal
- `static/chat/core.js` — `startIdlePoolRotation()` / `getIdlePool()`
- `templates/admin/settings_ai.html`, `static/admin/js/settings.js` — the
  admin card
- `tests/test_idle_video.py` — service, API, rendering-and-cache, and
  delete-forgets-it coverage
