"""Idle-time avatar video — the loop the avatar plays while nobody is
chatting (Settings → دستیار هوشمند → «ویدیوهای حالت انتظار»).

Covers:
  * app/services/idle_video.py: get/set roundtrip, is_valid_video_url only
    accepting a file that actually sits in VIDEO_DIR, forget_video, the
    rendered-page cache key
  * /admin/api/idle-videos: auth, the video-module gate, the 3-extra cap,
    rejecting a URL that was never uploaded, accepting one that was
  * the chat page: data-waiting-src / data-idle-pool render from settings
    (with the theme's historical default when nothing is configured),
    values are escaped, and a save flips the page cache (same contract as
    branding — see test_branding.py)
  * app/routers/dataset.py delete_video: removing the file also drops it
    from the idle-video setup so a broken clip can never linger

Each test runs against a throwaway SQLite DB and a throwaway VIDEO_DIR, and
logs in the same way as test_branding.py (a real admin session row + the
CSRF header test_sms_settings.py established the pattern for).
"""
import datetime
import json
import os
import re
import secrets

import pytest
from fastapi.testclient import TestClient

_TOKEN_RE = re.compile(r'<meta name="chat-token" content="([^"]+)"')

# Smallest header upload_video's magic-byte check accepts for "webm/mkv",
# padded past its 12-byte minimum read.
_WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 12


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "idle_video.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    # idle_video.py reads VIDEO_DIR off app.config lazily (inside the
    # function body), so patching the config module is enough for it.
    monkeypatch.setattr(config, "VIDEO_DIR", str(video_dir))
    # app/routers/dataset.py imports VIDEO_DIR at module load time, so its
    # own binding needs patching too — only the delete_video test exercises
    # that path, but it is harmless to patch here for every test.
    import app.routers.dataset as dataset_router
    monkeypatch.setattr(dataset_router, "VIDEO_DIR", str(video_dir))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, "tester", expiry.isoformat()),
    )
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    from app.auth.csrf import token_for_session
    client.headers.update({'X-CSRF-Token': token_for_session(token)})
    return token


def _drop_in(video_dir: str, filename: str) -> str:
    """Put a file straight into VIDEO_DIR — standing in for an upload
    already done through /admin/api/upload_video — and return its URL."""
    with open(os.path.join(video_dir, filename), "wb") as f:
        f.write(_WEBM_BYTES)
    return f"/media/videos/{filename}"


# ── 1. Service unit tests ───────────────────────────────────────────────

def test_get_idle_videos_defaults_empty(client):
    from app.services import idle_video
    assert idle_video.get_idle_videos() == {"main": "", "extra": []}


def test_set_and_get_roundtrip(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    main = _drop_in(VIDEO_DIR, "main.webm")
    extra = [_drop_in(VIDEO_DIR, f"extra-{i}.webm") for i in range(3)]
    idle_video.set_idle_videos(main, extra)
    assert idle_video.get_idle_videos() == {"main": main, "extra": extra}


def test_set_idle_videos_caps_extra_at_max(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    urls = [_drop_in(VIDEO_DIR, f"x-{i}.webm") for i in range(5)]
    idle_video.set_idle_videos("", urls)
    assert len(idle_video.get_idle_videos()["extra"]) == idle_video.IDLE_VIDEO_EXTRA_MAX


def test_is_valid_video_url_only_accepts_an_uploaded_file(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    real = _drop_in(VIDEO_DIR, "real.webm")
    assert idle_video.is_valid_video_url(real) is True
    assert idle_video.is_valid_video_url("/media/videos/never-uploaded.webm") is False
    assert idle_video.is_valid_video_url("") is False
    # Never a path-traversal or an external URL — this value is emitted raw
    # into the public chat page.
    assert idle_video.is_valid_video_url("/media/videos/../../etc/passwd") is False
    assert idle_video.is_valid_video_url("https://evil.example.com/x.mp4") is False


def test_forget_video_clears_main_and_extra(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    main = _drop_in(VIDEO_DIR, "main.webm")
    e1 = _drop_in(VIDEO_DIR, "e1.webm")
    e2 = _drop_in(VIDEO_DIR, "e2.webm")
    idle_video.set_idle_videos(main, [e1, e2])

    idle_video.forget_video(e1)
    assert idle_video.get_idle_videos() == {"main": main, "extra": [e2]}

    idle_video.forget_video(main)
    assert idle_video.get_idle_videos() == {"main": "", "extra": [e2]}

    # A URL that was never in use is a no-op, not an error.
    idle_video.forget_video("/media/videos/unrelated.webm")
    assert idle_video.get_idle_videos() == {"main": "", "extra": [e2]}


def test_idle_video_cache_key_changes_with_content(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    before = idle_video.idle_video_cache_key()
    idle_video.set_idle_videos(_drop_in(VIDEO_DIR, "m.webm"), [])
    after = idle_video.idle_video_cache_key()
    assert before != after


# ── 2. Admin API ────────────────────────────────────────────────────────

def test_idle_videos_api_requires_admin(client):
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/api/idle-videos").status_code in (401, 403)
        assert anon.post("/admin/api/idle-videos", json={"main": "", "extra": []}).status_code in (401, 403)


def test_idle_videos_api_roundtrip(client):
    from app.config import VIDEO_DIR
    _login(client)
    assert client.get("/admin/api/idle-videos").json() == {"main": "", "extra": []}

    main = _drop_in(VIDEO_DIR, "main.webm")
    extra = [_drop_in(VIDEO_DIR, f"e{i}.webm") for i in range(3)]
    r = client.post("/admin/api/idle-videos", json={"main": main, "extra": extra})
    assert r.status_code == 200, r.text

    assert client.get("/admin/api/idle-videos").json() == {"main": main, "extra": extra}


def test_idle_videos_api_rejects_more_than_three_extra(client):
    from app.config import VIDEO_DIR
    from app.services.idle_video import IDLE_VIDEO_EXTRA_MAX
    _login(client)
    urls = [_drop_in(VIDEO_DIR, f"e{i}.webm") for i in range(IDLE_VIDEO_EXTRA_MAX + 1)]
    r = client.post("/admin/api/idle-videos", json={"main": "", "extra": urls})
    assert r.status_code == 400
    assert r.json()["detail"]
    # Nothing written.
    assert client.get("/admin/api/idle-videos").json() == {"main": "", "extra": []}


def test_idle_videos_api_rejects_a_url_never_uploaded(client):
    _login(client)
    r = client.post("/admin/api/idle-videos",
                     json={"main": "/media/videos/does-not-exist.webm", "extra": []})
    assert r.status_code == 400
    assert r.json()["detail"]
    assert client.get("/admin/api/idle-videos").json() == {"main": "", "extra": []}


def test_idle_videos_api_404s_when_video_module_disabled(client, monkeypatch):
    import app.routers.admin as admin_router
    monkeypatch.setattr(admin_router, "is_module_enabled", lambda name: False)
    _login(client)
    assert client.get("/admin/api/idle-videos").status_code == 404
    r = client.post("/admin/api/idle-videos", json={"main": "", "extra": []})
    assert r.status_code == 404


# ── 3. Chat page rendering ──────────────────────────────────────────────

def test_chat_renders_theme_default_when_unset(client):
    # No admin setup at all: the inotex theme's historical hardcoded clip,
    # unchanged — an install that never opens this settings section sees no
    # behaviour change.
    html = client.get("/").text
    assert 'data-waiting-src="/media/videos/idle.mp4"' in html
    assert 'data-idle-pool="[]"' in html


def test_chat_renders_configured_idle_videos(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    _login(client)
    main = _drop_in(VIDEO_DIR, "main.webm")
    extra = [_drop_in(VIDEO_DIR, f"e{i}.webm") for i in range(2)]
    idle_video.set_idle_videos(main, extra)

    html = client.get("/").text
    assert f'data-waiting-src="{main}"' in html
    pool_json = json.dumps([main] + extra, ensure_ascii=True)
    import html as html_mod
    assert f'data-idle-pool="{html_mod.escape(pool_json, quote=True)}"' in html


def test_idle_video_save_invalidates_page_cache(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    _login(client)

    first = client.get("/").text
    assert 'data-waiting-src="/media/videos/idle.mp4"' in first

    main = _drop_in(VIDEO_DIR, "new-main.webm")
    idle_video.set_idle_videos(main, [])

    second = client.get("/").text
    assert f'data-waiting-src="{main}"' in second

    # Per-visitor token still spliced fresh even though the shell is cached.
    t1 = _TOKEN_RE.search(second).group(1)
    t2 = _TOKEN_RE.search(client.get("/").text).group(1)
    assert t1 != t2


def test_idle_video_values_are_escaped_in_chat_html(client):
    """idle_video_context() must pre-escape for the theme env's
    autoescape=False, exactly like branding.chat_branding_context() —
    otherwise a value containing a quote could break out of the attribute."""
    from app.services import idle_video
    tricky = '/media/videos/a"b.webm'
    from unittest.mock import patch
    with patch.object(idle_video, "get_idle_videos",
                       return_value={"main": tricky, "extra": []}):
        html = idle_video.idle_video_context()
    assert '"' not in html["idle_video_main"].replace("&quot;", "")


# ── 4. Deleting the file forgets it as an idle video ────────────────────

def test_deleting_the_video_file_forgets_it(client):
    from app.config import VIDEO_DIR
    from app.services import idle_video
    _login(client)
    main = _drop_in(VIDEO_DIR, "to-delete.webm")
    extra = _drop_in(VIDEO_DIR, "keep.webm")
    idle_video.set_idle_videos(main, [extra])

    r = client.delete("/admin/api/videos/to-delete.webm")
    assert r.status_code == 200, r.text

    assert idle_video.get_idle_videos() == {"main": "", "extra": [extra]}
    html = client.get("/").text
    assert "to-delete.webm" not in html
