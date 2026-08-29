"""Idle-time avatar video: what the avatar plays while nobody is chatting.

One main clip is the default idle loop, plus up to IDLE_VIDEO_EXTRA_MAX
administrator-uploaded clips that core.js rotates through at random while the
avatar is waiting — so a booth visitor does not see the exact same loop on
every visit. Storage mirrors the whitelabel_* pattern (app/services/branding.py):
key-value rows in `settings`, JSON for the list.

A value can only be a video already uploaded through /admin/api/upload_video
(app/routers/dataset.py) — is_valid_video_url checks it against VIDEO_DIR — so
this module can never be made to emit an arbitrary URL into the public chat
page.
"""
import html
import json
import os

from app.db.queries import get_setting, set_setting

IDLE_VIDEO_EXTRA_MAX = 3

_KEY_MAIN = "idle_video_main"
_KEY_EXTRA = "idle_video_extra"  # JSON list, at most IDLE_VIDEO_EXTRA_MAX items


def get_idle_videos() -> dict:
    """Current setup, raw and unescaped: {'main': str, 'extra': [str, ...]}."""
    main = get_setting(_KEY_MAIN, "") or ""
    raw = get_setting(_KEY_EXTRA, "") or ""
    try:
        extra = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        extra = []
    if not isinstance(extra, list):
        extra = []
    extra = [u for u in extra if isinstance(u, str) and u][:IDLE_VIDEO_EXTRA_MAX]
    return {"main": main, "extra": extra}


def is_valid_video_url(url: str) -> bool:
    """True only for a URL that names a file actually sitting in VIDEO_DIR —
    i.e. something uploaded through /admin/api/upload_video."""
    from app.config import VIDEO_DIR, VIDEO_BASE_URL, ALLOWED_VIDEO_EXTENSIONS
    if not url or not url.startswith(VIDEO_BASE_URL + "/"):
        return False
    filename = url[len(VIDEO_BASE_URL) + 1:]
    if not filename or "/" in filename or filename in (".", ".."):
        return False
    if os.path.splitext(filename)[1].lower() not in ALLOWED_VIDEO_EXTENSIONS:
        return False
    return os.path.isfile(os.path.join(VIDEO_DIR, filename))


def set_idle_videos(main: str, extra: list) -> None:
    set_setting(_KEY_MAIN, main)
    set_setting(_KEY_EXTRA, json.dumps(extra[:IDLE_VIDEO_EXTRA_MAX], ensure_ascii=True))


def forget_video(video_url: str) -> None:
    """Drop a video from the idle setup. Called when the file itself is
    deleted (app/routers/dataset.py:delete_video), so a removed upload can
    never linger as a broken idle clip on the public page."""
    if not video_url:
        return
    d = get_idle_videos()
    changed = False
    main = d["main"]
    if main == video_url:
        main = ""
        changed = True
    extra = d["extra"]
    if video_url in extra:
        extra = [u for u in extra if u != video_url]
        changed = True
    if changed:
        set_idle_videos(main, extra)


def idle_video_cache_key() -> tuple:
    """Identity of the current idle-video setup for the rendered-page cache
    (see app/services/themes.py) — mirrors branding.wl_cache_key."""
    d = get_idle_videos()
    return (d["main"], tuple(d["extra"]))


def idle_video_context() -> dict:
    """Template context for the chat page render.

    The theme Jinja env is built with autoescape=False (app/services/themes.py),
    so every value here is pre-escaped for raw emission into an HTML attribute
    — same contract as branding.chat_branding_context().
    """
    d = get_idle_videos()
    pool = ([d["main"]] if d["main"] else []) + d["extra"]
    return {
        "idle_video_main": html.escape(d["main"], quote=True),
        "idle_video_pool_json": html.escape(
            json.dumps(pool, ensure_ascii=True), quote=True),
        "idle_video_cache_key": idle_video_cache_key(),
    }
