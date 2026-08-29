"""Every answered turn lands in the durable transcript.

WHAT WAS BROKEN. migrations/0010 built `visitors`, `conversations` and
`messages`, and nothing wrote a single row into them. The chat router kept
writing one flat `chat_logs` row per turn, the browser kept the readable
transcript in localStorage, and a cleared kiosk browser threw away the thing
the exhibition exists to collect.

WHAT THIS FILE PINS DOWN.

1. One turn = one conversation row and two message rows, on every tier that
   answers. The router has more than a dozen answering branches, so the tests
   below walk several REAL ones (the company list, the pick, the curated
   questions index, trusted local retrieval) rather than one and a promise.
2. The assistant row carries what the owner needs to fix the bot: which tier
   answered, how sure it was, WHICH record it came from, and the clip it
   played.
3. A storage fault costs the visitor nothing. Chat is the product; logging is
   not. Every write here is wrapped, and the visitor still gets their answer.
4. A person who registers halfway through keeps the messages they sent before
   they had a name.
5. `chat_log_retention_days` prunes this store too, in the one cycle that
   already prunes chat_logs.
"""
import pytest
from fastapi.testclient import TestClient


DATASET = [
    ("faq-hours", "ساعت کاری نمایشگاه",
     "نمایشگاه هر روز از ساعت ۹ صبح تا ۱۸ باز است.", ""),
    ("co-alfa", "شرکت آلفا", "معرفی شرکت آلفا: فعال در هوش مصنوعی.",
     "ghorfe-01.mp4"),
    ("co-beta", "شرکت بتا", "شرکت بتا سامانه های هوش مصنوعی می سازد.",
     "ghorfe-02.mp4"),
    ("co-gama", "شرکت گاما", "شرکت گاما در زمینه هوش مصنوعی کار می کند.",
     "ghorfe-03.mp4"),
]

LIST_QUESTION = "شرکت‌های هوش مصنوعی را معرفی کن"
CURATED_QUESTION = "ساعت کاری نمایشگاه چیست؟"


def _seed():
    import app.db.connection as dbc

    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM companies")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for entry_id, title, text, video in DATASET:
        if entry_id.startswith("co-"):
            # Companies are their own table now (migrations/0013_companies.sql).
            conn.execute(
                "INSERT INTO companies (id, title, text, video_url, activity_field)"
                " VALUES (?, ?, ?, ?, 'هوش مصنوعی')", (entry_id, title, text, video))
        else:
            conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                         " VALUES (?, ?, ?, ?)", (entry_id, title, text, video))
    conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                 " VALUES (?, ?, '')", (CURATED_QUESTION, "faq-hours"))
    conn.commit()
    conn.close()

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "transcript.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        # The AI tier is off for the whole file: every tier exercised here is
        # a local one, and a network call would make the test flaky about
        # something it is not testing.
        set_setting("openai_enabled", "false")
        _seed()
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token(),
                          "User-Agent": "KioskBrowser/1.0"})
        yield c
    security._chat_rate_limits.clear()


@pytest.fixture(autouse=True)
def _no_otp_throttle(monkeypatch):
    """Dozens of OTP calls from one address in seconds is this file's normal
    mode. monkeypatch, never a bare assignment: a permanent one leaks into
    tests/test_security_hardening.py, which asserts those buckets DO bite."""
    from app.routers import otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)


def _ask(client, message, lang="fa"):
    return client.post("/chat", json={"message": message, "lang": lang})


def _rows(sql, args=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _messages():
    return _rows("SELECT * FROM messages ORDER BY id ASC")


def _conversations():
    return _rows("SELECT * FROM conversations")


# ── One turn, two messages, on several real tiers ────────────────────────

def test_a_company_list_turn_is_written_to_the_transcript(client):
    r = _ask(client, LIST_QUESTION)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local_company_search"

    convs = _conversations()
    assert len(convs) == 1, convs
    assert convs[0]["message_count"] == 2

    rows = _messages()
    assert [m["role"] for m in rows] == ["visitor", "assistant"]
    assert rows[0]["text"] == LIST_QUESTION
    assert rows[1]["source"] == "local_company_search"
    assert rows[1]["confidence"] == pytest.approx(0.9)
    assert rows[1]["text"] == r.json()["text"]


def test_a_pick_turn_records_the_record_and_the_clip_it_played(client):
    """The pick tier is the one that plays a booth video, so its message row
    is where `entry_id` and `video_url` have to show up."""
    _ask(client, LIST_QUESTION)
    picked = _ask(client, "1")
    assert picked.status_code == 200, picked.text
    assert picked.json()["source"] == "local_pick"

    rows = _messages()
    assert len(rows) == 4, rows
    answer = rows[-1]
    assert answer["source"] == "local_pick"
    assert answer["entry_id"].startswith("co-"), answer
    assert answer["video_url"] == picked.json()["video_url"]
    assert answer["video_url"].endswith(".mp4"), answer


def test_the_curated_questions_tier_is_written_too(client):
    r = _ask(client, CURATED_QUESTION)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local_questions"

    answer = _messages()[-1]
    assert answer["source"] == "local_questions"
    assert answer["entry_id"] == "faq-hours"
    assert answer["confidence"] >= 0.9


def test_trusted_local_retrieval_is_written_too(client, monkeypatch):
    """The fourth tier, forced: `find_best_match` above the trust bar."""
    from app.routers import chat as chat_router
    entry = {"id": "faq-hours", "title": "t", "text": "پاسخ محلی",
             "video_url": ""}
    monkeypatch.setattr(chat_router, "find_best_match", lambda q: (entry, 0.95))
    monkeypatch.setattr(chat_router, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat_router, "resolve_named_entity", lambda q: (None, set()))
    monkeypatch.setattr(chat_router, "unknown_salient_tokens", lambda q: set())

    r = _ask(client, "ساعت بازدید نمایشگاه")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local"

    answer = _messages()[-1]
    assert answer["source"] == "local"
    assert answer["entry_id"] == "faq-hours"
    assert answer["confidence"] == pytest.approx(0.95)


def test_three_turns_share_one_conversation(client):
    _ask(client, LIST_QUESTION)
    _ask(client, "1")
    _ask(client, CURATED_QUESTION)

    convs = _conversations()
    assert len(convs) == 1, convs
    assert convs[0]["message_count"] == 6
    assert len(_messages()) == 6


def test_the_conversation_carries_the_language_address_and_browser(client):
    _ask(client, LIST_QUESTION, lang="en")
    conv = _conversations()[0]
    assert conv["lang"] == "en"
    assert conv["ip"]
    assert "KioskBrowser" in conv["user_agent"]


def test_the_answer_the_visitor_saw_is_the_answer_that_was_stored(client):
    """The transcript is evidence. A stored answer that differs from the one
    on the screen is worse than no transcript at all."""
    r = _ask(client, CURATED_QUESTION)
    assert _messages()[-1]["text"] == r.json()["text"]


# ── A storage fault never costs a visitor their answer ───────────────────

def test_a_broken_transcript_write_still_answers_the_visitor(client, monkeypatch):
    from app.routers import chat as chat_router

    def boom(*a, **k):
        raise RuntimeError("transcript store is down")

    monkeypatch.setattr(chat_router.conversations, "append_assistant_message", boom)
    monkeypatch.setattr(chat_router.conversations, "append_visitor_message", boom)

    r = _ask(client, CURATED_QUESTION)
    assert r.status_code == 200, r.text
    assert "۹ صبح" in r.json()["text"]
    # chat_logs, which is a separate store with its own swallow, still has it.
    assert _rows("SELECT id FROM chat_logs")


def test_a_dropped_conversations_table_still_answers_the_visitor(client):
    """The session row cannot be written at all. Not a mock: the real table is
    removed under the running app, which is the fault a broken migration or a
    revoked grant actually looks like."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("DROP TABLE conversations")
    conn.commit()
    conn.close()

    r = _ask(client, CURATED_QUESTION)
    assert r.status_code == 200, r.text
    assert "۹ صبح" in r.json()["text"]


def test_a_dropped_messages_table_still_answers_the_visitor(client):
    """Not a mock: the real table is removed under the running app."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("DROP TABLE messages")
    conn.commit()
    conn.close()

    r = _ask(client, CURATED_QUESTION)
    assert r.status_code == 200, r.text
    assert "۹ صبح" in r.json()["text"]


# ── An unanswered question is still recorded ─────────────────────────────

def test_a_question_nobody_answered_keeps_its_visitor_message(client, monkeypatch):
    """The AI is down and no local match is strong enough, so the visitor gets
    a 503 — and `messages` keeps the question with nothing beside it. That row
    is exactly what a flat per-turn table cannot hold."""
    from app.db.queries import set_setting
    from app.routers import chat as chat_router
    set_setting("openai_enabled", "true")

    async def dead(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(chat_router, "select_records", dead)
    monkeypatch.setattr(chat_router, "classify_intent", dead)
    monkeypatch.setattr(chat_router, "get_openai_response", dead)
    monkeypatch.setattr(chat_router, "find_best_match", lambda q: (None, 0.0))
    monkeypatch.setattr(chat_router, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat_router, "classify_intent_local", lambda q: (None, 0.0))
    monkeypatch.setattr(chat_router, "resolve_named_entity", lambda q: (None, set()))

    r = _ask(client, "یک پرسش بی‌پاسخ")
    assert r.status_code == 503, r.text

    rows = _messages()
    assert [m["role"] for m in rows] == ["visitor"], rows
    assert rows[0]["text"] == "یک پرسش بی‌پاسخ"


# ── Registering halfway through ──────────────────────────────────────────

def _register(client, phone="09121234567"):
    """Walk the real OTP flow and return the challenge id."""
    r = client.post("/api/auth/otp/request", json={
        "destination": phone, "first_name": "سارا", "last_name": "احمدی",
        "job": "مدیر", "position": "مدیرعامل", "interests": "هوش مصنوعی"})
    assert r.status_code == 200, r.text
    challenge_id = r.json()["challenge_id"]

    from app.db.connection import get_db_connection
    from app.services import otp as otp_service
    code = "123456"
    conn = get_db_connection()
    conn.execute("UPDATE otp_challenges SET code_hmac = ? WHERE id = ?",
                 (otp_service._code_hmac(challenge_id, code), challenge_id))
    conn.commit()
    conn.close()

    v = client.post("/api/auth/otp/verify",
                    json={"challenge_id": challenge_id, "code": code})
    assert v.status_code == 200, v.text
    return challenge_id


def test_registering_mid_chat_claims_the_conversation_and_its_history(client):
    _ask(client, LIST_QUESTION)
    _ask(client, CURATED_QUESTION)
    before = len(_messages())
    assert before == 4

    _register(client)

    visitors = _rows("SELECT * FROM visitors")
    assert len(visitors) == 1, visitors
    assert visitors[0]["first_name"] == "سارا"
    assert visitors[0]["job"] == "مدیر"
    assert visitors[0]["phone"], "the raw number is what makes a lead reachable"

    conv = _conversations()[0]
    assert conv["visitor_id"] == visitors[0]["id"]
    # The four messages sent before they had a name are still there.
    assert len(_messages()) == before


def test_a_message_sent_after_registering_joins_the_same_conversation(client):
    _ask(client, LIST_QUESTION)
    _register(client)
    _ask(client, CURATED_QUESTION)

    convs = _conversations()
    assert len(convs) == 1, convs
    assert convs[0]["visitor_id"]
    assert len(_messages()) == 4


def test_correcting_the_profile_updates_the_same_visitor(client):
    challenge_id = _register(client)
    r = client.post("/api/auth/profile", json={
        "challenge_id": challenge_id, "job": "پژوهشگر",
        "position": "مدیر فنی", "interests": "رباتیک"})
    assert r.status_code == 200, r.text

    visitors = _rows("SELECT * FROM visitors")
    assert len(visitors) == 1, "one phone is one person, not two"
    assert visitors[0]["job"] == "پژوهشگر"
    assert visitors[0]["first_name"] == "سارا", "the name it proved is kept"


def test_an_unverified_challenge_creates_no_visitor(client):
    r = client.post("/api/auth/otp/request", json={
        "destination": "09129998877", "first_name": "بی‌نام"})
    assert r.status_code == 200, r.text
    assert _rows("SELECT * FROM visitors") == []


# ── Retention ────────────────────────────────────────────────────────────

def test_the_retention_setting_prunes_the_transcript(client):
    _ask(client, LIST_QUESTION)
    _ask(client, CURATED_QUESTION)
    assert len(_messages()) == 4

    from app.db.connection import get_db_connection
    from app.db.queries import purge_chat_logs, set_setting
    conn = get_db_connection()
    conn.execute("UPDATE messages SET created_at = datetime('now','-40 days')")
    conn.execute("UPDATE conversations"
                 " SET last_message_at = datetime('now','-40 days')")
    conn.execute("UPDATE chat_logs SET created_at = datetime('now','-40 days')")
    conn.commit()
    conn.close()

    set_setting("chat_log_retention_days", "30")
    purge_chat_logs()

    assert _messages() == []
    assert _conversations() == []


def test_retention_off_keeps_the_transcript(client):
    _ask(client, LIST_QUESTION)
    from app.db.connection import get_db_connection
    from app.db.queries import purge_chat_logs, set_setting
    conn = get_db_connection()
    conn.execute("UPDATE messages SET created_at = datetime('now','-400 days')")
    conn.commit()
    conn.close()

    set_setting("chat_log_retention_days", "0")
    purge_chat_logs()
    assert len(_messages()) == 2
