"""A login for an unknown username must cost what a known one costs.

WHAT WAS BROKEN. The login form asks for three things: username, password and
a security answer. For a REAL username the route runs two bcrypt verifies, one
for each secret. For an invented username it ran `timing_equalize`, which did
ONE. So an unauthenticated caller could post guesses, time the responses, and
sort real admin usernames from invented ones by the gap.

That matters here because the username is itself a secret. deploy/env/*.template
tells the operator to pick a non-obvious one, so confirming it removes an
unknown from the credential triple and makes password and security-answer
guessing worth attempting.

The equalizer was right when the login had one secret. Nobody revisited it when
the security answer was added.

WHY THIS TEST COUNTS CALLS INSTEAD OF MEASURING TIME. A wall-clock assertion on
bcrypt is flaky: the suite lowers BCRYPT_ROUNDS, CI machines are noisy, and a
threshold loose enough to never flake is loose enough to miss the bug. The
thing that actually leaked was the NUMBER of bcrypt verifies, so that is what
is asserted. Deterministic, and it fails for the real reason.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

USERNAME = "timingadmin"
PASSWORD = "correct-horse-battery"
ANSWER = "blue"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "timing.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth.security import hash_password, hash_security_answer
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        # bcrypt for BOTH secrets, which is what hash_security_answer produces
        # and therefore what every install has after its first login. A legacy
        # SHA-256 answer would make the real path cost one bcrypt, not two, and
        # that asymmetry is documented in timing_equalize's docstring.
        conn.execute(
            "INSERT OR REPLACE INTO admins (username, password_hash, salt,"
            " security_question, security_answer_hash) VALUES (?,?,?,?,?)",
            (USERNAME, hash_password(PASSWORD), "", "color",
             hash_security_answer(ANSWER)))
        conn.execute("DELETE FROM login_attempts")
        conn.commit()
        conn.close()
        yield c


@pytest.fixture
def count_bcrypt(monkeypatch):
    """Count every bcrypt.checkpw the login path runs."""
    import bcrypt as bcrypt_mod

    calls = []
    real = bcrypt_mod.checkpw

    def counting(secret, stored):
        calls.append(1)
        return real(secret, stored)

    monkeypatch.setattr(bcrypt_mod, "checkpw", counting)
    return calls


def _login(client, username):
    return client.post("/admin/login", json={"username": username,
                                             "password": "wrong-password",
                                             "sec_answer": "wrong-answer"})


def test_unknown_username_costs_the_same_bcrypts_as_a_known_one(
        client, count_bcrypt):
    """The whole finding, in one assertion."""
    _login(client, USERNAME)
    known = len(count_bcrypt)

    count_bcrypt.clear()
    _login(client, "no-such-admin-anywhere")
    unknown = len(count_bcrypt)

    assert known == unknown, (
        f"A known username costs {known} bcrypt verify(s) and an unknown one "
        f"costs {unknown}. The difference is measurable over the network and "
        "tells an attacker which admin usernames exist. Fix "
        "app.auth.security.timing_equalize so it spends the same cost the "
        "real path spends."
    )


def test_both_paths_actually_run_bcrypt(client, count_bcrypt):
    """Guards the test above from passing because neither path hashes at all.

    If a refactor made both branches cost zero, the equality assertion would
    still pass while the protection was gone.
    """
    _login(client, USERNAME)
    assert len(count_bcrypt) >= 2, (
        "The known-username path should verify a password AND a security "
        f"answer, so at least 2 bcrypt calls. Saw {len(count_bcrypt)}."
    )


def test_neither_path_reveals_which_secret_was_wrong(client):
    """A different message per failure reason would leak the same fact."""
    known = _login(client, USERNAME)
    unknown = _login(client, "no-such-admin-anywhere")
    assert known.status_code == unknown.status_code
    assert known.json() == unknown.json(), (
        "The response body differs between a real and an invented username, "
        "which leaks the same thing the timing did."
    )
