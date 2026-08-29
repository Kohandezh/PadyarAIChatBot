"""Every admin mutation the browser sends must carry the CSRF token.

WHAT WAS BROKEN. The sidebar's logout link called a plain `fetch` instead of
the `fetchAuth` wrapper that attaches `X-CSRF-Token`. `POST /admin/logout` is
inside `PROTECTED_PREFIXES` and is not in `EXEMPT_PATHS`, so the CSRF
middleware answered 403 before the route ran. `DELETE FROM admin_sessions`
never executed, the cookie was never cleared, and the page redirected to the
login screen anyway. `verify_admin` then found the session still valid and
bounced the operator straight back to the dashboard.

So the one control an operator has to end an admin session did nothing, on a
product whose admin laptops sit in an exhibition hall. The dataset reload
button in the same file was broken the same way and had been failing silently.

WHY A SOURCE SCAN AND NOT A REQUEST TEST. There already was a request test:
tests/test_session_lifetime.py posts to /admin/logout and asserts the session
dies. It passed the whole time, because it sends `X-CSRF-Token` itself. The
shipped JavaScript did not. A test that speaks HTTP directly cannot see a bug
that lives in what the browser sends, so this one reads the browser's code.

WHAT IT ENFORCES. In static/admin/js, a `fetch()` that carries a mutating
method must be `fetchAuth()`. Two exceptions are listed below with reasons.
Reads are untouched: the middleware only guards POST/PUT/PATCH/DELETE.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_JS_DIR = os.path.join(REPO_ROOT, "static", "admin", "js")

# `fetch(` only. `fetchAuth(` does not match: the identifier characters between
# "fetch" and "(" break the pattern, which is exactly the distinction we want.
BARE_FETCH = re.compile(r"\bfetch\s*\(")

# Enough of the call to reach its options object without running a JS parser.
LOOKAHEAD = 400

MUTATING = re.compile(r"""method\s*:\s*['"](POST|PUT|PATCH|DELETE)['"]""",
                      re.IGNORECASE)

# Each entry is (file, url fragment, why it is allowed to skip the wrapper).
ALLOWED = {
    ("auth.js", "/admin/login"):
        "There is no session yet, so there is no session-bound token to send. "
        "app/auth/csrf.py lists it in EXEMPT_PATHS for that reason; brute-force "
        "lockout and credentials protect it instead.",
}
# fetchAuth's own `fetch(url, opts)` in utils.js needs no entry here. Its
# method lives in a variable, so the literal-method scan never flags it. That
# is a limitation of scanning text rather than parsing JS, and it is the safe
# direction to be wrong in: this test can miss a dynamic call, it cannot
# invent one.


def _admin_js_files():
    if not os.path.isdir(ADMIN_JS_DIR):
        pytest.skip("no static/admin/js directory in this checkout")
    for name in sorted(os.listdir(ADMIN_JS_DIR)):
        if name.endswith(".js"):
            yield name, os.path.join(ADMIN_JS_DIR, name)


def _mutating_bare_fetches():
    """(file, line, snippet) for every bare fetch that changes server state."""
    for name, path in _admin_js_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for match in BARE_FETCH.finditer(src):
            window = src[match.start():match.start() + LOOKAHEAD]
            if not MUTATING.search(window):
                continue          # a read; the middleware does not guard it
            line = src.count("\n", 0, match.start()) + 1
            yield name, line, window.replace("\n", " ")[:160]


def _is_allowed(name, snippet):
    for (allowed_file, fragment), _why in ALLOWED.items():
        if name == allowed_file and fragment in snippet:
            return True
    return False


def test_every_admin_mutation_goes_through_fetch_auth():
    """A bare fetch with POST/PUT/PATCH/DELETE is a 403 waiting to happen."""
    offenders = [
        (name, line, snippet)
        for name, line, snippet in _mutating_bare_fetches()
        if not _is_allowed(name, snippet)
    ]
    assert not offenders, (
        "These admin mutations bypass fetchAuth, so they send no "
        "X-CSRF-Token and app/auth/csrf.py answers 403 before the route "
        "runs. Use fetchAuth() from ./utils.js:\n"
        + "\n".join(f"  static/admin/js/{n}:{ln}\n      {s}"
                    for n, ln, s in offenders)
    )


def test_logout_uses_the_wrapper():
    """The specific regression, named, so the failure message is obvious.

    Kept separate from the sweep above because this is the one that let an
    operator believe they had logged out when they had not.
    """
    path = os.path.join(ADMIN_JS_DIR, "auth.js")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    call = re.search(r"fetchAuth\(\s*['\"]/admin/logout['\"]", src)
    assert call, (
        "static/admin/js/auth.js must log out through fetchAuth(). A plain "
        "fetch() is rejected by the CSRF middleware, so the session row "
        "survives and the admin cookie keeps working."
    )


def test_the_allowlist_still_describes_real_code():
    """An allowlist nobody prunes turns into a list of forgotten exemptions."""
    seen = set()
    for name, _line, snippet in _mutating_bare_fetches():
        for key in ALLOWED:
            if name == key[0] and key[1] in snippet:
                seen.add(key)
    stale = set(ALLOWED) - seen
    assert not stale, (
        "These CSRF exemptions no longer match any code. Delete them from "
        f"ALLOWED: {sorted(stale)}"
    )
