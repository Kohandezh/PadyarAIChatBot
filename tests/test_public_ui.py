import re
"""Static-content tests for the public chat UI (R2/R3/R4/R5).

These read the theme files directly so they run without the FastAPI app or its
heavy dependencies (jinja2/sklearn). They assert the INOTEX transformation is
present in the *active* theme (liquid-glass) and shared core, and that no
Noor/medical/remote-media references leak into the public surface.
(«پادیار» is now the assistant's own name and is no longer banned.)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIQUID = ROOT / "themes" / "liquid-glass"
CORE_JS = ROOT / "static" / "chat" / "core.js"
BASE_CSS = ROOT / "static" / "chat" / "base.css"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── R4: INOTEX branding + red theme + mascot + credit badge ────────────

def test_active_theme_is_inotex_branded():
    # The header title is the templated whitelabel value (rendered by
    # /), not a hardcoded literal — branding is served from the
    # whitelabel_app_name setting with this fallback.
    header = read(LIQUID / "partials" / "header.html")
    assert "{{ app_title }}" in header
    assert "INOTEX" in header


def test_header_carries_the_brand_mark_not_a_character():
    """The header identifies the product with the hexagon-cube brand mark.

    The companion character lives in the decorative corner (see
    test_companion_is_decorative_only) — never inside the header, and never
    as the brand identity.
    """
    inotex_header = read(ROOT / "themes" / "inotex" / "partials" / "header.html")
    liquid_header = read(LIQUID / "partials" / "header.html")
    for text in (inotex_header, liquid_header):
        assert 'class="mascot"' not in text
        assert "pet-canvas" not in text
    assert 'class="brand-mark"' in inotex_header


def _without_html_comments(markup: str) -> str:
    """What the browser actually renders. An HTML comment is still bytes in the
    file, so asserting against raw text cannot tell "present" from
    "commented out"."""
    return re.sub(r"<!--.*?-->", "", markup, flags=re.S)


def test_companion_is_live_but_desktop_only():
    """The companion is back in the rendered markup — and pinned as LIVE.

    Policy history: barred from the chat UI, restored on request (2026-08-15)
    with the full Pet-INOTEX interaction set, switched off again (2026-08-21),
    and restored AGAIN on 2026-08-24 — this time desktop/tablet only: every
    surface that carries the character must hide it below a 640px viewport so
    it never crowds a phone composer or the OTP card.

    Assertions run on comment-stripped markup, so a future re-disable via
    HTML comments fails HERE instead of silently shipping.
    """
    footer = read(ROOT / "themes" / "inotex" / "partials" / "footer.html")
    visible = _without_html_comments(footer)

    assert 'id="pet-canvas"' in visible, "companion should be live markup"
    assert "COMPANION-OFF" not in footer, "stale off-markers left behind"

    # The accessibility contract: the drawing is decorative and every action
    # is a real, labelled button.
    for control in ('id="pet-eye"', 'id="pet-larger"', 'id="pet-smaller"',
                    'id="pet-hit"', 'id="pet-close"'):
        assert control in visible, f"companion control {control} not live"

    # The desktop-only rule, on every surface that carries the character.
    theme_css = read(ROOT / "themes" / "inotex" / "static" / "style.css")
    otp_css = read(ROOT / "static" / "otp" / "otp.css")
    for css, name in ((theme_css, "inotex theme"), (otp_css, "otp page")):
        assert "@media (max-width: 639px)" in css, f"{name}: missing <640px hide"
    pet_hide = re.search(
        r"@media \(max-width: 639px\) \{[^}]*\.pet-slot[^}]*display: none", theme_css)
    assert pet_hide, "inotex theme: .pet-slot not hidden under 640px"
    pet_hide_otp = re.search(
        r"@media \(max-width: 639px\) \{[^}]*\.pet-slot[^}]*display: none", otp_css)
    assert pet_hide_otp, "otp page: .pet-slot not hidden under 640px"

    # The OTP surface carries the character too — same live, same rule.
    otp_visible = _without_html_comments(
        read(ROOT / "templates" / "otp" / "verify.html"))
    assert 'id="pet-canvas"' in otp_visible, "otp page companion should be live"


def test_composer_has_no_sound_control():
    """The composer's speaker button (#tts-btn) — read-aloud on the chat tab,
    video mute/unmute on the video tab — was removed at the product owner's
    request (docs/features/hamburger-menu/SPEC.md, REQ-005): it conflated two
    unrelated jobs behind one icon. Nothing should remain wired to it: the
    button, its per-theme CSS, and the localStorage-driven speak/toggle logic
    (a stale "on" preference must not produce unstoppable narration with no
    control left to turn it off — REL-001).
    """
    for theme in ("inotex", "liquid-glass", "haj"):
        input_html = read(ROOT / "themes" / theme / "partials" / "input.html")
        footer = read(ROOT / "themes" / theme / "partials" / "footer.html")
        css = read(ROOT / "themes" / theme / "static" / "style.css")
        assert 'id="tts-btn"' not in input_html, theme
        assert "toggleVideoSound" not in footer, theme
        assert ".tts-btn" not in css, theme

    video = read(ROOT / "themes" / "inotex" / "partials" / "video.html")
    assert 'id="video-sound"' not in video, "no floating sound control either"


def test_inotex_theme_uses_official_palette_tokens():
    css = read(ROOT / "themes" / "inotex" / "static" / "style.css")
    # primary/accent feed from the whitelabel --wl-* custom properties, with
    # the official palette as var() fallbacks (the mapping is intentionally
    # crossed: --wl-primary is BLUE, --wl-accent is YELLOW — see
    # app/services/branding.py). The untouched tokens stay literal.
    for token in ("--inotex-primary: var(--wl-accent, #FCB715)",
                  "--inotex-blue: var(--wl-primary, #2D5CA7)",
                  "--inotex-navy: #1E2D52",
                  "--inotex-teal: #04A584"):
        assert token in css, f"missing palette token: {token}"
    # No unapproved purple/magenta in the INOTEX theme.
    for banned in ("#8b5cf6", "#9b1c53", "#b32462", "magenta"):
        assert banned not in css.lower()


def test_theme_colour_lives_only_in_the_primitive_layer():
    """The palette is indigo/violet. What this test really protects is the
    token architecture: components must reference semantic aliases, never a
    raw brand hex, so swapping the palette stays a one-layer change."""
    css = read(LIQUID / "static" / "style.css")
    lower = css.lower()
    assert "--indigo-600: #4f46e5" in lower
    assert "--violet-500: #8b5cf6" in lower
    # Semantic aliases point at primitives...
    assert "--color-accent: var(--indigo-600)" in css
    assert "--color-primary: var(--indigo-600)" in css
    assert "--send-bg: var(--color-accent)" in css
    # ...and the retired palette is gone entirely.
    for dead in ("#9b1c53", "#b32462", "--navy-800", "--inotex-magenta"):
        assert dead not in lower, dead


def test_credit_badge_present_with_exact_persian_text():
    """The credit string must appear verbatim, carry the Rayen logo, and be
    anchored in the layout rather than floating over the conversation."""
    partials = (LIQUID / "partials")
    markup = "".join(read(p) for p in partials.glob("*.html"))
    css = read(LIQUID / "static" / "style.css")
    exact = "قدرت گرفته از سکوی ملی متن باز هوش مصنوعی"
    assert exact in markup
    assert "rayen-sidebar-foot" in markup
    assert "rayen-logo.png" in markup
    assert "rayen-sidebar-foot" in css


def test_theme_json_matches_the_indigo_palette():
    """theme.json drives the admin theme picker's swatches — it must agree
    with the stylesheet, or the picker advertises a theme that no longer exists."""
    import json
    meta = json.loads(read(LIQUID / "theme.json"))
    colors = meta["preview_colors"]
    assert colors["primary"].lower() == "#4f46e5"
    assert colors["secondary"].lower() == "#8b5cf6"
    assert meta["author"] == "INOTEX"


# ── R2: original video + chat structure, no remote media ───────────────

def test_active_theme_inherits_video_and_chat_layout():
    index = read(ROOT / "themes" / "base" / "partials" / "index.html")
    header = read(LIQUID / "partials" / "header.html")
    video = read(ROOT / "themes" / "base" / "partials" / "video.html")
    assert '"messages.html"' in index
    assert '"video.html"' in index
    assert 'id="avatar-video"' in video
    # The original product's segmented control: two radios, video default.
    assert 'class="switcher"' in header
    assert 'value="video"' in header
    assert 'value="text"' in header


def test_active_theme_messages_have_language_aware_welcome():
    messages = read(LIQUID / "partials" / "messages.html")
    assert 'id="welcome-text"' in messages
    # The greeting is the templated whitelabel_welcome_text value — the
    # shipped default text lives in app/services/branding.py (WL_DEFAULTS).
    assert "{{ wl_welcome }}" in messages


def test_video_partial_has_no_remote_media():
    """The base video partial (kept for a future module) must not reference
    any remote/Noor media or autoplay anything by default."""
    video = read(ROOT / "themes" / "base" / "partials" / "video.html")
    assert "noorvision.com" not in video
    assert "Waiting_2.mp4" not in video
    assert "introduce.mp4" not in video
    # No autoplay so nothing plays until a real source is provided.
    assert "autoplay" not in video


# ── R3: bilingual UI + language switch ───────────────────────────────────

def test_core_js_has_fa_en_i18n_and_switch():
    js = read(CORE_JS)
    assert "const I18N" in js
    assert "function setLang" in js
    assert "'fa'" in js and "'en'" in js
    # lang-btn moved from the header into the hamburger drawer (see
    # docs/features/hamburger-menu/SPEC.md) — same id, same setLang() wiring,
    # different partial.
    assert 'id="lang-btn"' in read(LIQUID / "partials" / "menu.html")
    # EN suggested questions exist
    assert "What is INOTEX?" in js


def test_every_theme_localises_the_new_chat_button():
    """Phase 3 (docs/features/hamburger-menu/SPEC.md) moved "New chat" out of
    the header and into the drawer's sidebar as the always-first, labelled
    row (base/inotex/minimal/liquid-glass) — matching a ChatGPT-style
    sidebar, and no longer icon-only: `data-i18n` now drives a visible text
    node, on top of the `data-i18n-title` accessible name it always had.

    haj is the one deliberate exception: it keeps its own bespoke top-bar
    layout unchanged at every width (see the SPEC's Phase 3 scoping note), so
    its icon-only #new-chat-btn stays exactly where it was, in header.html.
    """
    for theme in ("base", "inotex", "minimal", "liquid-glass"):
        menu = read(ROOT / "themes" / theme / "partials" / "menu.html")
        button = menu.split('id="new-chat-btn"', 1)[1].split("</button>", 1)[0]
        assert 'data-i18n-title="newChat"' in button, theme
        assert 'data-i18n="newChat"' in button, f"{theme}: needs a visible, localized label"
        assert "<svg" in button, f"{theme}: needs an icon"
        header = read(ROOT / "themes" / theme / "partials" / "header.html") \
            if (ROOT / "themes" / theme / "partials" / "header.html").exists() else ""
        assert 'id="new-chat-btn"' not in header, f"{theme}: still duplicated in the header"

    haj_header = read(ROOT / "themes" / "haj" / "partials" / "header.html")
    haj_button = haj_header.split('id="new-chat-btn"', 1)[1].split("</button>", 1)[0]
    assert 'data-i18n-title="newChat"' in haj_button
    assert "<svg" in haj_button


def test_core_js_keeps_video_and_chat_with_null_guards_avatar():
    js = read(CORE_JS)
    # No remote/Noor media anywhere.
    assert "noorvision.com" not in js
    assert "introduce.mp4" not in js
    # Avatar/video references are guarded and the original video landing panel
    # remains available even before local media is configured.
    assert "if (!avatarVideo) return;" in js
    assert "isTextOnly: false" in js
    assert "switchTab('video');" in js
    # Storage keys are INOTEX-branded, not Noor.
    assert "inotex_chat_history" in js
    assert "noor_" not in js


# ── R5: responsive + RTL/LTR + accessibility ────────────────────────────

def test_active_theme_has_responsive_breakpoints():
    css = read(LIQUID / "static" / "style.css")
    for bp in ["max-width: 768px", "max-width: 360px", "orientation: landscape"]:
        assert bp in css, bp
    # Safe-area insets for notched/rounded phones and touch LCDs.
    assert "env(safe-area-inset" in css


def test_active_theme_supports_rtl_and_ltr():
    css = read(LIQUID / "static" / "style.css")
    assert "html[dir=\"ltr\"]" in css
    assert "inset-inline-start" in css or "inset-inline-end" in css


def test_active_theme_has_focus_styles_for_keyboard_a11y():
    css = read(LIQUID / "static" / "style.css")
    assert "focus-visible" in css


def test_questions_are_keyboard_activatable():
    """Suggested questions must be reachable + activable via keyboard."""
    assert "tabIndex = 0" in read(CORE_JS)
    assert "role" in read(CORE_JS).lower()


# ── No Noor/Padyar/medical leakage in public surfaces ───────────────────

def test_no_brand_leakage_in_public_ui_files():
    """No PREVIOUS customer's branding may survive in the public UI.

    "Padyar"/"پادیار" was on this list while the platform name was internal and
    the assistant carried the customer's name. The customer has since chosen
    «دستیار پادیار» as the assistant's own name (2026-08-18), so the platform
    name is now legitimately customer-facing and is no longer banned. The other
    entries stay: they are a DIFFERENT product's branding and must never
    reappear here.
    """
    for p in [
        LIQUID / "static" / "style.css",
        LIQUID / "partials" / "header.html",
        LIQUID / "partials" / "messages.html",
        LIQUID / "partials" / "input.html",
        LIQUID / "partials" / "footer.html",
        CORE_JS,
        BASE_CSS,
    ]:
        text = read(p)
        for banned in ["Noora", "noorvision", "نورا", "Waiting_2"]:
            assert banned not in text, f"'{banned}' found in {p}"


def test_the_assistant_name_is_the_one_the_customer_chose():
    """Guards the rename itself: the old name must not creep back in.

    The headers now render {{ app_title }} (the whitelabel_app_name value);
    core.js keeps the shipped fa fallback so the brand override can fail
    safe. The fallback — not a hardcoded header — is where the name lives.
    """
    for p in [LIQUID / "partials" / "header.html",
              ROOT / "themes" / "inotex" / "partials" / "header.html"]:
        text = read(p)
        assert "{{ app_title }}" in text, p
        assert "دستیار هوشمند اینوتکس" not in text, f"old name returned in {p}"
    js = read(CORE_JS)
    assert "دستیار پادیار" in js or "Padyar Assistant" in js
    assert "دستیار هوشمند اینوتکس" not in js
