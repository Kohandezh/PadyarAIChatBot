"""The visitor's credential: the session behind the cookie, and the code
behind the link (SPEC SEC-017 to SEC-020, PER-002).

Three defects are held down here.

F8: the cookie used to carry `lead_visitors.id` with a 12 hour `max_age`, so
the 12 hours were enforced by the browser and by nothing else, and the value
was a primary key that the admin panel prints and applog writes down.

F9: `lead_visitors.code` used to be stored in the clear, indexed, and present
in every backup `/admin/api/backups/download` can hand out.

PER-002: the lead routes shared `/chat`'s single threshold, so they could not
be given a ceiling that fits them without moving the chat's.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_db_connection
from app.services import leads as leads_service


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    import app.config as config
    path = tmp_path / "session.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    leads_service.ensure_tables()
    return path


@pytest.fixture
def client(db_path):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_buckets():
    from app.auth.security import _chat_rate_limits
    _chat_rate_limits.clear()
    yield
    _chat_rate_limits.clear()


def _open_link(client, code):
    """Walk in through the personal link and hand back the cookie value."""
    assert client.get(f"/v/{code}", follow_redirects=False).status_code == 303
    return client.cookies.get(leads_service.VISITOR_COOKIE)


def _rows(sql, params=()):
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _write(sql, params=()):
    conn = get_db_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _iso(**delta):
    return (datetime.datetime.utcnow() + datetime.timedelta(**delta)).isoformat()


# ── F8: the session ──────────────────────────────────────────────────────

def test_the_cookie_is_not_the_visitor_id(client):
    visitor = leads_service.create_visitor("همکار")
    token = _open_link(client, visitor["code"])
    assert token
    assert token != visitor["id"]
    # The id is what /admin/api/leads returns and what applog writes down. If
    # it opened a session, every log line would be a login.
    assert visitor["id"] not in token
    client.cookies.set(leads_service.VISITOR_COOKIE, visitor["id"])
    assert client.get("/api/leads/mine").status_code == 401


def test_the_expiry_is_checked_on_the_server(client):
    """A client that keeps the cookie past the window gets nothing.

    The browser's `max_age` is a hint. This is the control.
    """
    visitor = leads_service.create_visitor("همکار")
    token = _open_link(client, visitor["code"])
    assert client.get("/api/leads/mine").status_code == 200

    _write("UPDATE lead_visitor_sessions SET expiry = ? WHERE token = ?",
           (_iso(hours=-1), token))
    assert client.get("/api/leads/mine").status_code == 401
    # And the dead row is gone rather than left to be joined on forever.
    assert _rows("SELECT token FROM lead_visitor_sessions WHERE token = ?", (token,)) == []


def test_an_invented_token_opens_nothing(client):
    leads_service.create_visitor("همکار")
    client.cookies.set(leads_service.VISITOR_COOKIE, "a" * 40)
    assert client.get("/api/leads/mine").status_code == 401
    assert client.get("/v").status_code == 403


def test_working_the_day_slides_the_window(client):
    """Twelve hours of INACTIVITY ends the session, not twelve hours of work.

    A visitor who started at seven in the morning must not be signed out while
    they are standing at a booth.
    """
    visitor = leads_service.create_visitor("همکار")
    token = _open_link(client, visitor["code"])
    _write("UPDATE lead_visitor_sessions SET expiry = ? WHERE token = ?",
           (_iso(minutes=30), token))

    assert client.get("/api/leads/mine").status_code == 200
    expiry = _rows("SELECT expiry FROM lead_visitor_sessions WHERE token = ?",
                   (token,))[0]["expiry"]
    assert datetime.datetime.fromisoformat(expiry) > datetime.datetime.utcnow() \
        + datetime.timedelta(hours=11)


def test_the_slide_never_passes_the_code_expiry(client):
    """The ceiling that stops sliding from meaning forever."""
    visitor = leads_service.create_visitor("همکار")
    token = _open_link(client, visitor["code"])
    ceiling = _iso(hours=2)
    _write("UPDATE lead_visitors SET expires_at = ? WHERE id = ?",
           (ceiling, visitor["id"]))

    assert client.get("/api/leads/mine").status_code == 200
    expiry = _rows("SELECT expiry FROM lead_visitor_sessions WHERE token = ?",
                   (token,))[0]["expiry"]
    assert expiry == ceiling


def test_a_session_dies_with_its_code(client):
    visitor = leads_service.create_visitor("همکار")
    _open_link(client, visitor["code"])
    _write("UPDATE lead_visitors SET expires_at = ? WHERE id = ?",
           (_iso(hours=-1), visitor["id"]))
    assert client.get("/api/leads/mine").status_code == 401


def test_deactivating_a_visitor_is_instant(client):
    """Revocation was already instant and has to stay instant, but now it also
    takes the open sessions with it instead of waiting for expiry."""
    visitor = leads_service.create_visitor("همکار")
    _open_link(client, visitor["code"])
    assert client.get("/api/leads/mine").status_code == 200

    leads_service.set_visitor_active(visitor["id"], False)
    assert _rows("SELECT token FROM lead_visitor_sessions WHERE visitor_id = ?",
                 (visitor["id"],)) == []
    assert client.get("/api/leads/mine").status_code == 401


def test_a_surviving_session_of_a_deactivated_visitor_still_fails(client):
    """`active` is re-read on every request, so a row that slipped through a
    race is refused on its next use."""
    visitor = leads_service.create_visitor("همکار")
    token = _open_link(client, visitor["code"])
    _write("UPDATE lead_visitors SET active = 0 WHERE id = ?", (visitor["id"],))
    _write("INSERT INTO lead_visitor_sessions (token, visitor_id, expiry, created_at)"
           " VALUES (?, ?, ?, ?)",
           (token + "x", visitor["id"], _iso(hours=6), _iso()))
    client.cookies.set(leads_service.VISITOR_COOKIE, token + "x")
    assert client.get("/api/leads/mine").status_code == 401


def test_rotating_the_link_signs_the_old_phone_out(client):
    visitor = leads_service.create_visitor("همکار")
    _open_link(client, visitor["code"])
    assert client.get("/api/leads/mine").status_code == 200

    leads_service.rotate_visitor_code(visitor["id"])
    assert client.get("/api/leads/mine").status_code == 401


# ── F9: the code ─────────────────────────────────────────────────────────

def test_the_code_is_not_in_the_database(client, db_path):
    """Not in the column, not in the file. The backup is downloadable."""
    visitor = leads_service.create_visitor("همکار")
    columns = {r["name"] for r in _rows("PRAGMA table_info(lead_visitors)")}
    assert "code" not in columns
    assert "code_hash" in columns and "expires_at" in columns
    assert visitor["code"].encode() not in db_path.read_bytes()


def test_the_raw_code_is_returned_twice_and_never_again(client):
    """Once from creation, once from the rotate action. There is no third."""
    visitor = leads_service.create_visitor("همکار")
    assert visitor["code"]
    rotated = leads_service.rotate_visitor_code(visitor["id"])
    assert rotated["code"] and rotated["code"] != visitor["code"]

    for row in leads_service.list_visitors():
        assert "code" not in row and "code_hash" not in row


def test_the_old_code_stops_working_the_moment_it_is_rotated(client):
    visitor = leads_service.create_visitor("همکار")
    rotated = leads_service.rotate_visitor_code(visitor["id"])
    assert client.get(f"/v/{visitor['code']}", follow_redirects=False).status_code == 403
    assert client.get(f"/v/{rotated['code']}", follow_redirects=False).status_code == 303


def test_an_expired_code_opens_nothing(client):
    visitor = leads_service.create_visitor("همکار")
    _write("UPDATE lead_visitors SET expires_at = ? WHERE id = ?",
           (_iso(hours=-1), visitor["id"]))
    assert client.get(f"/v/{visitor['code']}", follow_redirects=False).status_code == 403
    assert leads_service.list_visitors()[0]["needs_link"] is True


def test_a_visitor_created_before_the_migration_keeps_the_row_and_loses_the_link(db_path):
    """What migrations/0007_visitor_sessions.sql says it destroys.

    The name and the counts survive. The link does not, because a keyed HMAC
    cannot be derived from a code the database is about to forget. The roster
    says so with `needs_link`, which is the operator's whole instruction.
    """
    _write("DROP TABLE lead_visitors")
    _write("CREATE TABLE lead_visitors (id TEXT PRIMARY KEY, name TEXT NOT NULL"
           " DEFAULT '', code TEXT NOT NULL, active INTEGER DEFAULT 1,"
           " created_at TEXT NOT NULL)")
    _write("INSERT INTO lead_visitors (id, name, code, active, created_at)"
           " VALUES ('old', 'همکار قدیمی', 'plain-code', 1, ?)", (_iso(),))

    leads_service.ensure_tables()

    columns = {r["name"] for r in _rows("PRAGMA table_info(lead_visitors)")}
    assert "code" not in columns
    roster = leads_service.list_visitors()
    assert [v["name"] for v in roster] == ["همکار قدیمی"]
    assert roster[0]["needs_link"] is True
    assert leads_service.start_session("plain-code") is None


# ── PER-002: the thresholds ──────────────────────────────────────────────

def test_the_shared_threshold_is_still_the_default():
    """Nothing that did not ask for its own ceiling changed."""
    from app.config import CHAT_RATE_LIMIT
    from app.auth.security import check_rate_limit
    from tests.test_client_ip import make_request

    for _ in range(CHAT_RATE_LIMIT):
        check_rate_limit(make_request(host="10.9.0.1"))
    with pytest.raises(Exception) as exc:
        check_rate_limit(make_request(host="10.9.0.1"))
    assert exc.value.status_code == 429


def test_a_route_can_carry_its_own_ceiling():
    from app.auth.security import check_rate_limit
    from tests.test_client_ip import make_request

    for _ in range(3):
        check_rate_limit(make_request(host="10.9.0.2"), limit=3)
    with pytest.raises(Exception) as exc:
        check_rate_limit(make_request(host="10.9.0.2"), limit=3)
    assert exc.value.status_code == 429
    # A different bucket at the same address is untouched by that ceiling.
    check_rate_limit(make_request(host="10.9.0.2"), key="visitor:x")


def test_the_visitor_routes_are_keyed_and_capped_on_the_visitor(client, monkeypatch):
    """The hall is behind one NAT, so the bucket is the person, and the number
    is the lead module's own rather than the chat's."""
    import app.routers.leads as leads_router
    seen = []
    monkeypatch.setattr(leads_router, "check_rate_limit",
                        lambda request, key="", limit=None: seen.append((key, limit)))
    visitor = leads_service.create_visitor("همکار")
    _open_link(client, visitor["code"])
    client.post("/api/leads/verify", json={"lead_id": "nope", "code": "1234"})
    assert seen == [(f"visitor:{visitor['id']}", leads_service.RATE_LIMIT_PER_VISITOR)]


def test_the_contact_route_carries_the_generous_ip_ceiling(client, monkeypatch):
    """The contact has no cookie to key on, and half the phones in the hall
    leave from one address."""
    import app.routers.leads as leads_router
    seen = []
    monkeypatch.setattr(leads_router, "check_rate_limit",
                        lambda request, key="", limit=None: seen.append((key, limit)))
    client.get("/edit/nothing-here")
    assert seen == [("", leads_service.RATE_LIMIT_PER_IP)]
    assert leads_service.RATE_LIMIT_PER_IP > leads_service.RATE_LIMIT_PER_VISITOR


def test_the_visitor_ceiling_actually_bites(client):
    """Not a wiring test: the request that passes the ceiling gets a 429."""
    visitor = leads_service.create_visitor("همکار")
    _open_link(client, visitor["code"])
    for _ in range(leads_service.RATE_LIMIT_PER_VISITOR):
        assert client.post("/api/leads/verify",
                           json={"lead_id": "nope", "code": "1234"}).status_code == 404
    assert client.post("/api/leads/verify",
                       json={"lead_id": "nope", "code": "1234"}).status_code == 429
