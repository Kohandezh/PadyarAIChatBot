"""The registration profile step, against a real PostgreSQL server.

WHY THIS EXISTS
---------------
`update_profile()` consumed its verified row with `WHERE ... AND used = 1`.
`used` is a real BOOLEAN (`migrations/0001_initial.sql`), and PostgreSQL has no
`boolean = integer` operator, so the statement raised UndefinedFunction and
`POST /api/auth/profile` answered 500 on every PostgreSQL install.

SQLite could not see it. There TRUE is an alias for 1, so `used = 1` is correct
and `tests/test_profile_edit.py` passes against the broken code. Only a real
server fails, which is the whole reason this directory exists.

`tests/test_sql_boolean_portability.py` guards the same bug by reading the
source. This proves the behaviour instead: the statement really does execute,
and the visitor's profile really is written.
"""
import pytest


DEST = "+989120000077"


@pytest.fixture()
def outbox(monkeypatch):
    """Capture the code instead of sending an SMS, as tests/test_profile_edit.py does."""
    sent = []
    from app.services import otp as otp_service
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture()
def verified(pg_clean, outbox):
    """A challenge that has passed its code: the only editable state.

    Driven through the real service calls rather than an INSERT, so the row
    under test is shaped exactly the way production shapes it, including
    whatever `verify()` writes into `used`.
    """
    from app.services import otp as otp_service

    issued = otp_service.request_challenge(DEST, first_name="علی", last_name="احمدی")
    cid = issued["challenge_id"]
    ok, message = otp_service.verify(cid, outbox[-1][1])
    assert ok, message
    return cid


def test_verify_stores_a_real_boolean_not_an_integer(verified, raw):
    """The premise of the WHERE clause: `used` holds `True`, not `1`."""
    row = raw.execute("SELECT used FROM otp_challenges WHERE id = %s",
                      (verified,)).fetchone()
    # `is True`, not `== True`: 1 == True in Python, so equality would pass
    # against exactly the value this asserts is absent.
    assert row[0] is True


def test_update_profile_writes_the_visitor_profile(verified):
    """The service call that raised UndefinedFunction before the fix."""
    from app.services import otp as otp_service

    assert otp_service.update_profile(verified, "مهندس", "مدیر فنی", "رباتیک") is True

    stored = otp_service.profile_for(verified)
    assert stored["job"] == "مهندس"
    assert stored["position"] == "مدیر فنی"
    assert stored["interests"] == "رباتیک"


def test_the_profile_endpoint_answers_200(verified):
    """The reported symptom: POST /api/auth/profile 500'd on PostgreSQL."""
    from fastapi.testclient import TestClient

    import app.routers.otp as otp_router
    from app.main import app

    otp_router.check_rate_limit = lambda request: None
    with TestClient(app) as c:
        r = c.post("/api/auth/profile", json={
            "challenge_id": verified,
            "job": "مهندس", "position": "مدیر فنی", "interests": "رباتیک",
        })
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    assert r.json()["updated"] is True


def test_an_unverified_challenge_is_still_refused(pg_clean, outbox):
    """The fix must not weaken the guard it sits in.

    `used = TRUE` has to keep rejecting a challenge that never passed a code,
    exactly as `used = 1` did on SQLite. A fix that made every row match would
    satisfy the tests above and hand any visitor someone else's row.
    """
    from app.services import otp as otp_service

    issued = otp_service.request_challenge(DEST, first_name="بدون", last_name="تأیید")
    assert otp_service.update_profile(issued["challenge_id"], "x", "y", "z") is False


def test_the_sqlite_idiom_is_a_hard_error_on_this_column(verified, raw):
    """Pin the failure itself, so nobody reintroduces `used = 1` as portable."""
    from psycopg import errors

    with pytest.raises(errors.UndefinedFunction):
        raw.execute("SELECT id FROM otp_challenges WHERE used = 1")
