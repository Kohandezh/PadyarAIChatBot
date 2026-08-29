"""Hamburger-drawer row visibility — which of language / theme (light-dark) /
text-size / logout an admin wants visible at all.

Same key-value pattern as whitelabel_* (app/services/branding.py) and
idle_video_* (app/services/idle_video.py): one settings row per flag, all
default "on" so an install that never opens this admin screen renders exactly
what it does today. A theme that has no language switch (haj) or no dark
mode (base) never had that row in its markup to begin with — these flags only
ever HIDE a row a theme already has, never add one a theme doesn't.
"""
from app.db.queries import get_setting, set_setting

MENU_DEFAULTS = {
    "menu_show_language": True,
    "menu_show_theme_toggle": True,
    "menu_show_text_size": True,
    "menu_show_logout": True,
}


def get_menu_settings() -> dict:
    """Current flags, keyed by the full setting name, as real booleans."""
    return {
        key: get_setting(key, "true" if default else "false") == "true"
        for key, default in MENU_DEFAULTS.items()
    }


def set_menu_settings(values: dict) -> None:
    for key in MENU_DEFAULTS:
        if key in values:
            set_setting(key, "true" if values[key] else "false")


def menu_settings_cache_key() -> tuple:
    """Identity of the current flags for the rendered-page cache — mirrors
    branding.wl_cache_key / idle_video.idle_video_cache_key."""
    m = get_menu_settings()
    return tuple(m[k] for k in MENU_DEFAULTS)


def menu_settings_context() -> dict:
    """Template context for the chat render. Plain booleans — safe straight
    into a Jinja {% if %}, no escaping needed (never emitted as text)."""
    m = get_menu_settings()
    m["menu_settings_cache_key"] = menu_settings_cache_key()
    return m
