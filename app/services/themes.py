"""
Theme discovery and management service.

Scans the themes/ directory for self-contained theme folders,
each with a theme.json metadata file, and optional partials/ or index.html.

Themes use Jinja2 partials (WordPress-style): themes/{name}/partials/ overrides
the base theme's partials/themes/base/partials/. If a theme has an index.html
instead of partials/, it's served as a raw file (legacy mode).
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional

import jinja2

from app.config import BASE_DIR
from app.db.queries import get_setting, set_setting

logger = logging.getLogger("PadyarAssistant")

THEMES_DIR = os.path.join(BASE_DIR, "themes")
VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def discover_themes() -> List[Dict]:
    """Scan themes/ directory, parse each theme.json, return sorted list."""
    themes: List[Dict] = []

    if not os.path.isdir(THEMES_DIR):
        logger.warning(f"Themes directory not found: {THEMES_DIR}")
        return themes

    for entry in sorted(os.listdir(THEMES_DIR)):
        theme_dir = os.path.join(THEMES_DIR, entry)
        if not os.path.isdir(theme_dir):
            continue

        meta = _load_theme_meta(entry, theme_dir)
        if meta:
            themes.append(meta)

    themes.sort(key=lambda t: t.get("display_name", t["name"]))
    return themes


def get_theme(name: str) -> Optional[Dict]:
    """Load and return metadata for a single theme by name."""
    theme_dir = os.path.join(THEMES_DIR, name)
    if not os.path.isdir(theme_dir):
        return None
    return _load_theme_meta(name, theme_dir)


def get_active_theme() -> str:
    """Return the machine name of the active theme. Defaults to 'inotex'."""
    return get_setting("active_theme", "inotex")


def set_active_theme(name: str) -> bool:
    """Validate theme exists and set it as active. Returns True on success."""
    theme = get_theme(name)
    if not theme:
        logger.warning(f"Attempted to activate unknown theme: {name}")
        return False

    set_setting("active_theme", name)
    logger.info(f"Theme activated: {name}")
    return True


def get_theme_index_path(name: str) -> str:
    """Return absolute path to themes/{name}/index.html (legacy mode)."""
    return os.path.join(THEMES_DIR, name, "index.html")


# --- Rendered-page cache --------------------------------------------------
# The public chat page is the most-visited endpoint of an exhibition and its
# render was anything but cheap: a fresh Jinja2 Environment, a template
# compile and several file reads per request, all on the event loop. The only
# per-visitor part of the page is the HMAC chat token, so the rendered shell
# is cached and the token is spliced in per request.
#
# The cache key includes the mtimes of every file behind the render, so a
# theme upgrade invalidates itself on the next request with no TTL guessing.
#
# Branding is baked into the cached shell (read_root merges
# branding.chat_branding_context() into the render), so the key also carries
# wl_cache_key — the identity of the 5 brand values. Without it an admin save
# would leave every visitor on the old shell until a theme file happened to
# change. A save pops the settings TTL cache (set_setting), the next request
# builds a new wl_cache_key, and exactly one fresh render happens. The token
# splice path is untouched: the placeholder never leaves this module.
_TOKEN_PLACEHOLDER = "__PADYAR_CHAT_TOKEN__"
_PAGE_CACHE: Dict[tuple, str] = {}
_PAGE_CACHE_MAX = 8


def _fingerprint(paths) -> str:
    parts = []
    for p in paths:
        if not os.path.isdir(p):
            continue
        for root, _dirs, files in os.walk(p):
            for f in sorted(files):
                fp = os.path.join(root, f)
                try:
                    parts.append(f"{f}:{int(os.path.getmtime(fp))}")
                except OSError:
                    continue
    return "|".join(parts)


def render_theme_index(theme_name: str, context: dict) -> str:
    """Render theme's index.html using Jinja2 with partial override resolution.

    If the theme has a partials/ directory, uses Jinja2 FileSystemLoader with
    child-first search path: [theme/partials, parent/partials, base/partials].
    Falls back to raw file read for legacy themes with index.html.

    The rendered shell is cached (see the module comment above); only the
    chat token differs between visitors.
    """
    theme_dir = os.path.join(THEMES_DIR, theme_name)
    theme_partials = os.path.join(theme_dir, "partials")
    base_partials = os.path.join(THEMES_DIR, "base", "partials")

    token = str(context.get("chat_token", "") or "")
    # Brand identity of this render (absent for callers that pass none — e.g.
    # a bare test render — and then None keeps the key shape stable).
    wl_key = context.get("wl_cache_key")
    # Same contract for the hamburger-drawer row-visibility flags: baked into
    # the cached shell, so an admin save must flip the key (see
    # app/services/menu_settings.py).
    menu_key = context.get("menu_settings_cache_key")

    # Legacy mode: theme has index.html but no partials/
    if not os.path.isdir(theme_partials):
        index_path = os.path.join(theme_dir, "index.html")
        if os.path.isfile(index_path):
            try:
                mtime = int(os.path.getmtime(index_path))
            except OSError:
                mtime = 0
            key = ("legacy", theme_name, mtime, wl_key, menu_key)
            html = _PAGE_CACHE.get(key)
            if html is None:
                with open(index_path, "r", encoding="utf-8") as f:
                    html = f.read().replace(
                        "<!-- CHAT_TOKEN -->",
                        f'<meta name="chat-token" content="{_TOKEN_PLACEHOLDER}">')
                if len(_PAGE_CACHE) >= _PAGE_CACHE_MAX:
                    _PAGE_CACHE.clear()
                _PAGE_CACHE[key] = html
            return html.replace(_TOKEN_PLACEHOLDER, token)
        raise FileNotFoundError(f"No partials/ or index.html for theme '{theme_name}'")

    # Determine parent theme
    parent = "base"
    meta_path = os.path.join(theme_dir, "theme.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            parent = meta.get("parent", "base")
        except (json.JSONDecodeError, OSError):
            pass

    # Build search path: child first, then parent chain, then base
    search_path = [theme_partials]
    if parent != "base":
        parent_partials = os.path.join(THEMES_DIR, parent, "partials")
        if os.path.isdir(parent_partials):
            search_path.append(parent_partials)
    if os.path.isdir(base_partials):
        search_path.append(base_partials)

    key = ("partials", theme_name, _fingerprint(search_path), wl_key, menu_key)
    html = _PAGE_CACHE.get(key)
    if html is None:
        loader = jinja2.FileSystemLoader(search_path)
        env = jinja2.Environment(loader=loader, autoescape=False)
        template = env.get_template("index.html")
        render_context = dict(context)
        render_context["chat_token"] = _TOKEN_PLACEHOLDER
        html = template.render(**render_context)
        if len(_PAGE_CACHE) >= _PAGE_CACHE_MAX:
            _PAGE_CACHE.clear()
        _PAGE_CACHE[key] = html
    return html.replace(_TOKEN_PLACEHOLDER, token)


# ── Internal helpers ───────────────────────────────────────────────────

def _load_theme_meta(name: str, theme_dir: str) -> Optional[Dict]:
    """Parse and validate a single theme's metadata. Returns None on failure."""
    json_path = os.path.join(theme_dir, "theme.json")

    if not os.path.isfile(json_path):
        logger.warning(f"Skipping theme '{name}': missing theme.json")
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Skipping theme '{name}': invalid theme.json — {e}")
        return None

    # Validate required fields
    if not isinstance(meta.get("name"), str) or meta["name"] != name:
        logger.warning(f"Skipping theme '{name}': 'name' must match directory name")
        return None

    if not VALID_NAME_RE.match(name):
        logger.warning(f"Skipping theme '{name}': invalid name format")
        return None

    # Must have either index.html (legacy) or partials/ (structured)
    has_index = os.path.isfile(os.path.join(theme_dir, "index.html"))
    has_partials = os.path.isdir(os.path.join(theme_dir, "partials"))
    if not has_index and not has_partials:
        logger.warning(f"Skipping theme '{name}': missing index.html or partials/")
        return None

    # Skip base theme from user-selectable list
    if meta.get("selectable") is False:
        return None

    screenshot = meta.get("screenshot", "screenshot.png")
    if not os.path.isfile(os.path.join(theme_dir, screenshot)):
        logger.warning(f"Theme '{name}': screenshot '{screenshot}' not found")

    # Ensure display_name fallback
    meta.setdefault("display_name", name.title())
    meta.setdefault("description", "")
    meta.setdefault("version", "1.0.0")
    meta.setdefault("author", "")
    meta.setdefault("preview_colors", {})

    return meta
