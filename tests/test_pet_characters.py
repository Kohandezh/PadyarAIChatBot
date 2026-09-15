"""Companion (pet) characters — the mascot is a setting, not a hardcoded
asset list (owner request, 2026-08-31: an event install shipped its own
character).

Covers the whole reader–writer chain:
  * the registry discovers the bundled characters and rejects nothing valid
  * the chat page carries the active character's atlas/pose maps (and the
    rendered-page cache flips the moment the setting changes)
  * the admin API lists/saves/rejects, admin-only
  * companion.js consumes per-character columns + pose maps and never
    blanks on an unmapped pose

No character name is hardcoded here: the default is computed as the
registry's first name, and integrity is asserted generically for every
bundled character.
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


def _registry():
    from app.services.pet_characters import discover_characters
    return discover_characters()


def _default_name():
    """The computed default: the registry's first name in sorted order —
    discover_characters() sorts, and dict order preserves it."""
    return next(iter(_registry()))


# ── Registry ────────────────────────────────────────────────────────────

def test_registry_holds_every_bundled_character_folder():
    """The bundled white-label assets stay selectable: every character
    folder shipped under static/otp/pet/characters/ loads into the
    registry (none is rejected), and both bundled folders are there."""
    import os
    from app.services.pet_characters import CHARACTERS_DIR
    on_disk = {e for e in os.listdir(CHARACTERS_DIR)
               if os.path.isdir(os.path.join(CHARACTERS_DIR, e))}
    assert len(on_disk) >= 2  # the two bundled character folders
    assert on_disk == set(_registry())


def test_every_bundled_character_is_intact():
    """Generic integrity for each bundled character: whatever ships must
    actually render — positive cell/columns, an idle pose the pose map
    resolves, and an atlas under the pet assets root."""
    characters = _registry()
    assert characters
    for name, c in characters.items():
        assert c["cell"] > 0, name
        assert c["columns"] > 0, name
        assert c["state_poses"]["idle"] in c["pose_index"], name
        assert c["atlas"].startswith("/static/otp/pet/"), name


def test_unknown_stored_character_falls_back_to_the_default(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "pet-fallback.db"))
    from app.db.connection import init_db
    init_db()
    from app.db.queries import set_setting
    from app.services.pet_characters import get_pet_character
    set_setting("pet_character", "does-not-exist")
    assert get_pet_character()["name"] == _default_name()


def test_the_unconfigured_install_serves_the_registrys_first_character(client):
    default = _registry()[_default_name()]
    html = client.get("/").text
    assert f'data-atlas="{default["atlas"]}"' in html
    assert f'data-cell="{default["cell"]}"' in html
    assert f'data-columns="{default["columns"]}"' in html
    # A character with no hide strip of its own ships an empty attribute,
    # and companion.js treats empty as "instant hide".
    assert f'data-hide-strip="{default["hide_strip"]}"' in html
    # The character's greet pose ships as data, not as renderer literals.
    assert default["state_poses"]["greet"] in html


# ── Render + cache ──────────────────────────────────────────────────────

def test_switching_the_character_flips_the_cached_shell(client):
    _login(client)
    characters = _registry()
    default_name = _default_name()
    other_name = next(n for n in characters if n != default_name)
    assert client.post("/admin/api/pet-character",
                       json={"character": other_name}).status_code == 200
    other = characters[other_name]
    html = client.get("/").text
    assert f'data-atlas="{other["atlas"]}"' in html
    assert f'data-cell="{other["cell"]}"' in html
    assert f'data-columns="{other["columns"]}"' in html
    assert f'data-hide-strip="{other["hide_strip"]}"' in html
    # Its own state map rides the page (success and error poses).
    assert other["state_poses"]["success"] in html
    assert other["state_poses"]["error"] in html
    # The cached shell flipped on the SAME process — cache key carries the
    # character identity (themes.py).
    assert f'data-atlas="{characters[default_name]["atlas"]}"' not in html


# ── Admin API ───────────────────────────────────────────────────────────

def test_pet_character_api_lists_and_saves(client):
    _login(client)
    characters = _registry()
    default_name = _default_name()
    other_name = next(n for n in characters if n != default_name)
    r = client.get("/admin/api/pet-character")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] == default_name
    names = {c["name"] for c in body["characters"]}
    assert set(characters) <= names
    entry = next(c for c in body["characters"] if c["name"] == other_name)
    assert entry["preview"].startswith("/static/")

    assert client.post("/admin/api/pet-character",
                       json={"character": other_name}).status_code == 200
    assert client.get("/admin/api/pet-character").json()["current"] == other_name


def test_pet_character_api_rejects_unknown(client):
    _login(client)
    r = client.post("/admin/api/pet-character", json={"character": "../evil"})
    assert r.status_code == 400


def test_pet_character_api_requires_admin(client):
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/api/pet-character").status_code in (401, 403)
        r = anon.post("/admin/api/pet-character",
                      json={"character": _default_name()})
        assert r.status_code in (401, 403)


# ── The renderer's contract (static) ────────────────────────────────────

def test_companion_js_reads_per_character_layout_and_maps():
    """The shared renderer must not assume one character's grid: columns, pose
    indices and state→pose come from the character's data attributes, and
    an unmapped pose degrades to idle instead of drawing nothing."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent
          / "static" / "companion" / "companion.js").read_text(encoding="utf-8")
    assert "dataset.columns" in js
    assert "_poseJson('poseIndex')" in js
    assert "_poseJson('poses')" in js
    assert "POSE['idle-neutral']" in js  # the never-blank fallback
