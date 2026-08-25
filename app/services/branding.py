"""White-label branding: the 5 real `whitelabel_*` keys, one source of truth.

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

# Exactly 5 keys — the whole white-label surface. Colors match the historical
# leads.py defaults (primary=blue / accent=yellow) so /v pages are unchanged;
# the name is unified to the chat's own name per the owner decision (the three
# surfaces used to hardcode three different names).
WL_DEFAULTS = {
    "whitelabel_app_name": "دستیار پادیار",
    # Empty = the theme's built-in SVG brand mark; a set URL replaces it.
    "whitelabel_logo_url": "",
    "whitelabel_primary_color": "#2D5CA7",
    "whitelabel_accent_color": "#FCB715",
    "whitelabel_welcome_text": "سلام! من دستیار پادیار هستم. درباره نمایشگاه اینوتکس هر سوالی دارید بپرسید.",
}

# The stored form fields (admin API / form) map 1:1 onto the keys above.
WL_FIELD_TO_KEY = {
    "app_name": "whitelabel_app_name",
    "logo_url": "whitelabel_logo_url",
    "primary_color": "whitelabel_primary_color",
    "accent_color": "whitelabel_accent_color",
    "welcome_text": "whitelabel_welcome_text",
}


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
    A tuple of the 5 values: cheap, order-stable, no hash collisions to reason
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
        # Plain values (title/header/welcome/logo) — html-escaped.
        "app_title": esc(b["whitelabel_app_name"]),
        "wl_welcome": esc(b["whitelabel_welcome_text"]),
        "wl_logo_url": esc(b["whitelabel_logo_url"]),
        # Ready-made tags, emitted raw by head.html.
        "wl_style": (
            "<style>:root{"
            f"--wl-primary:{esc(b['whitelabel_primary_color'])};"
            f"--wl-accent:{esc(b['whitelabel_accent_color'])};"
            "}</style>"
        ),
        "wl_brand_script": f"<script>window.PADYAR_BRAND={brand_json};</script>",
        # Cache-key material for themes.py — not rendered by any template.
        "wl_cache_key": wl_cache_key(),
    }
