"""Every asset stamped with ?v={{ asset_version }} must feed that version.

WHAT WAS BROKEN. _asset_version() in app/routers/public.py builds the
cache-buster from the newest mtime of a hand-written list of files. The list
had three entries: static/chat/base.css, themes/<theme>/static/style.css and
static/chat/core.js. But the theme footers stamp that same token onto three
more files, and static/companion/registration.js is one of them.

registration.js carries the whole client half of visitor sign-in: the
GET /api/auth/session probe, ChatConfig.signInRequiredFn (the 401 handler),
ChatConfig.sendGateFn and the logout button. Change only that file and the
generated URL is byte-identical, so nothing sends Cache-Control for /static
and the kiosk browser keeps the copy it already has. The server enforces this
week's sign-in rules while the screen in the exhibition hall enforces last
week's. That is a security fix that silently does not ship.

WHY THE TEST IS SHAPED THIS WAY. Asserting "the list has six entries" would
pass forever and catch nothing. So the test reads the truth from the templates
instead: it greps every theme .html file for the stamp, then proves each file
it finds really moves the version. A seventh asset added to a footer next year
fails this test on the day it is added, before it reaches a kiosk.

The check runs against a throwaway copy of the tree (BASE_DIR is monkeypatched)
because it has to change mtimes to see the effect, and it must never touch the
real repository files.
"""
import os
import re

import pytest

from app.routers import public

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEMES_DIR = os.path.join(REPO_ROOT, "themes")

# Matches src="/some/path.js?v={{ asset_version ... }}" and the href form.
# The URL group stops at the "?", so it is the plain path we have to resolve.
STAMPED = re.compile(r'(?:src|href)\s*=\s*"(/[^"?]+)\?v=\{\{\s*asset_version')

# The token the templates write for the active theme's own directory.
THEME_NAME_VAR = re.compile(r"\{\{\s*theme_name\s*\}\}")


def _theme_names():
    """Selectable themes. `base` is skipped on purpose.

    base is marked "selectable": false and only supplies default partials, so
    it is never rendered on its own. Its partials are folded into every theme
    below instead, which is how they are actually served.
    """
    names = []
    for name in sorted(os.listdir(THEMES_DIR)):
        if name == "base":
            continue
        if os.path.isfile(os.path.join(THEMES_DIR, name, "theme.json")):
            names.append(name)
    return names


def _html_files(theme_name: str):
    """Every template that can be rendered for this theme.

    Both the theme's own files and base's, because a theme that does not
    override a partial gets base's copy verbatim.
    """
    for root_dir in (os.path.join(THEMES_DIR, "base"),
                     os.path.join(THEMES_DIR, theme_name)):
        for root, _dirs, names in os.walk(root_dir):
            for name in names:
                if name.endswith(".html"):
                    yield os.path.join(root, name)


def _stamped_assets(theme_name: str):
    """Repo-relative paths of every asset this theme stamps with the token."""
    found = set()
    for path in _html_files(theme_name):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for url in STAMPED.findall(src):
            url = THEME_NAME_VAR.sub(theme_name, url)
            found.add(url.lstrip("/"))
    return sorted(found)


# One case per (theme, stamped asset). Built at import time so a missing asset
# shows up as its own named failure instead of hiding inside a loop.
CASES = [(theme, asset)
         for theme in _theme_names()
         for asset in _stamped_assets(theme)]


def _fake_tree(tmp_path, assets):
    """Write every asset into a throwaway BASE_DIR, all with the same mtime.

    Returns that shared mtime. Real content does not matter: _asset_version
    only reads mtimes.
    """
    base_mtime = 1_700_000_000
    for rel in assets:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        os.utime(path, (base_mtime, base_mtime))
    return base_mtime


def test_there_is_something_to_check():
    """Guards the two tests below from passing because the regex went stale."""
    assert CASES, (
        "No stamped assets found under themes/. Either the templates stopped "
        "using ?v={{ asset_version }} or the STAMPED regex no longer matches "
        "them, and both make the tests below vacuous."
    )


@pytest.mark.parametrize("theme_name,asset", CASES,
                         ids=[f"{t}:{a}" for t, a in CASES])
def test_every_stamped_asset_moves_the_version(tmp_path, monkeypatch,
                                               theme_name, asset):
    """Touch one stamped file and the ?v= token must change.

    If it does not, that file is missing from the list in _asset_version and
    a change to it ships to a URL the browser already has cached.
    """
    assets = _stamped_assets(theme_name)
    base_mtime = _fake_tree(tmp_path, assets)
    monkeypatch.setattr(public, "BASE_DIR", str(tmp_path))

    before = public._asset_version(theme_name)
    touched = base_mtime + 1000
    os.utime(tmp_path / asset, (touched, touched))
    after = public._asset_version(theme_name)

    assert after != before, (
        f"{asset} is stamped with ?v={{{{ asset_version }}}} in a {theme_name} "
        f"template, but editing it does not change the token (still {before}). "
        f"Every kiosk keeps the cached copy. Add it to the paths list in "
        f"_asset_version() in app/routers/public.py."
    )
    assert after == str(touched)


def test_touching_registration_js_changes_the_version(tmp_path, monkeypatch):
    """The exact file the bug was found on, named so the report is readable.

    registration.js holds the sign-in gate. A fix to it that reuses the old
    ?v= value is a fix that never reaches the visitor's browser.
    """
    asset = "static/companion/registration.js"
    themes = [t for t in _theme_names() if asset in _stamped_assets(t)]
    assert themes, (
        f"No theme stamps {asset} any more. If it is genuinely no longer "
        "loaded, delete this test; if it is loaded without the token, it can "
        "never be refreshed at all."
    )

    theme_name = themes[0]
    assets = _stamped_assets(theme_name)
    base_mtime = _fake_tree(tmp_path, assets)
    monkeypatch.setattr(public, "BASE_DIR", str(tmp_path))

    before = public._asset_version(theme_name)
    touched = base_mtime + 1000
    os.utime(tmp_path / asset, (touched, touched))

    assert public._asset_version(theme_name) == str(touched), (
        "Editing the sign-in gate must produce a new asset URL. It did not, "
        f"so browsers keep running the old {asset}."
    )
