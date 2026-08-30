"""Companion (pet) characters — which mascot an install ships.

Same key-value pattern as whitelabel_* (branding.py) / menu_show_*
(menu_settings.py): one `pet_character` settings row naming a folder under
static/otp/pet/characters/, each holding a character.json (atlas, cell size,
column count, fallback, optional hide strip, and the two pose maps the
shared companion.js consumes). An install that never opens the form gets
the INOTEX character — byte-identical to the hardcoded markup this
replaced (the json points at the same flat asset URLs).

The character is baked into the cached chat shell (footer.html's canvas
data-* attributes), so — like branding/menu settings — the context carries
a cache-key tuple themes.py appends to the page cache.

A character with no atlas entry is not a character: the registry skips it
rather than serving a companion that renders nothing.
"""
import html
import json
import logging
import os
import re

from app.config import BASE_DIR
from app.db.queries import get_setting, set_setting

logger = logging.getLogger("PadyarAssistant")

CHARACTERS_DIR = os.path.join(BASE_DIR, "static", "otp", "pet", "characters")
DEFAULT_CHARACTER = "inotex"

# Registry values are page attributes, not free text: names are slugs and
# the pose maps are {slug: slug} / {slug: int}. Anything else would ride
# straight into the cached shell's data-* attributes.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _load_character(name: str, folder: str):
    """Parse and validate one character.json. Returns None on any defect —
    a half-loaded character (atlas without pose map, say) would render a
    pet that can answer nothing, which is worse than no pet."""
    try:
        with open(os.path.join(folder, "character.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[pet] skipping character '{name}': {e}")
        return None

    atlas = str(meta.get("atlas") or "")
    if not atlas.startswith("/"):
        return None
    try:
        cell = int(meta.get("cell"))
        columns = int(meta.get("columns"))
    except (TypeError, ValueError):
        return None
    if cell <= 0 or columns <= 0:
        return None

    state_poses = meta.get("state_poses") or {}
    pose_index = meta.get("pose_index") or {}
    if not isinstance(state_poses, dict) or not isinstance(pose_index, dict):
        return None
    if "idle" not in state_poses or state_poses["idle"] not in pose_index:
        return None
    for state, pose in state_poses.items():
        if not _NAME_RE.match(str(state)) or not _NAME_RE.match(str(pose)):
            return None
    for pose, idx in pose_index.items():
        if not _NAME_RE.match(str(pose)) or not isinstance(idx, int) or not 0 <= idx < 64:
            return None

    return {
        "name": name,
        "display_name": str(meta.get("display_name") or name),
        "atlas": atlas,
        "cell": cell,
        "columns": columns,
        "fallback": str(meta.get("fallback") or ""),
        "hide_strip": str(meta.get("hide_strip") or ""),
        "state_poses": state_poses,
        "pose_index": pose_index,
    }


def discover_characters() -> dict:
    """All valid characters, keyed by folder name. Sorted by name so the
    admin dropdown order is stable."""
    characters: dict = {}
    if not os.path.isdir(CHARACTERS_DIR):
        return characters
    for entry in sorted(os.listdir(CHARACTERS_DIR)):
        folder = os.path.join(CHARACTERS_DIR, entry)
        if not os.path.isdir(folder) or not _NAME_RE.match(entry):
            continue
        loaded = _load_character(entry, folder)
        if loaded:
            characters[entry] = loaded
    if DEFAULT_CHARACTER not in characters:
        logger.warning("[pet] default character missing from the registry")
    return characters


def get_pet_character() -> dict:
    """The active character's metadata. An unknown/missing stored name
    collapses to the default — the companion degrades, it never 500s."""
    characters = discover_characters()
    name = get_setting("pet_character", DEFAULT_CHARACTER) or DEFAULT_CHARACTER
    return characters.get(name) or characters.get(DEFAULT_CHARACTER) or {}


def set_pet_character(name: str) -> bool:
    if name not in discover_characters():
        return False
    set_setting("pet_character", name)
    return True


def pet_character_cache_key() -> tuple:
    """Identity of the current character for the rendered-page cache —
    mirrors branding.wl_cache_key / menu_settings_cache_key."""
    c = get_pet_character()
    return (c.get("name", ""), c.get("atlas", ""), str(c.get("cell", "")))


def pet_character_context() -> dict:
    """Template context for the chat render, PRE-ESCAPED for the theme
    env's autoescape=False (same contract as branding.chat_branding_context).

    The two pose maps ride the canvas as html-escaped JSON in single-quoted
    data-* attributes — attribute values are entity-decoded, so &quot; in
    the source is a real quote to the parser and JSON.parse sees clean JSON.
    """
    c = get_pet_character()

    def attr_json(payload: dict) -> str:
        return html.escape(json.dumps(payload, ensure_ascii=True,
                                       separators=(",", ":")), quote=True)

    return {
        "pet_display_name": html.escape(c.get("display_name", "")),
        "pet_atlas_url": html.escape(c.get("atlas", "")),
        "pet_cell": c.get("cell", 512),
        "pet_columns": c.get("columns", 4),
        "pet_fallback_url": html.escape(c.get("fallback", "")),
        "pet_hide_strip_url": html.escape(c.get("hide_strip", "")),
        "pet_state_poses_json": attr_json(c.get("state_poses", {})),
        "pet_pose_index_json": attr_json(c.get("pose_index", {})),
        "pet_character_cache_key": pet_character_cache_key(),
    }
