"""The code entry must stay ONE input, on both surfaces.

A row of `maxlength="1"` boxes looks identical and is the obvious way to build
this — which is exactly why it needs a guard. The browser truncates an
autofilled SMS code to the first box, so the OS/keyboard code suggestion filled
a single digit and the distribute logic never ran. Documented on Android Chrome
and on iOS.

The markup is built in JavaScript, so these assert against the source. That is
blunt, but the alternative is a browser test for a one-line regression that
would otherwise ship silently and only show up on a real handset.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SURFACES = {
    "in-chat companion": ROOT / "static" / "companion" / "registration.js",
    "/verify page": ROOT / "static" / "otp" / "otp.js",
}
STYLES = {
    "in-chat companion": ROOT / "themes" / "inotex" / "static" / "style.css",
    "/verify page": ROOT / "static" / "otp" / "otp.css",
}
INPUT_CLASS = {"in-chat companion": "reg-code-input", "/verify page": "otp-code-input"}


@pytest.fixture(params=sorted(SURFACES), ids=lambda k: k)
def surface(request):
    return request.param


def test_the_code_field_is_a_single_input(surface):
    src = SURFACES[surface].read_text(encoding="utf-8")
    assert "maxLength = OTP_LENGTH" in src or "maxLength = n" in src, (
        f"{surface}: the code field must hold the whole code")


def test_no_single_character_code_boxes(surface):
    """`maxLength = 1` on the code field is the exact bug this replaced."""
    src = SURFACES[surface].read_text(encoding="utf-8")
    assert "inp.maxLength = 1" not in src
    assert "codeInput.maxLength = 1" not in src


def test_autocomplete_is_on_the_real_field_only(surface):
    """`one-time-code` must sit on the single input. The old markup put it on
    box 0 of several, which is what the browser then truncated."""
    src = SURFACES[surface].read_text(encoding="utf-8")
    assert "'one-time-code'" in src
    assert "i === 0 ? 'one-time-code' : 'off'" not in src, (
        f"{surface}: per-box autocomplete is the pattern that broke autofill")


def test_the_field_is_not_hidden_from_the_browser(surface):
    """A field the browser considers invisible is skipped by autofill. The
    input is transparent ON PURPOSE — it must never become display:none,
    visibility:hidden or opacity:0."""
    css = STYLES[surface].read_text(encoding="utf-8")
    cls = INPUT_CLASS[surface]
    start = css.index("." + cls)
    block = css[start:css.index("}", start)]
    for banned in ("display: none", "visibility: hidden", "opacity: 0"):
        assert banned not in block, f"{surface}: {banned} would disable autofill"


def test_persian_digits_are_still_normalised(surface):
    """A code pasted as «۱۲۳۴۵۶» has to become 123456 before it is submitted."""
    src = SURFACES[surface].read_text(encoding="utf-8")
    assert "onlyDigits(" in src
