"""Conversation memory: three columns on the table that already stores turns.

WHAT IS BROKEN TODAY: `app/routers/chat.py` computes a `conversation_id` and
sets the `padyar_conv` cookie on every answer — and NOTHING persists it. The
chatbot therefore has no memory at all: "and the second one?" is a brand new
question, and no stored row can be traced back to the record that produced it.

THE FEATURE under test, all on `app.chat_logs`:

    conversation_id  TEXT NOT NULL DEFAULT ''   the padyar_conv cookie value
    entry_id         TEXT NOT NULL DEFAULT ''   WHICH record produced this answer
    offer_state      TEXT NOT NULL DEFAULT ''   JSON: what was OFFERED this turn

plus two readers that must NEVER raise — `recent_turns()` and
`last_offer_state()` — plus `purge_chat_logs()`, because chat_logs is the
UNREDACTED store and this design starts reading it back and shipping up to
five turns off-box.

A logging fault has never been allowed to fail a visitor's answer, and none of
this changes that: every function here degrades to an empty default.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


DATASET = [
    ("faq-guide", "اطلاعات نمایشگاه",
     "درباره غرفه ها و ساعت کاری توضیح کامل در ورودی نمایشگاه موجود است.", ""),
    ("co-alfa", "شرکت آلفا", "معرفی شرکت آلفا: فعال در هوش مصنوعی.", "ghorfe-01.mp4"),
    ("co-beta", "شرکت بتا", "شرکت بتا سامانه های هوش مصنوعی می سازد.", "ghorfe-02.mp4"),
    ("co-gama", "شرکت گاما", "شرکت گاما در زمینه هوش مصنوعی کار می کند.", "ghorfe-03.mp4"),
]

LIST_QUESTION = "شرکت‌های هوش مصنوعی را معرفی کن"


def _seed(rows=DATASET):
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for i, title, text, video in rows:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, ?)", (i, title, text, video))
    conn.commit()
    conn.close()

    from app.services import leads
    leads.ensure_tables()
    conn = dbc.get_db_connection()
    for i, _t, _x, _v in rows:
        if i.startswith("co-"):
            conn.execute(
                "INSERT INTO company_profiles (dataset_id, activity_field,"
                " created_at, updated_at)"
                " VALUES (?, 'هوش مصنوعی', '2026-08-28', '2026-08-28')", (i,))
    conn.commit()
    conn.close()

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        set_setting("search_backend", "tfidf")

        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _ask(client, message, lang="fa"):
    return client.post("/chat", json={"message": message, "lang": lang})


def _mock_ai(monkeypatch, classified=None, generated="پاسخ تولیدشدهٔ AI"):
    import app.routers.chat as chat

    async def fake_classify(query):
        return classified, 1, 0.0

    async def fake_generate(query, lang="fa"):
        return generated, 2, 0.0

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)


def _db():
    from app.db.connection import get_db_connection
    return get_db_connection()


def _rows(sql, args=()):
    conn = _db()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _log_rows(event=None):
    from app.services import applog
    conn = applog.get_logs_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM app_logs")]
    finally:
        conn.close()
    return [r for r in rows if event is None or r["event_name"] == event]


# ── The schema ───────────────────────────────────────────────────────────

def test_chat_logs_carries_the_three_conversation_columns(client):
    """Three columns on the table that already stores every answered turn. A
    second `conversation_turns` table would double-write the same text and
    need its own retention, and CLAUDE.md requires a new table to justify
    itself."""
    _seed()
    cols = {r["name"] for r in _rows("PRAGMA table_info(chat_logs)")}
    assert {"conversation_id", "entry_id", "offer_state"} <= cols, sorted(cols)


def test_an_answer_row_records_which_record_produced_it(client, monkeypatch):
    """`entry_id` is not decoration. Today a stored row cannot be traced back
    to the record it came from, so "which record did the bot serve when it got
    this wrong?" is unanswerable from the table."""
    _seed()
    _mock_ai(monkeypatch)
    r = _ask(client, "شرکت آلفا چیست؟")
    assert r.status_code == 200, r.text

    rows = _rows("SELECT * FROM chat_logs ORDER BY id DESC")
    assert rows, "the turn must be logged"
    assert rows[0]["entry_id"] == "co-alfa", rows[0]
    assert rows[0]["conversation_id"], rows[0]


# ── log_chat keeps its old contract ──────────────────────────────────────

def test_log_chat_still_accepts_the_seven_positional_arguments(client):
    """tests/test_ai_legacy_import.py wraps log_chat with a fixed
    seven-positional-argument spy. The new parameters are KEYWORD-ONLY so that
    spy keeps working — this is a compatibility rule, not a style choice."""
    _seed()
    from app.db.queries import log_chat

    log_chat("q", "r", "text", "local", 0.5, 3, 0.01)
    rows = _rows("SELECT * FROM chat_logs")
    assert len(rows) == 1, rows
    assert rows[0]["source"] == "local" and rows[0]["tokens"] == 3

    import inspect
    params = inspect.signature(log_chat).parameters
    for name in ("conversation_id", "entry_id", "offer_state"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_log_chat_against_a_narrow_table_swallows_the_failure(client):
    """tests/test_reset_script.py builds its own two-column chat_logs, and an
    install whose migration has not run yet must still serve visitors. A
    logging fault has never been allowed to fail an answer."""
    _seed()
    conn = _db()
    conn.execute("DROP TABLE chat_logs")
    conn.execute("CREATE TABLE chat_logs (id INTEGER PRIMARY KEY, query TEXT)")
    conn.commit()
    conn.close()

    from app.db.queries import log_chat
    log_chat("q", "r", "text", "local", 0.5, conversation_id="c1",
             entry_id="co-alfa", offer_state="{}")   # must not raise


def test_log_chat_falls_back_to_the_narrow_insert_on_an_unmigrated_table(client):
    """The wide INSERT is tried first and the original seven-column INSERT is
    the fallback, so an install that has not applied migration 0009 keeps
    logging exactly as it does today."""
    _seed()
    conn = _db()
    conn.execute("DROP TABLE chat_logs")
    conn.execute(
        "CREATE TABLE chat_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " query TEXT, response TEXT, response_type TEXT, source TEXT,"
        " confidence REAL, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0.0,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

    from app.db.queries import log_chat
    log_chat("q", "r", "text", "local", 0.5, conversation_id="c1")
    rows = _rows("SELECT * FROM chat_logs")
    assert len(rows) == 1 and rows[0]["query"] == "q", rows


# ── recent_turns ─────────────────────────────────────────────────────────

def _insert(conn, conversation_id, query, response, source="local",
            entry_id="", offer_state="", created_at=None):
    conn.execute(
        "INSERT INTO chat_logs (query, response, response_type, source,"
        " confidence, tokens, cost, conversation_id, entry_id, offer_state,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,"
        + ("?" if created_at else "CURRENT_TIMESTAMP") + ")",
        (query, response, "text", source, 0.9, 0, 0.0,
         conversation_id, entry_id, offer_state)
        + ((created_at,) if created_at else ()))


def test_recent_turns_returns_only_this_conversations_turns(client):
    """The key is the padyar_conv cookie the app already sets. Two visitors at
    the same booth on two devices are two cookies and must never see each
    other's history."""
    _seed()
    conn = _db()
    _insert(conn, "conv-a", "پرسش الف", "پاسخ الف")
    _insert(conn, "conv-b", "پرسش ب", "پاسخ ب")
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns
    turns = recent_turns("conv-a", limit=5)
    assert len(turns) == 1, turns
    assert turns[0]["query"] == "پرسش الف"


def test_recent_turns_skips_the_system_sentinel_rows(client):
    """`no_confident_match` and `ai_unavailable_no_strong_match` are stored in
    the RESPONSE column with source 'system'. Replaying them to the model as
    prior assistant answers teaches it to emit them."""
    _seed()
    conn = _db()
    _insert(conn, "conv-a", "پرسش یک", "پاسخ یک")
    _insert(conn, "conv-a", "پرسش دو", "no_confident_match", source="system")
    _insert(conn, "conv-a", "پرسش سه", "ai_unavailable_no_strong_match",
            source="system")
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns
    responses = [t["response"] for t in recent_turns("conv-a", limit=5)]
    assert "no_confident_match" not in responses, responses
    assert "ai_unavailable_no_strong_match" not in responses, responses
    assert "پاسخ یک" in responses


def test_recent_turns_orders_by_id_not_by_timestamp(client):
    """SQLite's CURRENT_TIMESTAMP has one-second resolution, so two turns in
    the same second tie and the order becomes whatever the planner felt like.
    `id` is monotonic on both backends, which is the whole reason it is the
    sort key."""
    _seed()
    conn = _db()
    for n in range(1, 6):
        _insert(conn, "conv-a", f"پرسش {n}", f"پاسخ {n}")
    # One UPDATE gives every row the SAME timestamp, which is the tie this test
    # is about. It has to be read off the database rather than written as a
    # literal date: recent_turns() only looks back HISTORY_WINDOW_MINUTES, so a
    # hardcoded timestamp puts the rows outside the window the moment that many
    # minutes pass and the test starts failing on the clock.
    conn.execute("UPDATE chat_logs SET created_at = datetime('now')")
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns
    turns = recent_turns("conv-a", limit=3)
    assert len(turns) == 3, turns
    # Newest first: the three highest ids, in descending id order.
    assert [t["query"] for t in turns] == ["پرسش 5", "پرسش 4", "پرسش 3"], turns


def test_recent_turns_honours_its_limit(client):
    _seed()
    conn = _db()
    for n in range(1, 12):
        _insert(conn, "conv-a", f"پرسش {n}", f"پاسخ {n}")
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns
    assert len(recent_turns("conv-a", limit=5)) == 5


def test_recent_turns_ignores_turns_outside_the_history_window(client):
    """History is cut off at HISTORY_WINDOW_MINUTES — a conversation's length,
    not the sliding padyar_conv cookie's. See tests/test_kiosk_privacy.py for
    why the cookie is the wrong bound at a shared kiosk."""
    _seed()
    conn = _db()
    _insert(conn, "conv-a", "پرسش کهنه", "پاسخ کهنه",
            created_at="2020-01-01 00:00:00")
    _insert(conn, "conv-a", "پرسش تازه", "پاسخ تازه")
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns
    queries = [t["query"] for t in recent_turns("conv-a", limit=5)]
    assert queries == ["پرسش تازه"], queries


# ── last_offer_state ─────────────────────────────────────────────────────

def test_last_offer_state_returns_the_newest_stored_offer(client):
    _seed()
    conn = _db()
    _insert(conn, "conv-a", "q1", "r1",
            offer_state=json.dumps({"ids": ["co-alfa"], "shown": 1}))
    _insert(conn, "conv-a", "q2", "r2")            # no offer on this turn
    _insert(conn, "conv-a", "q3", "r3",
            offer_state=json.dumps({"ids": ["co-beta", "co-gama"], "shown": 2}))
    conn.commit()
    conn.close()

    from app.db.queries import last_offer_state
    raw = last_offer_state("conv-a", within_minutes=15)
    assert json.loads(raw)["ids"] == ["co-beta", "co-gama"], raw


def test_last_offer_state_ignores_an_offer_outside_the_window(client):
    """A booth kiosk is ONE browser and ONE cookie shared by many people. A
    bare "3" typed twenty minutes after somebody else's list must not resolve
    against that stranger's list."""
    _seed()
    conn = _db()
    _insert(conn, "conv-a", "q1", "r1",
            offer_state=json.dumps({"ids": ["co-alfa"], "shown": 1}))
    conn.execute("UPDATE chat_logs SET created_at = datetime('now','-20 minutes')")
    conn.commit()
    conn.close()

    from app.db.queries import last_offer_state
    assert last_offer_state("conv-a", within_minutes=15) == ""


def test_last_offer_state_is_empty_for_a_conversation_that_never_had_one(client):
    _seed()
    from app.db.queries import last_offer_state
    assert last_offer_state("conv-never", within_minutes=15) == ""


# ── An unmigrated install degrades, it does not break ────────────────────

def test_both_readers_return_their_empty_default_without_the_new_columns(client):
    """Migration 0009 may not have run. Neither reader may raise: with no
    history the selection tier still works (it just sees no prior turns), the
    pick tier never fires, and the whole feature degrades to today's
    chatbot."""
    _seed()
    conn = _db()
    conn.execute("DROP TABLE chat_logs")
    conn.execute(
        "CREATE TABLE chat_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " query TEXT, response TEXT, response_type TEXT, source TEXT,"
        " confidence REAL, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0.0,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO chat_logs (query, response, source)"
                 " VALUES ('q','r','local')")
    conn.commit()
    conn.close()

    from app.db.queries import recent_turns, last_offer_state
    assert recent_turns("conv-a", limit=5) == []
    assert last_offer_state("conv-a", within_minutes=15) == ""


def test_a_chat_turn_still_answers_when_the_memory_columns_are_missing(client, monkeypatch):
    """REGRESSION over existing behaviour, and it passes today on purpose.

    End to end on an unmigrated install: the visitor gets an answer, and
    nothing about the request fails. This is the guard that keeps a logging
    change from ever costing a visitor their answer."""
    _seed()
    conn = _db()
    conn.execute("DROP TABLE chat_logs")
    conn.execute(
        "CREATE TABLE chat_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " query TEXT, response TEXT, response_type TEXT, source TEXT,"
        " confidence REAL, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0.0,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

    _mock_ai(monkeypatch)
    r = _ask(client, "شرکت آلفا چیست؟")
    assert r.status_code == 200, r.text
    assert r.json()["text"], r.json()


# ── 32. The conversation id finally reaches the log row ──────────────────

def test_the_answer_log_row_carries_the_conversation_id(client, monkeypatch):
    """Today the ONLY applog call site that passes conversation_id is the
    "message received" row, so every `conversation.answer.served` row carries
    an empty string — the log explorer's conversation filter and the new
    index are both dead. A ContextVar beside request_id/correlation_id fixes
    every call site at once."""
    _seed()
    _mock_ai(monkeypatch)
    r = _ask(client, "شرکت آلفا چیست؟")
    assert r.status_code == 200, r.text

    served = _log_rows("conversation.answer.served")
    assert served, [x["event_name"] for x in _log_rows()]
    assert served[-1]["conversation_id"], served[-1]

    received = _log_rows("conversation.message.received")
    assert received[-1]["conversation_id"] == served[-1]["conversation_id"]


# ── 47. The shared-kiosk escape hatch ────────────────────────────────────

def test_starting_a_new_conversation_clears_the_cookie(client, monkeypatch):
    """The 15-minute offer window SHRINKS the shared-kiosk problem; this
    button CLOSES it. One tap, one plain label, no explanation needed."""
    _seed()
    _mock_ai(monkeypatch)
    first = _ask(client, "شرکت آلفا چیست؟")
    assert first.status_code == 200, first.text
    assert client.cookies.get("padyar_conv"), dict(client.cookies)

    r = client.post("/api/chat/new-conversation")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}, r.json()
    assert not client.cookies.get("padyar_conv"), dict(client.cookies)


def test_starting_a_new_conversation_needs_the_same_guards_as_a_chat_turn(client):
    """It is a state-changing visitor endpoint on the public surface, so it
    carries the origin and chat-token checks every other one does."""
    _seed()
    r = client.post("/api/chat/new-conversation",
                    headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403, r.text


def test_after_a_new_conversation_the_previous_offer_cannot_be_picked_from(client, monkeypatch):
    """The point of the button: the next person's "1" must not land on the
    previous person's list."""
    _seed()
    _mock_ai(monkeypatch)
    listed = _ask(client, LIST_QUESTION)
    assert listed.json()["source"] == "local_company_search", listed.text
    assert listed.json()["options"], "the list turn must offer pickable options"

    reset = client.post("/api/chat/new-conversation")
    assert reset.status_code == 200, reset.text

    picked = _ask(client, "1")
    assert picked.status_code in (200, 503), picked.text
    if picked.status_code == 200:
        assert picked.json()["source"] != "local_pick", picked.json()


# ── 50. Retention on the unredacted store ────────────────────────────────

def test_purging_chat_logs_keeps_everything_when_retention_is_zero(client):
    """Default 0 = keep forever, so no existing install loses data by
    upgrading."""
    _seed()
    conn = _db()
    _insert(conn, "conv-a", "q", "r", created_at="2020-01-01 00:00:00")
    conn.commit()
    conn.close()

    from app.db.queries import purge_chat_logs, set_setting
    set_setting("chat_log_retention_days", "0")
    assert purge_chat_logs() == 0
    assert len(_rows("SELECT id FROM chat_logs")) == 1


def test_purging_chat_logs_deletes_rows_past_the_retention_window(client):
    """chat_logs is the UNREDACTED store — log_chat writes the raw visitor
    query with no content policy applied — and today NOTHING prunes it. This
    design reads it back and ships up to five turns off-box, so an operator
    needs a dial."""
    _seed()
    conn = _db()
    _insert(conn, "conv-a", "کهنه", "r", created_at="2020-01-01 00:00:00")
    _insert(conn, "conv-a", "تازه", "r")
    conn.commit()
    conn.close()

    from app.db.queries import purge_chat_logs, set_setting
    set_setting("chat_log_retention_days", "7")
    assert purge_chat_logs() == 1
    remaining = [r["query"] for r in _rows("SELECT query FROM chat_logs")]
    assert remaining == ["تازه"], remaining
