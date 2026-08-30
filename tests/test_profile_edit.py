"""Editing a visitor's work profile after verification.

The loop this closes: register → plan → change your mind → new plan. The
security question it answers: can somebody who never passed a code write into
that row? It must not.

Identity is the session cookie `POST /api/auth/otp/verify` mints, so `client`
here is one browser and the cookie it is holding. The `challenge_id` these
tests used to post as proof is gone from both endpoints — see
tests/test_visitor_auth_otp.py for what it can and cannot do now.
"""
import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_db_connection
from app.main import app
from app.services import otp as otp_service

DEST = "+989120000077"


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture()
def client():
    # A browser sends Origin and User-Agent; TestClient sends neither, and
    # every endpoint that acts on the visitor cookie validates the origin.
    with TestClient(app) as c:
        c.headers.update({"Origin": "http://localhost",
                          "User-Agent": "pytest-agent/1.0"})
        yield c


@pytest.fixture(autouse=True)
def _no_ip_throttle(monkeypatch):
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM otp_challenges WHERE destination LIKE '+9891200000%'")
        # A verified challenge is now promoted to a durable `visitors`
        # row (app/routers/otp.py). These three OTP files run against
        # the ambient database, so their test numbers have to be swept
        # out of that table too or they pile up in a real install.
        conn.execute("DELETE FROM visitors WHERE phone LIKE '+9891200000%'")
        conn.commit()
    finally:
        conn.close()


def _verified(client, outbox, **profile):
    """Register for real, so the client ends up holding a session cookie."""
    body = {"destination": DEST, "first_name": "علی", "last_name": "احمدی"}
    body.update(profile)
    r = client.post("/api/auth/otp/request", json=body)
    assert r.status_code == 200, r.text
    cid = r.json()["challenge_id"]
    code = outbox[-1][1]
    v = client.post("/api/auth/otp/verify", json={"challenge_id": cid, "code": code})
    assert v.status_code == 200, v.text
    return cid


def _stored():
    """The durable row — where the profile actually lives now.

    It used to be read back off `otp_challenges`, a table built to expire. The
    edit writes `app.visitors`, which is the copy the exhibition keeps.
    """
    from app.services import conversations
    row = conversations.find_visitor_by_phone(DEST)
    assert row, "no visitor row was written"
    return row


# ── The loop ─────────────────────────────────────────────────────────────

def test_edit_replaces_the_profile_and_the_plan_follows(client, outbox):
    _verified(client, outbox, job="خبرنگار", interests="رسانه")

    # Empty body on purpose: the plan follows the STORED profile, which the
    # session cookie identifies. Nothing in the request names this visitor.
    before = client.post("/api/visit-plan", json={}).json()
    assert "media-hub" in [s["id"] for s in before["sections"]]

    r = client.post("/api/auth/profile", json={
        "job": "سرمایه‌گذار", "position": "مدیر", "interests": "جذب سرمایه",
    })
    assert r.status_code == 200, r.text
    assert r.json()["profile"]["job"] == "سرمایه‌گذار"

    after = client.post("/api/visit-plan", json={}).json()
    ids = [s["id"] for s in after["sections"]]
    assert "capital-cafe" in ids
    assert "media-hub" not in [s["id"] for s in after["sections"] if not s["general"]]


def test_edit_cannot_change_name_or_number(client, outbox):
    _verified(client, outbox, job="خبرنگار")
    masked_before = client.get("/api/auth/session").json()["profile"]["destination_masked"]
    assert masked_before

    client.post("/api/auth/profile", json={
        "job": "مدیرعامل", "position": "مدیر", "interests": "عمومی",
    })

    after = client.get("/api/auth/session").json()["profile"]
    assert after["first_name"] == "علی"
    assert after["last_name"] == "احمدی"
    assert after["destination_masked"] == masked_before
    assert _stored()["job"] == "مدیرعامل"


def test_clearing_the_profile_is_now_rejected(client, outbox):
    """The 3 onboarding questions are mandatory now, not optional plan input.

    A blank submission must be refused, and must not overwrite what was
    already stored.
    """
    _verified(client, outbox, job="خبرنگار", interests="رسانه")
    r = client.post("/api/auth/profile", json={
        "job": "", "position": "", "interests": "",
    })
    assert r.status_code == 422
    assert _stored()["interests"] == "رسانه"


# ── The boundary ─────────────────────────────────────────────────────────

def test_a_challenge_that_never_passed_its_code_unlocks_nothing(client, outbox):
    """A code that was never entered must not unlock anything.

    It used to be the whole credential, so this is the same boundary the file
    always guarded — only the answer moved from 403 to 401, because the
    request is now simply not signed in.
    """
    r = client.post("/api/auth/otp/request",
                    json={"destination": DEST, "first_name": "ب", "last_name": "ج"})
    cid = r.json()["challenge_id"]

    resp = client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "مدیرعامل", "position": "مدیر", "interests": "همه چیز",
    })
    assert resp.status_code == 401

    from app.services import conversations
    assert not conversations.find_visitor_by_phone(DEST), (
        "an unverified registration became a durable visitor")


def test_an_unknown_challenge_is_refused(client):
    r = client.post("/api/auth/profile", json={
        "challenge_id": "z" * 40, "job": "x", "position": "x", "interests": "x",
    })
    assert r.status_code == 401


def test_oversized_input_is_refused(client, outbox):
    _verified(client, outbox)
    r = client.post("/api/auth/profile", json={
        "job": "x" * 500, "position": "", "interests": "",
    })
    assert r.status_code == 422


def test_service_returns_false_rather_than_raising_on_unverified():
    assert otp_service.update_profile("nonexistent-id", "a", "b", "c") is False


# ── Form options ─────────────────────────────────────────────────────────

def test_options_endpoint_serves_the_taxonomy(client):
    r = client.get("/api/registration/options")
    assert r.status_code == 200
    body = r.json()
    assert body["jobs"] and body["interests"] and body["positions"]
    assert all(set(i) == {"id", "label"} for i in body["jobs"])


def test_position_survives_a_profile_edit(client, outbox):
    """سمت is a real field, not a placeholder — it must round-trip."""
    _verified(client, outbox, job="کارمند", position="کارشناس")
    assert _stored()["position"] == "کارشناس"

    client.post("/api/auth/profile", json={
        "job": "کارمند", "position": "مدیر بخش", "interests": "عمومی",
    })
    assert _stored()["position"] == "مدیر بخش"


def test_options_endpoint_is_bilingual(client):
    fa = client.get("/api/registration/options?lang=fa").json()
    en = client.get("/api/registration/options?lang=en").json()
    assert [j["id"] for j in fa["jobs"]] == [j["id"] for j in en["jobs"]]
    assert fa["jobs"][0]["label"] != en["jobs"][0]["label"]


def test_options_endpoint_exposes_no_secrets(client):
    """It is public and unauthenticated — it must carry nothing but choices."""
    text = client.get("/api/registration/options").text
    for leak in ("password", "api_key", "destination", "code_hmac", "keywords"):
        assert leak not in text
