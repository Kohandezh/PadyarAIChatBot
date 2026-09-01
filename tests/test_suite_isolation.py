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


def _parametrized_names(node) -> set:
    """Argument names this function receives from @pytest.mark.parametrize.

    A parametrized argument is PROVIDED, not requested: pytest fills it from
    the decorator's table and never looks up a fixture of that name, which
    is how a test may legitimately take a parameter called `context`
    (test_suggestions.py, 2026-08-31) without waking the sync driver.
    """
    names = set()
    for dec in node.decorator_list:
        if not (isinstance(dec, ast.Call) and dec.args):
            continue
        func = dec.func
        if not (isinstance(func, ast.Attribute) and func.attr == "parametrize"):
            continue
        first = dec.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names |= {p.strip() for p in first.value.split(",") if p.strip()}
        elif isinstance(first, (ast.List, ast.Tuple)):
            for elt in first.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value.strip())
    return names


def _plugin_fixtures_asked_for(path: Path) -> set:
    """The pytest-playwright fixtures this module reaches, read from its own
    function signatures. A fixture is requested by NAME, so the signature is
    where the truth is.

    Only tests and fixtures request fixtures; a plain helper that happens to
    take a `page` argument does not. A module that DEFINES a fixture of that
    name shadows the plugin\'s, which is how both browser test files drive
    Chromium safely. A name PARAMETRIZED onto the function is likewise never
    resolved as a fixture — see _parametrized_names.
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
        asked |= ({a.arg for a in
                   args.posonlyargs + args.args + args.kwonlyargs}
                  - _parametrized_names(node))
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


# ── The scanner's own precision ───────────────────────────────────────────
# A parametrized argument is PROVIDED by the decorator's table, not requested
# from a fixture: pytest never resolves a plugin fixture for a name that
# parametrize fills. Treating those names as fixture asks (the 2026-08-31
# test_suggestions.py `context` parametrize) flags innocent files and trains
# people to weaken this guard. These tests pin both halves of the rule.

def _scan(tmp_path, source):
    import textwrap
    path = tmp_path / "test_sample.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return _plugin_fixtures_asked_for(path)


def test_a_parametrized_name_is_not_a_fixture_ask(tmp_path):
    """`@pytest.mark.parametrize("context", ...)` shadows the plugin fixture
    for that test — the sync driver can never start. Must not flag."""
    assert _scan(tmp_path, """
        import pytest
        CONTEXTS = [{"kind": "entry"}, {"kind": "options"}]

        @pytest.mark.parametrize("context", CONTEXTS)
        def test_kinds(context):
            assert context
    """) == set()


def test_a_parametrized_name_list_is_not_a_fixture_ask(tmp_path):
    assert _scan(tmp_path, """
        import pytest

        @pytest.mark.parametrize(["context", "page"], [(1, 2)])
        def test_pairs(context, page):
            assert context + page
    """) == set()


def test_a_plain_context_argument_is_still_a_fixture_ask(tmp_path):
    """No parametrize, no local fixture: this IS the plugin fixture being
    requested — the poisoning case the guard exists for."""
    assert _scan(tmp_path, """
        def test_asks_the_plugin(context):
            assert context
    """) == {"context"}


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
