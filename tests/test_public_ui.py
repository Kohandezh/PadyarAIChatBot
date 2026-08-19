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
    header = read(LIQUID / "partials" / "header.html")
    assert "دستیار پادیار" in header
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


def test_companion_interaction_is_keyboard_reachable():
    """The companion is interactive (Pet-INOTEX parity: rail controls, drag,
    tap-to-open chat), so its affordances must be real controls.

    Policy history: the companion was first barred from the chat UI, then
    restored on request (2026-08-15) with the full Pet-INOTEX interaction set.
    The invariant that survived every revision: the drawing itself is
    decorative and every ACTION is a focusable, labelled button — a
    keyboard-only or screen-reader visitor can do everything a mouse can.
    """
    footer = read(ROOT / "themes" / "inotex" / "partials" / "footer.html")

    # The canvas is the decorative layer, hidden from assistive tech.
    canvas_tag = "<canvas" + footer.split("<canvas", 1)[1].split(">", 1)[0]
    assert 'id="pet-canvas"' in canvas_tag
    assert 'aria-hidden="true"' in canvas_tag, "the drawing must stay decorative"

    # Every action is a real button carrying an accessible name.
    for control in ('id="pet-eye"', 'id="pet-larger"', 'id="pet-smaller"',
                    'id="pet-hit"', 'id="pet-close"'):
        assert control in footer, f"missing companion control {control}"
        markup = footer.split(control, 1)[0][-320:] + footer.split(control, 1)[1][:320]
        assert "button" in markup, f"{control} must be a real button"
        assert "aria-label" in markup, f"{control} needs an accessible name"

    # The mini chat is announced like a conversation, not a decoration.
    assert 'role="log"' in footer
    assert 'aria-live="polite"' in footer
    assert 'aria-expanded' in footer

    css = read(ROOT / "themes" / "inotex" / "static" / "style.css")
    # Present on BOTH tabs (chat and video) — it is a theme-level companion,
    # not a chat-only decoration. On the video tab it is repositioned, never
    # removed; a `display: none` there is the regression this guards.
    assert "body.video-mode .pet-slot { bottom:" in css
    assert "body.video-mode .pet-slot { display: none" not in css
    # It still yields on viewports too short to hold it and the composer.
    assert "@media (max-height: 620px)" in css


def test_one_sound_control_for_the_surface_in_view():
    """A single speaker button owns sound, and says which sound it owns.

    Two separate sound controls on one screen (a composer button for
    text-to-speech plus a floating one for the video) leaves the visitor
    guessing which is "the" sound button, so the composer button switches
    meaning with the tab and re-labels itself.
    """
    footer = read(ROOT / "themes" / "inotex" / "partials" / "footer.html")
    video = read(ROOT / "themes" / "inotex" / "partials" / "video.html")

    assert 'id="video-sound"' not in video, "no second, competing sound control"
    assert "toggleVideoSound" in footer
    assert "video-mode" in footer, "the button must know which surface is in view"
    # Both meanings are spelled out for assistive tech, not implied by an icon.
    for label in ("پخش صدای ویدیو", "قطع صدای ویدیو", "خواندن پاسخ‌ها با صدا"):
        assert label in footer, f"missing sound-button label: {label}"
    assert "aria-pressed" in footer


def test_inotex_theme_uses_official_palette_tokens():
    css = read(ROOT / "themes" / "inotex" / "static" / "style.css")
    for token in ("--inotex-primary: #FCB715", "--inotex-blue: #2D5CA7",
                  "--inotex-navy: #1E2D52", "--inotex-teal: #04A584"):
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
    assert "دستیار پادیار" in messages


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
    assert 'id="lang-btn"' in read(LIQUID / "partials" / "header.html")
    # EN suggested questions exist
    assert "What is INOTEX?" in js


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
    """Guards the rename itself: the old name must not creep back in."""
    for p in [LIQUID / "partials" / "header.html",
              ROOT / "themes" / "inotex" / "partials" / "header.html",
              CORE_JS]:
        text = read(p)
        assert "دستیار پادیار" in text or "Padyar Assistant" in text, p
        assert "دستیار هوشمند اینوتکس" not in text, f"old name returned in {p}"
