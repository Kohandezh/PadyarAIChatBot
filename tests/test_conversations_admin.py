"""The admin screens for visitors, transcripts and the bot's wrong answers.

THE SCENARIO. The exhibition closes for the day. The owner opens the panel and
asks three things: who came and how do I reach them, what exactly did this
person and the bot say to each other, and where did the bot answer badly so I
can fix the content that should have answered. This file holds those three to
their promises against the real endpoints, not against a mock.

The most important test in the file is the first one. Every route here serves
names, raw phone numbers, IP addresses and the exact words people typed. One
route that forgets `Depends(verify_admin)` publishes the lot.

The store (app/services/conversations.py) is seeded directly, because these
tests are about the panel. The chat router's writes are covered by
tests/test_conversations_store.py.
"""
import csv
import datetime
import io
import json
import secrets

import pytest
from fastapi.testclient import TestClient


API_ROUTES = [
    "/admin/api/conversations",
    "/admin/api/conversations/weak",
    "/admin/api/conversations/export",
    "/admin/api/conversations/some-id",
    "/admin/api/visitors",
    "/admin/api/visitors/export",
]

PAGE_ROUTES = [
    "/secure-panel-inotex/visitors",
    "/secure-panel-inotex/conversations",
]


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "conversations-admin.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    yield


@pytest.fixture
def anon(app_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(app_db):
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('panel','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "panel",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


# ── Seeding helpers ──────────────────────────────────────────────────────

def _store():
    from app.services import conversations
    return conversations


def _stamp(table: str, column: str, value: str, where_column: str, key: str):
    """Move a row's timestamp. Written as 'YYYY-MM-DD HH:MM:SS', the format
    SQLite's own datetime('now') produces, so string comparison sorts the way
    the filters assume."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {where_column} = ?",
                     (value, key))
        conn.commit()
    finally:
        conn.close()


def _conversation(cid: str, day: str, *, turns=(), visitor_id: str = ""):
    """One session with its turns, pinned to a day so ordering is decidable.

    Without pinning, every row is written in the same second and
    `last_message_at` ties — the list would come back in whatever order the
    planner felt like and a "newest first" assertion would prove nothing.
    """
    store = _store()
    store.get_or_create_conversation(cid, lang="fa", ip="10.0.0.1", user_agent="kiosk")
    for question, answer, source, confidence in turns:
        store.append_visitor_message(cid, question)
        store.append_assistant_message(cid, answer, source=source,
                                       confidence=confidence, entry_id="vid_1")
    if visitor_id:
        store.attach_visitor(cid, visitor_id)
    _stamp("conversations", "started_at", f"{day} 09:00:00", "id", cid)
    _stamp("conversations", "last_message_at", f"{day} 09:30:00", "id", cid)
    _stamp("messages", "created_at", f"{day} 09:30:00", "conversation_id", cid)
    return cid


def _visitor(first, last, phone, job, interests):
    return _store().upsert_visitor(first_name=first, last_name=last, phone=phone,
                                   job=job, position="مدیر", interests=interests)


def _seed(client):
    """Three sessions: an old anonymous one, a registered one, and one where
    the bot failed."""
    ali = _visitor("علی", "رضایی", "09121112233", "دانشجو", "هوش مصنوعی، رباتیک")
    sara = _visitor("سارا", "کریمی", "09354445566", "پژوهشگر", "اینترنت اشیا")

    _conversation("c-old", "2026-08-20", turns=[
        ("ساعت کاری نمایشگاه چند است؟", "از ۹ تا ۱۸", "local", 0.91)])
    _conversation("c-mid", "2026-08-22", visitor_id=ali, turns=[
        ("سالن شرکت پدیدار کجاست؟", "سالن ۳۸", "local_company_field", 0.88)])
    _conversation("c-bad", "2026-08-25", visitor_id=sara, turns=[
        ("قیمت بلیت پارکینگ چند است؟",
         "متوجه سوال نشدم، لطفاً جور دیگری بپرسید.", "system", 0.05)])
    return {"ali": ali, "sara": sara}


def _ids(payload):
    return [r["id"] for r in payload["rows"]]


# ── The gate ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", API_ROUTES)
def test_every_api_route_refuses_an_anonymous_caller(anon, route):
    """The whole feature in one assertion. These responses carry names, phone
    numbers and everything visitors typed; a route that forgets the dependency
    publishes them to anyone who types the URL."""
    assert anon.get(route).status_code in (401, 403), route


@pytest.mark.parametrize("route", PAGE_ROUTES)
def test_pages_send_an_anonymous_visitor_to_the_login(anon, route):
    res = anon.get(route, follow_redirects=False)
    assert res.status_code == 303, route
    assert "/login" in res.headers.get("location", "")


@pytest.mark.parametrize("route", PAGE_ROUTES)
def test_pages_render_for_an_administrator(client, route):
    assert client.get(route, follow_redirects=False).status_code == 200, route


def test_the_sidebar_links_to_both_screens_and_to_the_wrong_answers(client):
    """A page nobody can reach from the menu is a page nobody uses — the
    failure tests/test_admin_navigation.py exists to prevent. The wrong-answer
    queue gets its own link because it is the one that improves the bot."""
    sidebar = client.get("/secure-panel-inotex").text
    assert 'href="/secure-panel-inotex/visitors"' in sidebar
    assert 'href="/secure-panel-inotex/conversations"' in sidebar
    assert 'href="/secure-panel-inotex/conversations?view=weak"' in sidebar


# ── The conversation list ────────────────────────────────────────────────

def test_list_is_newest_first_and_paginates(client):
    _seed(client)

    first = client.get("/admin/api/conversations?limit=2").json()
    assert _ids(first) == ["c-bad", "c-mid"], "the list is not newest-activity first"
    assert first["has_more"] is True

    second = client.get("/admin/api/conversations?limit=2&offset=2").json()
    assert _ids(second) == ["c-old"]
    assert second["has_more"] is False, "a last page that claims more pages"


def test_the_page_never_returns_more_than_it_was_asked_for(client):
    """has_more comes from fetching one extra row. That extra row must not
    leak into the response, or every page would show 51 of a 50-row page."""
    _seed(client)
    payload = client.get("/admin/api/conversations?limit=1").json()
    assert len(payload["rows"]) == 1


def test_date_range_covers_the_whole_last_day(client):
    """«تا تاریخ» is a day, not a midnight. Without the end-of-day bound the
    final day of any range comes back empty and the screen looks broken."""
    _seed(client)
    payload = client.get("/admin/api/conversations?since=2026-08-22&until=2026-08-22").json()
    assert _ids(payload) == ["c-mid"]


def test_registered_and_anonymous_are_separable(client):
    _seed(client)
    registered = client.get("/admin/api/conversations?registered=yes").json()
    assert _ids(registered) == ["c-bad", "c-mid"]
    assert client.get("/admin/api/conversations?registered=no").json()["rows"][0]["id"] == "c-old"


def test_filter_by_which_tier_answered(client):
    _seed(client)
    payload = client.get("/admin/api/conversations?source=local_company_field").json()
    assert _ids(payload) == ["c-mid"]


def test_filter_by_confidence_band(client):
    """The screen's «پاسخ ضعیف» dropdown becomes max_confidence. It must find
    the session where the bot failed and nothing else."""
    _seed(client)
    weak = client.get("/admin/api/conversations?max_confidence=0.45").json()
    assert _ids(weak) == ["c-bad"]
    good = client.get("/admin/api/conversations?min_confidence=0.7").json()
    assert set(_ids(good)) == {"c-mid", "c-old"}


def test_free_text_search_reads_the_message_bodies(client):
    _seed(client)
    payload = client.get("/admin/api/conversations?q=پارکینگ").json()
    assert _ids(payload) == ["c-bad"]


def test_one_visitors_conversations(client):
    """Clicking a person on the visitors screen lands here."""
    seeded = _seed(client)
    payload = client.get(f"/admin/api/conversations?visitor_id={seeded['ali']}").json()
    assert _ids(payload) == ["c-mid"]


def test_a_session_carrying_a_bad_answer_is_flagged_in_the_list(client):
    """The column the owner scans for. It must count only the bad turns."""
    _seed(client)
    rows = {r["id"]: r["weak_count"] for r in
            client.get("/admin/api/conversations").json()["rows"]}
    assert rows["c-bad"] == 1
    assert rows["c-mid"] == 0 and rows["c-old"] == 0


# ── The transcript ───────────────────────────────────────────────────────

def test_transcript_returns_the_turns_in_order(client):
    _seed(client)
    _store().append_visitor_message("c-mid", "غرفه چند نفره است؟")
    _store().append_assistant_message("c-mid", "چهار نفر", source="local",
                                      confidence=0.8)

    payload = client.get("/admin/api/conversations/c-mid").json()
    roles = [m["role"] for m in payload["messages"]]
    texts = [m["text"] for m in payload["messages"]]
    assert roles == ["visitor", "assistant", "visitor", "assistant"], \
        "the bot appears to answer before it was asked"
    assert texts[2] == "غرفه چند نفره است؟"
    assert payload["conversation"]["first_name"] == "علی"


def test_transcript_of_an_unknown_session_is_a_404(client):
    assert client.get("/admin/api/conversations/nope").status_code == 404


# ── The wrong answers ────────────────────────────────────────────────────

def test_weak_view_returns_only_the_bad_turns(client):
    _seed(client)
    payload = client.get("/admin/api/conversations/weak").json()
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["conversation_id"] == "c-bad"
    assert row["confidence"] == pytest.approx(0.05)


def test_weak_row_carries_the_question_that_caused_it(client):
    """Without the question the operator cannot tell what the bot failed at,
    and the screen is a list of answers to nothing."""
    _seed(client)
    row = client.get("/admin/api/conversations/weak").json()["rows"][0]
    assert row["question"] == "قیمت بلیت پارکینگ چند است؟"
    assert row["no_answer"] is True, "a 'could not help' turn must be marked as one"


def test_weak_view_respects_the_threshold(client):
    """The «چقدر سخت‌گیر باشیم» dropdown. At the strictest setting a 0.05 turn
    still shows; a 0.88 one never does."""
    _seed(client)
    assert len(client.get("/admin/api/conversations/weak?threshold=0.19").json()["rows"]) == 1
    loose = client.get("/admin/api/conversations/weak?threshold=0.95").json()["rows"]
    assert len(loose) == 3, "a looser threshold must widen the queue, not replace it"


def test_a_healthy_install_shows_an_empty_queue(client):
    _conversation("c-fine", "2026-08-21",
                  turns=[("ساعت کاری؟", "۹ تا ۱۸", "local", 0.95)])
    assert client.get("/admin/api/conversations/weak").json()["rows"] == []


# ── Visitors ─────────────────────────────────────────────────────────────

def test_visitors_list_counts_their_conversations(client):
    seeded = _seed(client)
    rows = {r["id"]: r for r in client.get("/admin/api/visitors").json()["rows"]}
    assert rows[seeded["ali"]]["conversation_count"] == 1
    assert rows[seeded["ali"]]["first_name"] == "علی"


def test_visitors_filter_by_job(client):
    """An exact match on the job label, not a text search: a person whose
    interests happen to contain the word must not answer a job filter."""
    seeded = _seed(client)
    _store().upsert_visitor(first_name="نگار", phone="09121110000",
                            job="مهندس", interests="دانشجو")
    payload = client.get("/admin/api/visitors?job=دانشجو").json()
    assert _ids(payload) == [seeded["ali"]]


def test_visitors_filter_by_interest(client):
    seeded = _seed(client)
    payload = client.get("/admin/api/visitors?interest=رباتیک").json()
    assert _ids(payload) == [seeded["ali"]]


def test_visitors_free_text_finds_a_name(client):
    seeded = _seed(client)
    payload = client.get("/admin/api/visitors?q=کریمی").json()
    assert _ids(payload) == [seeded["sara"]]


def test_visitors_search_finds_a_whole_phone_number(client):
    """The store keeps phone out of its free-text search on purpose. One box
    still has to work for an operator holding a phone number."""
    seeded = _seed(client)
    payload = client.get("/admin/api/visitors?q=09354445566").json()
    assert _ids(payload)[0] == seeded["sara"]


def test_visitors_paginate(client):
    _seed(client)
    first = client.get("/admin/api/visitors?limit=1").json()
    assert len(first["rows"]) == 1 and first["has_more"] is True
    last = client.get("/admin/api/visitors?limit=1&offset=1").json()
    assert last["has_more"] is False
    assert _ids(first) != _ids(last)


# ── Exports ──────────────────────────────────────────────────────────────

def _csv_rows(response):
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    return rows[0], [r for r in rows[1:] if r]


def _audit(event: str) -> dict:
    from app.services import applog
    rows, _total = applog.query(tables=["audit_logs"], limit=200)
    for row in rows:
        if row.get("event_name") == event:
            return row
    return {}


def _metadata(row) -> dict:
    value = row.get("metadata")
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def test_conversations_export_is_csv_and_carries_every_row(client):
    _seed(client)
    res = client.get("/admin/api/conversations/export")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    header, rows = _csv_rows(res)
    assert len(rows) == 3
    assert header[0] == "شناسه گفتگو"


def test_conversations_export_writes_an_audit_row_with_the_real_count(client):
    """Personal data leaving the building is an event, and the count in the
    audit row must be the count in the file — a number nobody can trust is
    worse than no number."""
    _seed(client)
    res = client.get("/admin/api/conversations/export?registered=yes")
    _header, rows = _csv_rows(res)
    row = _audit("admin.conversations.exported")
    assert row, "the export left no audit trail"
    assert row.get("actor") == "panel"
    assert _metadata(row).get("rows") == len(rows) == 2


def test_visitors_export_writes_an_audit_row_with_the_real_count(client):
    _seed(client)
    res = client.get("/admin/api/visitors/export")
    _header, rows = _csv_rows(res)
    row = _audit("admin.visitors.exported")
    assert row, "the export left no audit trail"
    assert _metadata(row).get("rows") == len(rows) == 2


def test_the_export_obeys_the_filters_the_screen_applied(client):
    """An export that quietly ignores a filter hands the operator a file that
    is not what they were looking at."""
    _seed(client)
    _header, rows = _csv_rows(client.get("/admin/api/conversations/export?q=پارکینگ"))
    assert len(rows) == 1


def test_export_defuses_a_spreadsheet_formula(client):
    """A visitor chooses their own name. '=cmd' in a cell executes in Excel."""
    _store().upsert_visitor(first_name="=HYPERLINK(\"http://evil\")",
                            phone="09120000001", job="")
    _header, rows = _csv_rows(client.get("/admin/api/visitors/export"))
    assert rows[0][0].startswith("'="), "an exported cell can still run in Excel"
