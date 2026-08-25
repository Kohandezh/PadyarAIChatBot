"""The lead-capture scenario, walked end-to-end on the real PostgreSQL backend.

The unit suite runs on SQLite; production is PostgreSQL, and this feature is
exactly the kind that slips between the two (TIMESTAMPTZ comparisons, the
boolean `released_at` guards, migration 0005/0006 tables). One test walks the
whole exhibition day the way the SPEC describes it, because the feature is a
scenario, not a list of endpoints.

The OTP provider is `dev` here, so the code the contact reads out lands in
`data/otp-dev-outbox.log` — the harness reads it back the way the contact
would read their phone.
"""
import os

import pytest

from app.config import BASE_DIR
from app.services import leads as leads_service

DEV_OUTBOX = os.path.join(BASE_DIR, "data", "otp-dev-outbox.log")


@pytest.fixture
def captured_qrs(monkeypatch):
    """Intercept the QR renderer, which is the only place the raw invite URL
    appears. Asserting on what was handed to the booth is more honest than
    parsing it back out of the SVG."""
    seen = []
    monkeypatch.setattr(leads_service, "qr_svg",
                        lambda url: (seen.append(url), "<svg></svg>")[1])
    return seen


def last_otp() -> str:
    assert os.path.exists(DEV_OUTBOX), "dev provider did not write the outbox"
    return open(DEV_OUTBOX, encoding="utf-8").read().strip().rsplit("code=", 1)[1]


def drop_visitor_cookie(client):
    for ck in list(client.cookies.jar):
        if ck.name == leads_service.VISITOR_COOKIE:
            client.cookies.jar.clear(ck.domain, ck.path, ck.name)


@pytest.fixture
def booth(client, captured_qrs):
    """A visitor with a live session, standing at the exhibition.

    Returns the client with the visitor cookie set, plus a helper to switch to
    the contact's phone (which carries no visitor cookie).
    """
    res = client.post("/admin/api/leads/visitors", json={"name": "ب"})
    assert res.status_code == 200, res.text
    code = res.json()["link"].rsplit("/", 1)[1]
    res = client.get(f"/v/{code}", follow_redirects=False)
    assert res.status_code == 303
    visitor_value = res.cookies.get(leads_service.VISITOR_COOKIE)
    assert visitor_value
    # The 303 already stored the cookie host-only; nothing to set.

    def as_contact():
        drop_visitor_cookie(client)
        return client

    def as_visitor():
        drop_visitor_cookie(client)
        client.cookies.set(leads_service.VISITOR_COOKIE, visitor_value)
        return client

    client.as_contact = as_contact
    client.as_visitor = as_visitor
    return client


def _seed_company(conn, cid, title, text):
    conn.execute(
        "INSERT INTO dataset (id, title, text) VALUES (?, ?, ?)"
        " ON CONFLICT (id) DO NOTHING", (cid, title, text))
    conn.commit()


def test_the_exhibition_day(conn, booth, captured_qrs):
    if os.path.exists(DEV_OUTBOX):
        os.remove(DEV_OUTBOX)
    _seed_company(conn, "co1", "شرکت الف", "متن قدیمی")

    # The booth finds the company and registers its contact.
    res = booth.get("/api/leads/companies?q=%D8%A7%D9%84%D9%81")
    assert "co1" in [c["id"] for c in res.json()["companies"]]
    res = booth.post("/api/leads/register", json={
        "dataset_id": "co1", "first_name": "آقای الف", "phone": "09121234567"})
    assert res.status_code == 200, res.text
    lead_id = res.json()["lead_id"]

    # Unverified does NOT own the company yet: another booth still finds it.
    res = booth.get("/api/leads/companies?q=%D8%A7%D9%84%D9%81")
    assert "co1" in [c["id"] for c in res.json()["companies"]]

    # The contact reads the code out; the booth types it in.
    res = booth.post("/api/leads/verify", json={"lead_id": lead_id, "code": last_otp()})
    assert res.status_code == 200, res.text
    invite_token = captured_qrs[-1].rsplit("/", 1)[1]

    # Now the company is gone from EVERY visitor's search.
    res = booth.get("/api/leads/companies?q=%D8%A7%D9%84%D9%81")
    assert "co1" not in [c["id"] for c in res.json()["companies"]]

    # The contact opens the link on their own phone, sees the live text,
    # rewrites it. Opening does not burn; a successful submit does.
    phone = booth.as_contact()
    res = phone.get(f"/api/leads/edit/{invite_token}")
    assert res.status_code == 200
    assert res.json()["text"] == "متن قدیمی"
    assert res.json()["pending"] is False
    res = phone.post(f"/api/leads/edit/{invite_token}", json={"text": "متن تازه شرکت"})
    assert res.status_code == 200, res.text
    res = phone.get(f"/edit/{invite_token}")
    assert res.status_code == 410
    assert "اینجا چیزی برای نمایش نیست" in res.text

    # Only `text` is accepted from the contact — anything else is a refusal.
    res = phone.post(f"/api/leads/edit/{invite_token}", json={"title": "x"})
    assert res.status_code in (400, 410)

    # The admin approves, and the dataset the chatbot serves changes.
    res = booth.as_visitor().get("/admin/api/leads/edits")
    edit_id = res.json()["edits"][0]["id"]
    res = booth.post(f"/admin/api/leads/edits/{edit_id}", json={"approve": True})
    assert res.status_code == 200, res.text
    live = conn.execute("SELECT text FROM dataset WHERE id = 'co1'").fetchone()["text"]
    assert live == "متن تازه شرکت"


def test_release_returns_the_company_and_kills_the_invite(conn, booth, captured_qrs):
    if os.path.exists(DEV_OUTBOX):
        os.remove(DEV_OUTBOX)
    _seed_company(conn, "co2", "شرکت ب", "متن دوم")

    booth.as_visitor()
    res = booth.post("/api/leads/register", json={
        "dataset_id": "co2", "first_name": "آقای ب", "phone": "09129876543"})
    lead_id = res.json()["lead_id"]
    res = booth.post("/api/leads/verify",
                     json={"lead_id": lead_id, "code": last_otp()})
    assert res.status_code == 200, res.text
    token = captured_qrs[-1].rsplit("/", 1)[1]

    # The admin frees the unreachable company.
    res = booth.post(f"/admin/api/leads/{lead_id}/release")
    assert res.status_code == 200, res.text
    res = booth.get("/api/leads/companies?q=%D8%A8")
    assert "co2" in [c["id"] for c in res.json()["companies"]]

    # The old invite died with the release. It is DELETEd, not marked used, so
    # the status is 404 (unknown) rather than 410 (burned) — but the page the
    # contact sees is the same dead end either way.
    res = booth.as_contact().get(f"/edit/{token}")
    assert res.status_code in (404, 410)
    assert "اینجا چیزی برای نمایش نیست" in res.text


def test_funnel_matches_the_stuck_list_after_a_release(conn, booth, captured_qrs):
    """`stuck_leads()` promises its list and the funnel's `verified` number are
    always the same number. A released registration keeps `status='verified'`
    (only `released_at` moves), so the funnel still counts it while the stuck
    list rightly drops it — the two numbers the operator is told to trust
    disagree after the first release."""
    if os.path.exists(DEV_OUTBOX):
        os.remove(DEV_OUTBOX)
    _seed_company(conn, "co3", "شرکت ج", "متن سوم")

    booth.as_visitor()
    res = booth.post("/api/leads/register", json={
        "dataset_id": "co3", "first_name": "آقای ج", "phone": "09121110002"})
    lead_id = res.json()["lead_id"]
    res = booth.post("/api/leads/verify",
                     json={"lead_id": lead_id, "code": last_otp()})
    assert res.status_code == 200, res.text

    funnel = booth.get("/admin/api/leads/funnel").json()
    stuck = booth.get("/admin/api/leads/stuck").json()["stuck"]
    assert funnel["verified"] == len(stuck) == 1

    res = booth.post(f"/admin/api/leads/{lead_id}/release")
    assert res.status_code == 200, res.text

    funnel = booth.get("/admin/api/leads/funnel").json()
    stuck = booth.get("/admin/api/leads/stuck").json()["stuck"]
    assert funnel["verified"] == len(stuck), (
        f"funnel says {funnel['verified']} verified, stuck list has {len(stuck)} — "
        "the released registration is counted by one and not the other")
