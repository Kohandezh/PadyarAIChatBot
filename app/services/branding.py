"""White-label branding: the real `whitelabel_*` keys, one source of truth.

This is a CMS installed per-customer, so the install's own name, colours,
welcome text and optional logo live in the `settings` table — never in code
literals. Defaults here reproduce the INOTEX chat pixel-for-pixel: an install
that never opens the branding form renders exactly what it shipped with.

Surfaces served (all read through get_branding / chat_branding_context):
  * the chat page (title, header, welcome, --wl-* palette, JS brand override)
  * the admin sidebar title and the branding form (`/admin/api/branding`)
  * the /v lead pages (leads.py keeps its own escaping, reads these defaults)

Escaping contract — read before touching anything here:
  * The theme Jinja2 env is built with autoescape=False (app/services/themes.py),
    so chat_branding_context() pre-escapes EVERY value it hands over. A raw
    value in that dict is stored XSS.
  * The JSON payload inside <script> must NOT use html.escape (entities are not
    decoded inside a script block — the visitor would see &quot; literally).
    json.dumps + the `</` to `<\\/` guard is the standard JSON-in-script
    sanitization: it stops the payload from ever closing its own tag.
  * The admin env has autoescape=True and receives raw values only.
"""
import html
import json

# The whole white-label surface. Colors match the historical leads.py
# defaults (primary=blue / accent=yellow) so /v pages are unchanged; the
# remaining six --wl-* tokens feed the theme's full palette (see
# chat_branding_context below) so Settings > Branding controls EVERY theme
# color; the name is unified to the chat's own name per the owner decision
# (the three surfaces used to hardcode three different names). The subtitle
# is the small line under the chat title (default keeps today's pixels).
WL_DEFAULTS = {
    "whitelabel_app_name": "دستیار پادیار",
    # The small line under the chat title. The default keeps today's pixels:
    # every theme header hardcoded "INOTEX" before this key existed.
    "whitelabel_subtitle": "INOTEX",
    # Empty = the theme's built-in SVG brand mark; a set URL replaces it.
    "whitelabel_logo_url": "",
    "whitelabel_primary_color": "#2D5CA7",
    "whitelabel_accent_color": "#FCB715",
    "whitelabel_yellow_light_color": "#FEBE27",
    "whitelabel_navy_color": "#1E2D52",
    "whitelabel_teal_color": "#04A584",
    "whitelabel_dark_teal_color": "#00644F",
    "whitelabel_background_color": "#000000",
    "whitelabel_white_color": "#FFFFFF",
    "whitelabel_welcome_text": "سلام! من دستیار پادیار هستم. درباره نمایشگاه اینوتکس هر سوالی دارید بپرسید.",
    # Background images behind the two tabs (the theme paints them on
    # .view-container). Same shipped photo for both, so an install that
    # never opens the form renders today's pixels. Empty = the default.
    "whitelabel_chat_background_url": "/themes/inotex/static/bg-bricks.jpg",
    "whitelabel_video_background_url": "/themes/inotex/static/bg-bricks.jpg",
}

# The stored form fields (admin API / form) map 1:1 onto the keys above.
WL_FIELD_TO_KEY = {
    "app_name": "whitelabel_app_name",
    "subtitle": "whitelabel_subtitle",
    "logo_url": "whitelabel_logo_url",
    "primary_color": "whitelabel_primary_color",
    "accent_color": "whitelabel_accent_color",
    "yellow_light_color": "whitelabel_yellow_light_color",
    "navy_color": "whitelabel_navy_color",
    "teal_color": "whitelabel_teal_color",
    "dark_teal_color": "whitelabel_dark_teal_color",
    "background_color": "whitelabel_background_color",
    "white_color": "whitelabel_white_color",
    "welcome_text": "whitelabel_welcome_text",
    "chat_background_url": "whitelabel_chat_background_url",
    "video_background_url": "whitelabel_video_background_url",
}


def _css_url(value: str) -> str:
    """Wrap a URL as a CSS url("...") token that cannot break out.

    html.escape is WRONG here: <style> is a raw-text element, entities are
    not decoded inside it, so &quot; would reach the browser literally and
    an & in a query string would survive as &amp;. CSS-string escaping
    (backslash, both quotes, newlines) plus the `</` guard is the correct
    sanitization for this position.
    """
    safe = (value.replace("\\", "\\\\")
                 .replace('"', '\\"')
                 .replace("'", "\\'")
                 .replace("\n", " ")
                 .replace("\r", " ")
                 .replace("</", "<\\/"))
    return f'url("{safe}")'


def get_branding() -> dict:
    """Raw current values keyed by the full `whitelabel_*` setting names.

    `or default` collapses an explicitly-cleared row back to the default, so
    an operator emptying the welcome field gets the shipped greeting, never
    a blank bubble. Reads ride the shared settings TTL cache (15s).
    """
    from app.db.queries import get_setting
    return {
        key: (get_setting(key, default) or default)
        for key, default in WL_DEFAULTS.items()
    }


def wl_cache_key() -> tuple:
    """Identity of the current branding for the rendered-page cache key.

    Brand values are baked into the cached chat shell (same bytes for every
    visitor), so the cache key must flip the moment a value changes — or an
    admin save would keep serving the old shell until a theme file changed.
    A tuple of the values: cheap, order-stable, no hash collisions to reason
    about. themes.py appends it to both cache keys; the per-visitor token
    splice is untouched by design.
    """
    b = get_branding()
    return tuple(b[k] for k in WL_DEFAULTS)


def chat_branding_context() -> dict:
    """Everything the chat render needs, PRE-ESCAPED for autoescape=False.

    Consumed by read_root() and merged into render_theme_index()'s context.
    Every string here is safe for raw emission into the theme templates —
    that is the whole point of this function.
    """
    b = get_branding()
    esc = lambda v: html.escape(v, quote=True)  # noqa: E731 — local idiom
    brand_json = (
        json.dumps(
            {"app_name": b["whitelabel_app_name"],
             "welcome": b["whitelabel_welcome_text"]},
            ensure_ascii=True,
        )
        # The </ guard is the load-bearing part: it makes it impossible for a
        # stored value to emit "</script>" and break out of the block. `<`
        # alone is inert inside a JS string.
        .replace("</", "<\\/")
    )
    return {
        # Plain values (title/subtitle/header/welcome/logo) — html-escaped.
        "app_title": esc(b["whitelabel_app_name"]),
        "wl_subtitle": esc(b["whitelabel_subtitle"]),
        "wl_welcome": esc(b["whitelabel_welcome_text"]),
        "wl_logo_url": esc(b["whitelabel_logo_url"]),
        # Ready-made tags, emitted raw by head.html. One --wl-* custom
        # property per palette token plus the two background images; themes
        # map their own --{theme}-* tokens onto these (with the official
        # palette as var() fallback), so Settings > Branding controls every
        # theme color and both tab backgrounds.
        "wl_style": (
            "<style>:root{"
            f"--wl-primary:{esc(b['whitelabel_primary_color'])};"
            f"--wl-accent:{esc(b['whitelabel_accent_color'])};"
            f"--wl-yellow-light:{esc(b['whitelabel_yellow_light_color'])};"
            f"--wl-navy:{esc(b['whitelabel_navy_color'])};"
            f"--wl-teal:{esc(b['whitelabel_teal_color'])};"
            f"--wl-dark-teal:{esc(b['whitelabel_dark_teal_color'])};"
            f"--wl-background:{esc(b['whitelabel_background_color'])};"
            f"--wl-white:{esc(b['whitelabel_white_color'])};"
            f"--wl-chat-background:{_css_url(b['whitelabel_chat_background_url'])};"
            f"--wl-video-background:{_css_url(b['whitelabel_video_background_url'])};"
            "}</style>"
        ),
        "wl_brand_script": f"<script>window.PADYAR_BRAND={brand_json};</script>",
        # Cache-key material for themes.py — not rendered by any template.
        "wl_cache_key": wl_cache_key(),
    }
