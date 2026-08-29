"""/openapi.json, /docs and /redoc must not be public on a production install.

WHAT WAS BROKEN
---------------
app/main.py built `FastAPI(...)` with the defaults, so all three were served
to anybody, with no session and no token. One unauthenticated
`GET /openapi.json` returned the complete route table: the obscured admin path
(/secure-panel-inotex), every /admin/api/... endpoint, and the exact request
body each one accepts. The `Referrer-Policy` header the same file sets exists
to keep that panel path out of other people's logs, which is wasted effort
while anyone can simply read it.

WHICH MARKER, AND WHY IT IS TESTED THAT WAY
-------------------------------------------
`PADYAR_ENV`, through `prodcheck.is_production()`. NOT `COOKIE_SECURE`:
app/prodcheck.py spells out why deriving "is this production" from a setting
production itself must set is unsound, because a host that forgot the setting
would classify itself as development. So these tests set PADYAR_ENV.

The switch is read once, when app/main.py is imported, because `app` is built
at module scope. Reloading the module is therefore the only way to test the
real object rather than a stand-in, and the stand-in is what would let this
regress. The reload is undone afterwards so the rest of the suite keeps the
singleton it expects.

The client is deliberately NOT used as a context manager. Entering it runs the
lifespan, and the lifespan calls prodcheck.enforce_at_startup(), which refuses
to boot a production install configured like a development one. That refusal
is correct and is tested elsewhere; here it would just stop the test.
"""
import contextlib
import importlib
import os

import pytest
from fastapi.testclient import TestClient


DOC_PATHS = ("/openapi.json", "/docs", "/redoc")


@pytest.fixture(autouse=True)
def _isolated_databases(tmp_path, monkeypatch):
    """Keep the request-log write from a 404 out of the developer's own DB.

    A 404 is an "interesting" response, so app/main.py's request_correlation
    middleware records a row for it. applog never raises, so a missing table
    is harmless, but it must not write into the real store either.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "docs.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "docs-logs.db"))
    yield


@contextlib.contextmanager
def _app_declared_as(environment):
    """The real FastAPI object, rebuilt with PADYAR_ENV set to `environment`.

    The environment is restored and the module reloaded a second time on the
    way out. app/main.py's `app` is a module-level singleton the whole suite
    shares, so leaving a production-built one behind would hide /openapi.json
    from every test that runs after this file.
    """
    import app.main
    previous = os.environ.get("PADYAR_ENV")
    os.environ["PADYAR_ENV"] = environment
    try:
        yield importlib.reload(app.main).app
    finally:
        if previous is None:
            os.environ.pop("PADYAR_ENV", None)
        else:
            os.environ["PADYAR_ENV"] = previous
        importlib.reload(app.main)


# ── The three paths ──────────────────────────────────────────────────────

def test_production_serves_no_api_documentation():
    """404, not 401: the route is removed, so nothing hints it was ever here."""
    with _app_declared_as("production") as fastapi_app:
        client = TestClient(fastapi_app)
        for path in DOC_PATHS:
            assert client.get(path).status_code == 404, path


def test_production_does_not_leak_the_admin_panel_path():
    """The specific harm, named.

    The panel path is obscured on purpose and the schema published it in full.
    Asserting on the body as well as the status code means a future change
    that serves the schema from some other address still fails here.
    """
    with _app_declared_as("production") as fastapi_app:
        client = TestClient(fastapi_app)
        body = client.get("/openapi.json").text
        assert "/secure-panel-inotex" not in body
        assert "/admin/api" not in body


def test_staging_serves_no_api_documentation_either():
    """Staging is treated as production here, and it has to be.

    prodcheck.audit() runs every other security rule with
    `strict = env in (STAGING, PRODUCTION)`, because a staging box is reachable
    from the internet and is built to look like the real install. It carries
    the same obscured admin path. A first version of this switch asked
    `is_production()` and left staging publishing the whole route table, which
    would have leaked exactly what the production switch was added to hide.
    """
    with _app_declared_as("staging") as fastapi_app:
        client = TestClient(fastapi_app)
        for path in DOC_PATHS:
            assert client.get(path).status_code == 404, path


def test_development_keeps_the_documentation():
    """The switch must not cost a developer the tool they use every day."""
    with _app_declared_as("development") as fastapi_app:
        client = TestClient(fastapi_app)
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert "/admin/api/visitors" in response.text
        assert client.get("/docs").status_code == 200


# ── The switch itself ────────────────────────────────────────────────────

def test_an_unreadable_environment_value_hides_the_docs(monkeypatch):
    """A typo like PADYAR_ENV=prod must not publish the API.

    prodcheck.environment() raises on an unrecognised value, and that raise
    stops the boot a moment later with a clear message. This decision is made
    at import time, before that, so it swallows the error and chooses the safe
    answer instead of turning a typo into a crash with a worse message.
    """
    import app.main as main
    monkeypatch.setenv("PADYAR_ENV", "prod")
    assert main._api_docs_enabled() is False


def test_the_marker_is_padyar_env_and_not_cookie_secure(monkeypatch):
    """COOKIE_SECURE must not be able to switch this off.

    app/prodcheck.py: a gate must never be disabled by the thing it guards
    against, and COOKIE_SECURE is one of the settings that gate CHECKS. A
    production host that forgot it would otherwise publish its whole API.
    """
    import app.main as main
    monkeypatch.setenv("PADYAR_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    assert main._api_docs_enabled() is False
