"""pytest-playwright's own fixtures must never be used anywhere in this suite.

WHY THIS FILE EXISTS (measured 2026-08-28). Playwright's SYNC api keeps an
event loop running in the main thread, and pytest-playwright hands that driver
out through a SESSION-scoped fixture, so nothing tears it down between test
files. pytest.ini sets `asyncio_mode = auto`, so every later test that calls
`asyncio.run()` then raises "cannot be called from a running event loop".

The first browser test landed under `tests/e2e/`, and pytest collects
directories before sibling files, so it ran FIRST and poisoned everything
after it: `pytest -q` went from 15 failures (all of them the known
needs-PostgreSQL set) to 141. Reproduced with a single one-line browser test:
`tests/test_ai_adapters.py` alone is 47 passed, behind that one test 22 failed.

THE FIXTURE IS THE PROBLEM, NOT THE BROWSER. `tests/test_kiosk_privacy.py` and
`tests/e2e/test_chat_localisation.py` both drive Chromium, and both stay in the
default run, because both use `playwright.async_api` through a browser fixture
they define THEMSELVES. An async browser lives on the loop the test already
has and stops with it.

Keeping the browser tests in the default run is the point. The bug that made
the «گفتگوی جدید» button wipe `#loading-bubble` and leave a permanently dead
chat screen is invisible to a source-string assertion. Only a real browser
sees it, and a directory excluded from the default run is a directory CI never
runs.
"""
import ast
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

# pytest-playwright's own fixtures. Asking for any of them starts the
# session-scoped sync driver that outlives the file that asked.
BROWSER_FIXTURES = {"page", "browser", "context", "browser_context", "playwright"}


def _is_fixture(node) -> bool:
    return any("fixture" in ast.dump(d) for d in node.decorator_list)


def _plugin_fixtures_asked_for(path: Path) -> set:
    """The pytest-playwright fixtures this module reaches, read from its own
    function signatures. A fixture is requested by NAME, so the signature is
    where the truth is.

    Only tests and fixtures request fixtures; a plain helper that happens to
    take a `page` argument does not. A module that DEFINES a fixture of that
    name shadows the plugin\'s, which is how both browser test files drive
    Chromium safely.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    defined, asked = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fixture = _is_fixture(node)
        if fixture:
            defined.add(node.name)
        if not fixture and not node.name.startswith("test_"):
            continue
        args = node.args
        asked |= {a.arg for a in
                  args.posonlyargs + args.args + args.kwonlyargs}
    return (asked & BROWSER_FIXTURES) - defined


def test_no_test_file_uses_the_sync_playwright_fixtures():
    """Every file, `tests/e2e/` included. One `def test_x(page)` anywhere puts
    the 141 failures back, and it fails as a mystery in an unrelated file
    rather than in the one that caused it.
    """
    stray = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        asked = _plugin_fixtures_asked_for(path)
        if asked:
            stray[str(path.relative_to(TESTS))] = sorted(asked)
    assert stray == {}, (
        f"pytest-playwright fixtures used in the suite: {stray}")


def test_the_browser_tests_are_actually_collected():
    """The other half of the rule. Banning the sync fixtures is only worth
    something while the browser tests still RUN by default. An exclusion added
    to make the suite green would quietly take the one check that catches a
    dead chat screen out of CI, and nothing would say so.

    This collects with NO path argument on purpose. Naming `tests/e2e` on the
    command line proves only that the directory holds tests, which stays true
    after somebody adds it to `norecursedirs` — the exact change this test is
    here to catch. What has to be asserted is that the default run reaches it.
    """
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=TESTS.parent, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stdout[-2000:]
    for wanted in ("e2e/test_chat_localisation.py",
                   "test_kiosk_privacy.py"):
        assert wanted in out.stdout, (
            f"{wanted} is not in the default collection — the browser tests "
            f"stopped running by default.\n{out.stdout[-2000:]}")
