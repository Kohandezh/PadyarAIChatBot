"""Companion (pet) characters — the mascot is a setting, not a hardcoded
asset list (owner request, 2026-08-31: elecomp ships its own character).

Covers the whole reader–writer chain:
  * the registry discovers the bundled characters and rejects nothing valid
  * the chat page carries the active character's atlas/pose maps (and the
    rendered-page cache flips the moment the setting changes)
  * the admin API lists/saves/rejects, admin-only
  * companion.js consumes per-character columns + pose maps and never
    blanks on an unmapped pose
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "pet.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
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


# ── Registry ────────────────────────────────────────────────────────────

def test_registry_discovers_both_bundled_characters():
    from app.services.pet_characters import discover_characters
    characters = discover_characters()
    assert set(characters) >= {"inotex", "elecomp"}
    # The inotex entry keeps pointing at the flat HD assets the markup
    # hardcoded before this feature — default pixels unchanged.
    assert characters["inotex"]["atlas"] == "/static/otp/pet/inotex-pose-atlas-hd.webp"
    assert characters["inotex"]["cell"] == 512
    assert characters["inotex"]["columns"] == 4
    # The elecomp sheet is 3 columns wide at 384px — the layout data the
    # hardcoded COLS=4 could never express.
    assert characters["elecomp"]["columns"] == 3
    assert characters["elecomp"]["cell"] == 384
    assert characters["elecomp"]["state_poses"]["success"] == "flight-soar"


def test_unknown_stored_character_falls_back_to_the_default():
    from app.db.queries import set_setting
    from app.services.pet_characters import get_pet_character
    set_setting("pet_character", "does-not-exist")
    assert get_pet_character()["name"] == "inotex"


def test_default_is_inotex(client):
    html = client.get("/").text
    assert 'data-atlas="/static/otp/pet/inotex-pose-atlas-hd.webp"' in html
    assert 'data-cell="512"' in html
    assert 'data-columns="4"' in html
    assert 'data-hide-strip="/static/otp/pet/inotex-hide-strip.webp"' in html
    assert "welcome-wave" in html  # the inotex greet pose ships as data


# ── Render + cache ──────────────────────────────────────────────────────

def test_switching_the_character_flips_the_cached_shell(client):
    _login(client)
    assert client.post("/admin/api/pet-character",
                       json={"character": "elecomp"}).status_code == 200
    html = client.get("/").text
    assert 'data-atlas="/static/otp/pet/characters/elecomp/elecomp-pose-atlas.webp"' in html
    assert 'data-cell="384"' in html
    assert 'data-columns="3"' in html
    # The bird has no hide strip of its own — the attribute is present but
    # empty, and companion.js treats empty as "instant hide" (its own rule).
    assert 'data-hide-strip=""' in html
    # Its state map rides the page: success soars, errors wings-up.
    assert "flight-soar" in html
    assert "front-wings" in html
    # The cached shell flipped on the SAME process — cache key carries the
    # character identity (themes.py).
    assert "inotex-pose-atlas-hd" not in html


# ── Admin API ───────────────────────────────────────────────────────────

def test_pet_character_api_lists_and_saves(client):
    _login(client)
    r = client.get("/admin/api/pet-character")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] == "inotex"
    names = {c["name"] for c in body["characters"]}
    assert {"inotex", "elecomp"} <= names
    elecomp = next(c for c in body["characters"] if c["name"] == "elecomp")
    assert elecomp["preview"].endswith(".jpg")

    assert client.post("/admin/api/pet-character",
                       json={"character": "elecomp"}).status_code == 200
    assert client.get("/admin/api/pet-character").json()["current"] == "elecomp"


def test_pet_character_api_rejects_unknown(client):
    _login(client)
    r = client.post("/admin/api/pet-character", json={"character": "../evil"})
    assert r.status_code == 400


def test_pet_character_api_requires_admin(client):
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/api/pet-character").status_code in (401, 403)
        r = anon.post("/admin/api/pet-character", json={"character": "elecomp"})
        assert r.status_code in (401, 403)


# ── The renderer's contract (static) ────────────────────────────────────

def test_companion_js_reads_per_character_layout_and_maps():
    """The shared renderer must not assume the INOTEX grid: columns, pose
    indices and state→pose come from the character's data attributes, and
    an unmapped pose degrades to idle instead of drawing nothing."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent
          / "static" / "companion" / "companion.js").read_text(encoding="utf-8")
    assert "dataset.columns" in js
    assert "_poseJson('poseIndex')" in js
    assert "_poseJson('poses')" in js
    assert "POSE['idle-neutral']" in js  # the never-blank fallback
