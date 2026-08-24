"""The two signals the lead list has to make visible (SPEC REQ-080).

The reward is paid on `verified`, and a visitor reaches `verified` alone with a
spare SIM. `company_leads.ip` and `company_leads.user_agent` have been written
on every registration since the feature was built and read by nothing, so these
tests hold down that they are served, that they are served ONLY to an admin,
and that the two clusters an operator cannot see by scrolling are computed.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.services.leads import MIN_SECONDS_BETWEEN_CAPTURES


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "signals.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    from app.services import leads as leads_service
    leads_service.ensure_tables()
    return tmp_path


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    import app.routers.leads as leads_router
    monkeypatch.setattr(leads_router, "check_rate_limit",
                        lambda request, key=None, limit=None: None)


@pytest.fixture
def outbox(monkeypatch):
    from app.services import otp as otp_service
    sent = []
    monkeypatch.setattr(otp_service, "_deliver",
                        lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture
def client(paths):
    from app.main import app
    with TestClient(app) as c:
        yield c


def _admin(client):
    import secrets
    from app.config import ADMIN_COOKIE_NAME
    from app.auth.csrf import token_for_session
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    client.headers.update({"X-CSRF-Token": token_for_session(token)})


def _capture(lead_id, visitor_id, created_at, ip="", user_agent="",
             status="verified"):
    """A registration as the running product writes one."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
        " first_name, phone, phone_hash, status, created_at, ip, user_agent)"
        " VALUES (?, ?, ?, ?, 'مخاطب', '+989120000000', 'h', ?, ?, ?, ?)",
        (lead_id, f"co-{lead_id}", f"شرکت {lead_id}", visitor_id, status,
         created_at, ip, user_agent))
    conn.commit()
    conn.close()


def _visitor(name):
    from app.services import leads as leads_service
    return leads_service.create_visitor(name)


def _rows(client):
    body = client.get("/admin/api/leads").json()
    return {r["id"]: r for r in body["leads"]}, body


IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"


# ── The columns reach the admin, and nobody else ────────────────────────

def test_the_admin_list_serves_the_two_columns(client, outbox):
    """They are written on every registration and were read by nothing."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("INSERT INTO dataset (id, title, text, video_url, title_en,"
                 " text_en, position) VALUES ('co-1', 'یک شرکت', 'م', '', '', '', 10)")
    conn.commit()
    conn.close()

    v = _visitor("همکار")
    client.get(f"/v/{v['code']}", follow_redirects=False)
    made = client.post("/api/leads/register", json={
        "dataset_id": "co-1", "first_name": "مینا", "last_name": "ر",
        "position": "مدیر", "phone": "09121110044"},
        headers={"User-Agent": IPHONE})
    assert made.status_code == 200, made.text

    _admin(client)
    rows, body = _rows(client)
    row = rows[made.json()["lead_id"]]
    assert row["ip"]
    assert row["user_agent"] == IPHONE
    # The threshold travels with the data so the screen cannot state a rule
    # different from the one that ran.
    assert body["fast_capture_seconds"] == MIN_SECONDS_BETWEEN_CAPTURES


def test_a_visitor_never_sees_the_signals_about_themselves(client, outbox):
    v = _visitor("همکار")
    client.get(f"/v/{v['code']}", follow_redirects=False)
    _capture("l1", v["id"], "2026-08-24T09:00:00", ip="10.0.0.1", user_agent=IPHONE)
    mine = client.get("/api/leads/mine").json()["leads"]
    assert mine and all(
        key not in mine[0]
        for key in ("ip", "user_agent", "shared_device", "too_fast"))


# ── One device, two badges ──────────────────────────────────────────────

def test_a_device_shared_by_two_visitors_is_flagged(client):
    a, b = _visitor("همکار الف"), _visitor("همکار ب")
    _capture("l1", a["id"], "2026-08-24T09:00:00", ip="10.0.0.1", user_agent=IPHONE)
    _capture("l2", b["id"], "2026-08-24T09:05:00", ip="10.0.0.1", user_agent=IPHONE)
    _capture("l3", a["id"], "2026-08-24T10:00:00", ip="10.0.0.9", user_agent="Other/1.0")
    _admin(client)
    rows, _ = _rows(client)
    assert rows["l1"]["shared_device"] is True
    assert rows["l1"]["shared_device_visitors"] == 2
    assert rows["l2"]["shared_device"] is True
    assert rows["l3"]["shared_device"] is False


def test_one_visitor_working_all_day_from_one_phone_is_not_flagged(client):
    """The address and the device belong to the visitor's own phone, so every
    row of theirs shares them. Flagging that would flag everything."""
    a = _visitor("همکار الف")
    for i in range(4):
        _capture(f"l{i}", a["id"], f"2026-08-24T09:0{i}:00",
                 ip="10.0.0.1", user_agent=IPHONE)
    _admin(client)
    rows, _ = _rows(client)
    assert all(r["shared_device"] is False for r in rows.values())


def test_a_shared_address_alone_is_not_a_signal(client):
    """A whole hall sits behind one NAT. Only the pair is compared."""
    a, b = _visitor("همکار الف"), _visitor("همکار ب")
    _capture("l1", a["id"], "2026-08-24T09:00:00", ip="10.0.0.1", user_agent=IPHONE)
    _capture("l2", b["id"], "2026-08-24T09:05:00", ip="10.0.0.1",
             user_agent="Mozilla/5.0 (Linux; Android 14)")
    _admin(client)
    rows, _ = _rows(client)
    assert rows["l1"]["shared_device"] is False
    assert rows["l2"]["shared_device"] is False


# ── Faster than a booth conversation ────────────────────────────────────

def test_two_captures_inside_a_minute_are_flagged(client):
    a = _visitor("همکار الف")
    _capture("first", a["id"], "2026-08-24T09:00:00")
    _capture("rushed", a["id"], "2026-08-24T09:00:30")
    _capture("normal", a["id"], "2026-08-24T09:30:00")
    _admin(client)
    rows, _ = _rows(client)
    # The first capture of the day has nothing to be measured against.
    assert rows["first"]["seconds_since_previous"] is None
    assert rows["first"]["too_fast"] is False
    assert rows["rushed"]["seconds_since_previous"] == 30
    assert rows["rushed"]["too_fast"] is True
    assert rows["normal"]["too_fast"] is False


def test_the_gap_is_measured_against_the_same_visitor_only(client):
    """Two visitors working side by side are not each other's evidence."""
    a, b = _visitor("همکار الف"), _visitor("همکار ب")
    _capture("l1", a["id"], "2026-08-24T09:00:00")
    _capture("l2", b["id"], "2026-08-24T09:00:10")
    _admin(client)
    rows, _ = _rows(client)
    assert rows["l2"]["seconds_since_previous"] is None
    assert rows["l2"]["too_fast"] is False


# ── The pair of numbers settlement is decided on ────────────────────────

def test_the_visitor_roster_carries_verified_and_completed(client):
    a = _visitor("همکار الف")
    _capture("l1", a["id"], "2026-08-24T09:00:00", status="verified")
    _capture("l2", a["id"], "2026-08-24T09:10:00", status="completed")
    _capture("l3", a["id"], "2026-08-24T09:20:00", status="unverified")
    _admin(client)
    row = client.get("/admin/api/leads/visitors").json()["visitors"][0]
    assert row["total"] == 3
    # `verified` is what the reward pays for: reached verification and stayed
    # there or went further.
    assert row["verified"] == 2
    assert row["completed"] == 1
