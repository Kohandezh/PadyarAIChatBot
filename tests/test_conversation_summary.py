"""A long chat is summarized instead of being sent whole.

WHAT IT FIXES. The model was handed the last HISTORY_TURNS (5) turns and
nothing else, so turn six forgot turn one. Sending everything instead is not
an option: the prompt grows without limit and the oldest turns are the least
useful part of it. So the OLD part is folded into one short paragraph kept on
the conversation row, the recent turns stay word for word, and the two travel
together.

THREE RULES THIS FILE HOLDS.

1. A short conversation is not summarized. Below the threshold the recent
   turns already ARE the whole conversation, and a summary would be a second,
   worse copy of it.
2. The work is INCREMENTAL. Each refresh reads the messages written since the
   last one, not the whole conversation again.
3. THE SUMMARY IS CONTEXT, NEVER CONTENT. A model wrote it, so it is not
   evidence about the exhibition. It is never shown to a visitor, and it
   reaches only the SELECTION call, whose entire output is record ids the code
   then prints out of the database. It never reaches get_openai_response(),
   the one call that writes prose a visitor reads. That is structural, not a
   promise, and the last two tests hold it in place.

And a failure here costs the visitor nothing: the summary is refreshed AFTER
the answer has been sent, so a dead or slow provider never delays a turn.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import SUMMARIZE_AFTER_MESSAGES
from app.services import conversations


DATASET = [
    ("faq-hours", "ساعت کاری نمایشگاه",
     "نمایشگاه هر روز از ساعت ۹ صبح تا ۱۸ باز است.", ""),
    ("faq-map", "نقشه سالن", "نقشه سالن‌ها در ورودی اصلی نصب شده است.", ""),
]

CONV = "conv-long"


class _Reply:
    """The shape `padyar_ai.classify` returns, with only what we read."""

    def __init__(self, content):
        self.content = content
        self.tokens_total = 5
        self.cost = 0.0


def _seed():
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for entry_id, title, text, video in DATASET:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, ?)", (entry_id, title, text, video))
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "summary.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "false")
        _seed()
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _talk(turns: int, conversation_id: str = CONV):
    """`turns` question-and-answer pairs written straight into the store."""
    for i in range(turns):
        conversations.append_visitor_message(conversation_id, f"پرسش {i}")
        conversations.append_assistant_message(
            conversation_id, f"پاسخ {i}", source="local", confidence=0.9,
            entry_id="faq-hours")


def _row(conversation_id: str = CONV) -> dict:
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return dict(conn.execute("SELECT * FROM conversations WHERE id = ?",
                                 (conversation_id,)).fetchone())
    finally:
        conn.close()


def _capture_summarizer(monkeypatch, reply="خلاصهٔ گفتگو: بازدیدکننده دنبال ساعت کاری بود."):
    """Stub the routed classify task and record what it was asked."""
    from app.services.ai import wrapper
    seen = []

    async def fake_classify(query, system_prompt="", **kwargs):
        seen.append({"query": query, "system_prompt": system_prompt})
        return _Reply(reply)

    monkeypatch.setattr(wrapper.padyar_ai, "classify", fake_classify)
    return seen


def _dead_summarizer(monkeypatch):
    from app.services.ai import wrapper

    async def boom(*a, **k):
        raise RuntimeError("summarizer provider is down")

    monkeypatch.setattr(wrapper.padyar_ai, "classify", boom)


# ── Short conversations are left alone ───────────────────────────────────

async def test_a_short_conversation_is_not_summarized(client, monkeypatch):
    seen = _capture_summarizer(monkeypatch)
    _talk(2)
    assert await conversations.update_summary(CONV) == ""
    assert seen == [], "no provider call for a conversation that fits"
    assert _row()["summary"] == ""


async def test_one_message_below_the_threshold_is_still_left_alone(client, monkeypatch):
    seen = _capture_summarizer(monkeypatch)
    _talk((SUMMARIZE_AFTER_MESSAGES - 2) // 2)
    assert _row()["message_count"] < SUMMARIZE_AFTER_MESSAGES
    await conversations.update_summary(CONV)
    assert seen == []


async def test_an_unknown_conversation_is_a_no_op(client, monkeypatch):
    seen = _capture_summarizer(monkeypatch)
    assert await conversations.update_summary("nobody-here") == ""
    assert await conversations.update_summary("") == ""
    assert seen == []


# ── A long conversation is folded ────────────────────────────────────────

async def test_a_long_conversation_is_summarized(client, monkeypatch):
    seen = _capture_summarizer(monkeypatch)
    _talk(7)                       # 14 messages
    summary = await conversations.update_summary(CONV)

    assert len(seen) == 1, seen
    assert summary
    row = _row()
    assert row["summary"] == summary
    assert row["summary_upto_id"] > 0


async def test_the_recent_turns_are_not_folded(client, monkeypatch):
    """The router still sends those word for word. Summarizing a turn that is
    also quoted in full spends tokens saying it twice."""
    seen = _capture_summarizer(monkeypatch)
    _talk(7)                       # 14 messages; the newest 10 stay verbatim
    await conversations.update_summary(CONV)

    asked = seen[0]["query"]
    assert "پرسش 0" in asked, asked
    assert "پرسش 6" not in asked, "the newest turn must not be folded"


async def test_the_refresh_is_incremental(client, monkeypatch):
    seen = _capture_summarizer(monkeypatch)
    _talk(7)
    first = await conversations.update_summary(CONV)
    upto = _row()["summary_upto_id"]

    seen2 = _capture_summarizer(monkeypatch, reply="خلاصهٔ به‌روزشده.")
    _talk(4, CONV)                 # four more turns
    second = await conversations.update_summary(CONV)

    assert second == "خلاصهٔ به‌روزشده."
    assert _row()["summary_upto_id"] > upto
    asked = seen2[0]["query"]
    assert first in asked, "the previous summary is carried in, not re-derived"
    assert "پرسش 0" not in asked, "already-folded messages are not read again"


async def test_nothing_new_means_no_provider_call(client, monkeypatch):
    _capture_summarizer(monkeypatch)
    _talk(7)
    await conversations.update_summary(CONV)

    seen2 = _capture_summarizer(monkeypatch)
    kept = await conversations.update_summary(CONV)
    assert seen2 == [], "a second refresh with nothing new must cost nothing"
    assert kept == _row()["summary"]


async def test_the_summary_is_clipped(client, monkeypatch):
    from app.config import SUMMARY_MAX_CHARS
    _capture_summarizer(monkeypatch, reply="ب" * (SUMMARY_MAX_CHARS * 3))
    _talk(7)
    summary = await conversations.update_summary(CONV)
    assert len(summary) == SUMMARY_MAX_CHARS


async def test_the_prompt_asks_for_the_visitors_language(client, monkeypatch):
    seen = _capture_summarizer(monkeypatch)
    _talk(7)
    await conversations.update_summary(CONV, lang="en")
    assert "English" in seen[0]["system_prompt"]

    seen2 = _capture_summarizer(monkeypatch)
    _talk(4)
    await conversations.update_summary(CONV, lang="fa")
    assert "Persian" in seen2[0]["system_prompt"]


# ── A failed summary is not a failed conversation ────────────────────────

async def test_a_dead_summarizer_keeps_what_was_already_stored(client, monkeypatch):
    _capture_summarizer(monkeypatch, reply="خلاصهٔ اول.")
    _talk(7)
    await conversations.update_summary(CONV)

    _dead_summarizer(monkeypatch)
    _talk(4)
    assert await conversations.update_summary(CONV) == ""
    assert _row()["summary"] == "خلاصهٔ اول.", "the good summary survives"


async def test_an_empty_reply_keeps_what_was_already_stored(client, monkeypatch):
    _capture_summarizer(monkeypatch, reply="خلاصهٔ اول.")
    _talk(7)
    await conversations.update_summary(CONV)

    _capture_summarizer(monkeypatch, reply="   ")
    _talk(4)
    await conversations.update_summary(CONV)
    assert _row()["summary"] == "خلاصهٔ اول."


def test_the_refresh_actually_runs_after_the_answer(client, monkeypatch):
    """The scheduling itself, not just the function. A background task that is
    never run is a feature that silently does not exist."""
    ran = []

    async def spy(conversation_id, lang="fa"):
        ran.append((conversation_id, lang))
        return ""

    monkeypatch.setattr(conversations, "update_summary", spy)
    from app.routers import chat as chat_router
    monkeypatch.setattr(chat_router.conversations, "update_summary", spy)

    r = client.post("/chat", json={"message": "ساعت کاری نمایشگاه چیست؟",
                                   "lang": "fa"})
    assert r.status_code == 200, r.text
    assert len(ran) == 1, ran
    assert ran[0][1] == "fa"


def test_a_dead_summarizer_still_answers_the_visitor(client, monkeypatch):
    """End to end. The refresh runs after the response is on the wire, so a
    provider that raises cannot cost the visitor their answer."""
    _dead_summarizer(monkeypatch)
    for _ in range(3):
        r = client.post("/chat", json={"message": "ساعت کاری نمایشگاه چیست؟",
                                       "lang": "fa"})
        assert r.status_code == 200, r.text
        assert "۹ صبح" in r.json()["text"] or r.json()["source"] == "no_answer"


# ── Context, never content ───────────────────────────────────────────────

def test_the_summary_takes_the_oldest_slot(client):
    """`_history_for` hands the model the recent turns newest-first with the
    summary last, which is the OLDEST position once the prompt reverses it."""
    from app.routers import chat as chat_router
    from app.db.queries import log_chat

    for i in range(6):
        log_chat(f"پرسش {i}", f"پاسخ {i}", "text", "local", 0.9,
                 conversation_id=CONV)
    conversations.get_or_create_conversation(CONV)
    conversations.set_summary(CONV, "خلاصهٔ قبلی", 1)

    history = chat_router._history_for(CONV, "fa")
    assert history[-1]["response"] == "خلاصهٔ قبلی"
    assert history[-1]["source"] == "summary"
    # It costs one slot, never the newest turn.
    assert history[0]["query"] == "پرسش 5"
    from app.config import HISTORY_TURNS
    assert len(history) == HISTORY_TURNS


def test_no_summary_means_the_history_is_unchanged(client):
    from app.routers import chat as chat_router
    from app.db.queries import log_chat

    for i in range(3):
        log_chat(f"پرسش {i}", f"پاسخ {i}", "text", "local", 0.9,
                 conversation_id=CONV)
    history = chat_router._history_for(CONV, "fa")
    assert [h["query"] for h in history] == ["پرسش 2", "پرسش 1", "پرسش 0"]


def test_the_summary_reaches_the_selection_call_and_not_the_prose_call(
        client, monkeypatch):
    """The whole grounding argument in one test. The selection call may see
    the summary — its output is record ids, which the renderer then prints out
    of the database. The prose call may NOT: it is the one place a model
    writes what a visitor reads."""
    from app.db.queries import set_setting
    from app.routers import chat as chat_router
    set_setting("openai_enabled", "true")

    seen = {"selection": None, "prose": None}

    async def fake_select(user_query, candidates, history, lang="fa"):
        seen["selection"] = history
        return None

    async def fake_prose(query, lang="fa"):
        seen["prose"] = query
        return "یک پاسخ تولیدشده", 1, 0.0

    async def fake_classify(query):
        return None, 0, 0.0

    monkeypatch.setattr(chat_router, "select_records", fake_select)
    monkeypatch.setattr(chat_router, "get_openai_response", fake_prose)
    monkeypatch.setattr(chat_router, "classify_intent", fake_classify)
    monkeypatch.setattr(chat_router, "find_best_match", lambda q: (None, 0.0))
    monkeypatch.setattr(chat_router, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat_router, "classify_intent_local", lambda q: (None, 0.0))
    monkeypatch.setattr(chat_router, "resolve_named_entity", lambda q: (None, set()))
    monkeypatch.setattr(chat_router, "unknown_salient_tokens", lambda q: set())
    monkeypatch.setattr(chat_router.conversations, "get_summary",
                        lambda cid: "SECRET_SUMMARY_MARKER")

    r = client.post("/chat", json={"message": "نقشه سالن کجاست", "lang": "fa"})
    assert r.status_code == 200, r.text

    replayed = json.dumps(seen["selection"], ensure_ascii=False)
    assert "SECRET_SUMMARY_MARKER" in replayed, replayed
    assert seen["prose"] == "نقشه سالن کجاست"
    assert "SECRET_SUMMARY_MARKER" not in (seen["prose"] or "")
    assert "SECRET_SUMMARY_MARKER" not in r.json()["text"]


def test_the_summary_is_never_shown_to_a_visitor(client, monkeypatch):
    """Nothing renders `conversations.summary` into an answer."""
    from app.routers import chat as chat_router
    monkeypatch.setattr(chat_router.conversations, "get_summary",
                        lambda cid: "SECRET_SUMMARY_MARKER")
    r = client.post("/chat", json={"message": "ساعت کاری نمایشگاه چیست؟",
                                   "lang": "fa"})
    assert r.status_code == 200, r.text
    assert "SECRET_SUMMARY_MARKER" not in r.json()["text"]
