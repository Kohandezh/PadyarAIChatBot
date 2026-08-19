"""Editing a visitor's work profile after verification.

The loop this closes: register → plan → change your mind → new plan. The
security question it answers: can a challenge id that never passed a code be
used to write into that row? It must not.
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
    with TestClient(app) as c:
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
        conn.commit()
    finally:
        conn.close()


def _verified(client, outbox, **profile):
    """A challenge that has passed its code — the only editable state."""
    body = {"destination": DEST, "first_name": "علی", "last_name": "احمدی"}
    body.update(profile)
    r = client.post("/api/auth/otp/request", json=body)
    assert r.status_code == 200, r.text
    cid = r.json()["challenge_id"]
    code = outbox[-1][1]
    v = client.post("/api/auth/otp/verify", json={"challenge_id": cid, "code": code})
    assert v.status_code == 200, v.text
    return cid


# ── The loop ─────────────────────────────────────────────────────────────

def test_edit_replaces_the_profile_and_the_plan_follows(client, outbox):
    cid = _verified(client, outbox, job="خبرنگار", interests="رسانه")

    before = client.post("/api/visit-plan", json={"challenge_id": cid}).json()
    assert "media-hub" in [s["id"] for s in before["sections"]]

    r = client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "سرمایه‌گذار", "position": "", "interests": "جذب سرمایه",
    })
    assert r.status_code == 200, r.text
    assert r.json()["profile"]["job"] == "سرمایه‌گذار"

    after = client.post("/api/visit-plan", json={"challenge_id": cid}).json()
    ids = [s["id"] for s in after["sections"]]
    assert "capital-cafe" in ids
    assert "media-hub" not in [s["id"] for s in after["sections"] if not s["general"]]


def test_edit_cannot_change_name_or_number(client, outbox):
    cid = _verified(client, outbox, job="خبرنگار")
    masked_before = otp_service.profile_for(cid)["destination_masked"]

    client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "مدیرعامل", "position": "", "interests": "",
    })

    after = otp_service.profile_for(cid)
    assert after["first_name"] == "علی"
    assert after["last_name"] == "احمدی"
    assert after["destination_masked"] == masked_before


def test_clearing_the_profile_is_allowed(client, outbox):
    """Removing every interest must be possible — consent runs both ways."""
    cid = _verified(client, outbox, job="خبرنگار", interests="رسانه")
    r = client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "", "position": "", "interests": "",
    })
    assert r.status_code == 200
    assert otp_service.profile_for(cid)["interests"] == ""
    assert client.post("/api/visit-plan", json={"challenge_id": cid}).json()["matched"] is False


# ── The boundary ─────────────────────────────────────────────────────────

def test_unverified_challenge_cannot_be_edited(client, outbox):
    """A code that was never entered must not unlock the row."""
    r = client.post("/api/auth/otp/request",
                    json={"destination": DEST, "first_name": "ب", "last_name": "ج"})
    cid = r.json()["challenge_id"]

    resp = client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "مدیرعامل", "position": "", "interests": "همه چیز",
    })
    assert resp.status_code == 403

    conn = get_db_connection()
    row = conn.execute("SELECT job FROM otp_challenges WHERE id = ?", (cid,)).fetchone()
    conn.close()
    assert (row["job"] or "") == "", "an unverified row was written"


def test_unknown_challenge_is_refused(client):
    r = client.post("/api/auth/profile", json={
        "challenge_id": "z" * 40, "job": "x", "position": "", "interests": "",
    })
    assert r.status_code == 403


def test_oversized_input_is_refused(client, outbox):
    cid = _verified(client, outbox)
    r = client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "x" * 500, "position": "", "interests": "",
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
    cid = _verified(client, outbox, job="کارمند", position="کارشناس")
    assert otp_service.profile_for(cid)["position"] == "کارشناس"

    client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "کارمند", "position": "مدیر بخش", "interests": "",
    })
    assert otp_service.profile_for(cid)["position"] == "مدیر بخش"


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
