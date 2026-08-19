"""Smoke test — confirms the pytest + FastAPI TestClient toolchain is wired up.

This is intentionally minimal. Real coverage lives in dedicated modules
(see the `write-tests` / `api-test` skills). It just proves the app boots
under TestClient and the chat-token primitive works.
"""
from fastapi.testclient import TestClient

from app.main import app
from app.auth.security import generate_chat_token


def test_app_boots():
    # Use TestClient as a context manager so startup/shutdown lifespan events
    # actually run and clean up — without `with`, shutdown never fires and
    # resources (DB connections, background tasks) leak.
    with TestClient(app) as client:
        # Any non-5xx response proves the app imported and is serving requests.
        assert client.get("/").status_code < 500


def test_chat_token_has_expected_shape():
    token = generate_chat_token()
    # Format is "<timestamp>.<signature>".
    assert token.count(".") == 1


# ── Unauthenticated endpoints must not 500 ──────────────────────────────
# These are the endpoints an orchestrator, load balancer or visitor's browser
# hits without credentials. They had NO coverage, and it cost us twice:
#
#   * `/api/dataset` 500'd on PostgreSQL for weeks (`ORDER BY rowid`), and
#   * `/api/ready` 500'd with a NameError after a class rename, because the
#     symbol was only referenced at module scope and nothing imported it.
#
# A green suite that never calls these proves nothing about them. This is the
# cheapest possible guard: it does not assert content, only that the endpoint
# does not blow up.

import pytest


@pytest.mark.parametrize("path", [
    "/",
    "/api/health",
    "/api/ready",
    "/api/ready?deep=true",     # the deep branch is a SEPARATE code path
    "/api/dataset",
    "/api/questions",
    "/api/voice-status",
    "/api/auth/registration-status",
])
def test_public_endpoints_do_not_return_a_server_error(path):
    with TestClient(app) as client:
        res = client.get(path)
        assert res.status_code < 500, f"{path} -> {res.status_code}\n{res.text[:300]}"
