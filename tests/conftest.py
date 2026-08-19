"""Shared pytest fixtures and setup for the PadyarAIChatbot test suite.

Imported automatically by pytest before any test module. Anything that must be
true *before* `app.*` is imported (e.g. environment defaults) goes here.
"""
import os

import pytest

# Let the app import even without a .env file. Real OpenAI/GapGPT network calls
# are mocked in tests, so a dummy key is sufficient for import-time config.
os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key")

# The suite covers every module this project SHIPS, independently of which
# ones a particular installation happens to have switched on in its .env
# (e.g. the registration/OTP module is built and tested but not enabled here
# yet). Empty = load all optional modules. Set before app.config imports
# dotenv, which does not override variables already present in the process.
os.environ["ENABLED_MODULES"] = ""

# The suite runs against throwaway SQLite files. Production runtime is
# PostgreSQL (DB_BACKEND=postgres); forcing sqlite here keeps every test
# hermetic and stops the suite from writing into the real database.
os.environ["DB_BACKEND"] = "sqlite"

# bcrypt at production strength costs ~580 ms per hash on this machine, and
# init_db() performs one for the auto-generated admin. With ~130 fixtures
# calling init_db() that single line dominated the suite. 4 rounds keeps the
# SAME code path and the same verification logic, just without the deliberate
# slowness. Production keeps 12 — see app/auth/security.py.
os.environ["BCRYPT_ROUNDS"] = "4"

# The OTP per-destination hourly cap is read ONCE at import time in
# app/services/otp.py, and the whole suite shares one process. Left to the
# developer's .env, the value silently decides whether unrelated tests pass:
# lowering it from 100 to 5 (the correct production value) made
# test_profile_edit fail only when run after test_otp had already spent the
# quota on the same number. Pinning it here makes the suite independent of
# local configuration. The test that exercises the limit reads the module
# constant, so it stays correct at any value.
# 50, not 500: test_destination_hourly_rate_limit loops up to this value, so a
# large pin turned one test into a 15-second loop. 50 is far above what any
# other test consumes and keeps that test honest.
os.environ["OTP_DEST_HOURLY_LIMIT"] = "50"


@pytest.fixture(autouse=True)
def _never_touch_the_real_env_file(tmp_path, monkeypatch):
    """Redirect the app's `.env` to a throwaway file for EVERY test.

    Saving the SMS settings writes the project's environment file
    (app/services/secure_store.py). No test may touch the developer's real
    .env, so the path every writer resolves is redirected before any test body
    runs — the same idiom the suite already uses for DB_PATH.
    """
    import app.config as config
    monkeypatch.setattr(config, "ENV_FILE", str(tmp_path / ".env"))


@pytest.fixture(autouse=True)
def _never_touch_the_real_log_db(tmp_path, monkeypatch):
    """Redirect the log store to a throwaway file for EVERY test.

    Instrumentation now writes a row on almost every code path, so without this
    a single `pytest` run pours hundreds of synthetic rows into the operator's
    real application_logs.db — polluting the evidence an incident investigation
    would rely on. Verified: a run before this fixture existed left 409 rows in
    the live database.

    Same idiom, and same reason, as the ENV_FILE fixture above.
    """
    import app.config as config
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "application_logs.db"))


@pytest.fixture(autouse=True)
def _reset_log_storm_suppression():
    """Clear applog's duplicate-suppression window between tests.

    applog collapses more than ~20 identical (category, event, level) rows
    inside a 10-second window so a provider outage cannot write a million rows.
    That counter is module-level state and it does NOT belong to any one test:
    a file with 49 tests that each create a backup emits `backup.started` far
    past the threshold in well under 10 seconds, so a later test finds its rows
    legitimately suppressed and fails for a reason that has nothing to do with
    what it is asserting.

    Real behaviour, wrong scope. Reset it per test rather than weakening either
    the suppression or the tests that depend on their own rows existing.
    """
    from app.services import applog
    applog._recent.clear()
    yield
    applog._recent.clear()
