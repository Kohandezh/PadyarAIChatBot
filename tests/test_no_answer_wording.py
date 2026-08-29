"""What the bot says when it cannot answer, and when it is only half sure.

TWO LIES THIS FILE REMOVES.

The first: `app/routers/chat.py` raised `HTTPException(503, "AI service
unavailable")` when it simply found no confident match. Nothing was
unavailable. `static/chat/core.js` turns any 503 into "the AI service is not
responding", so a visitor asking about something we have no record for was
told the machine was broken. It was not. We looked, and we had nothing.
The product owner's sentence for that case is
«متاسفانه در این خصوص نمی‌توانم پاسخی به شما بدهم».

The second: an answer served BELOW the trust bar looked exactly like an answer
served above it. The owner's rule is that a half-sure answer is still worth
giving, as long as it ends by inviting the correction:
«اگر منظورت چیز دیگه‌ای بود بهم بگو».

The 503 that is TRUE — the AI provider is genuinely down and no local match
was strong enough — stays a 503 and keeps its own message. The two must not
collapse into one, or the operator loses the only signal that says "go look at
the provider".
"""
import pytest
from fastapi.testclient import TestClient

from app.services import scope


DATASET = [
    ("faq-hours", "ساعت کاری نمایشگاه",
     "نمایشگاه هر روز از ساعت ۹ صبح تا ۱۸ باز است.", ""),
]

ANSWERLESS = "قیمت بلیت قطار به شیراز چند است"


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
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "no_answer.db"))
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


def _ask(client, message, lang="fa"):
    return client.post("/chat", json={"message": message, "lang": lang})


def _nothing_matches(monkeypatch):
    """Every local tier comes up empty. The AI tier is off in the fixture, so
    the request lands on the no-confident-match branch."""
    from app.routers import chat as chat_router
    monkeypatch.setattr(chat_router, "find_best_match", lambda q: (None, 0.0))
    monkeypatch.setattr(chat_router, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat_router, "classify_intent_local", lambda q: (None, 0.0))
    monkeypatch.setattr(chat_router, "resolve_named_entity", lambda q: (None, set()))


# ── "I have no answer" is an answer, not an outage ───────────────────────

def test_no_confident_match_is_200_with_the_owners_sentence(client, monkeypatch):
    _nothing_matches(monkeypatch)
    r = _ask(client, ANSWERLESS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "no_answer"
    assert body["text"] == "متاسفانه در این خصوص نمی‌توانم پاسخی به شما بدهم."
    assert body["video_url"] is None


def test_the_english_visitor_gets_english(client, monkeypatch):
    _nothing_matches(monkeypatch)
    r = _ask(client, "how much is a train ticket", lang="en")
    assert r.status_code == 200, r.text
    assert r.json()["text"] == scope.DEFAULT_NO_ANSWER_EN


def test_a_customer_can_change_the_wording_without_a_deploy(client, monkeypatch):
    from app.db.queries import set_setting
    set_setting("no_answer_text_fa", "چیزی در این باره ندارم.")
    _nothing_matches(monkeypatch)
    r = _ask(client, ANSWERLESS)
    assert r.json()["text"] == "چیزی در این باره ندارم."


def test_admin_text_with_a_stray_brace_does_not_raise(client, monkeypatch):
    """str.replace, never .format(). An operator typing a { must not 500."""
    from app.db.queries import set_setting
    set_setting("no_answer_text_fa", "پاسخی ندارم {نمیدانم")
    _nothing_matches(monkeypatch)
    r = _ask(client, ANSWERLESS)
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "پاسخی ندارم {نمیدانم"


def test_the_no_answer_turn_is_recorded_as_its_own_tier(client, monkeypatch):
    """The owner needs to count how often this happens, so it must not hide
    inside the old `system` bucket the dashboard ignores."""
    _nothing_matches(monkeypatch)
    _ask(client, ANSWERLESS)

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT source, response FROM chat_logs"
                           " ORDER BY id DESC LIMIT 1").fetchone()
        msg = conn.execute("SELECT source, text FROM messages"
                           " WHERE role = 'assistant' ORDER BY id DESC"
                           " LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["source"] == "no_answer"
    assert row["response"] == scope.DEFAULT_NO_ANSWER_FA
    assert msg["source"] == "no_answer"


def test_a_no_answer_turn_is_never_replayed_to_the_model(client, monkeypatch):
    """«متاسفانه ...» is a sentence WE wrote. Handing it back as a prior
    assistant answer teaches the model to write it."""
    _nothing_matches(monkeypatch)
    _ask(client, ANSWERLESS)

    from app.routers import chat as chat_router
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conversation_id = conn.execute(
            "SELECT conversation_id FROM chat_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()["conversation_id"]
    finally:
        conn.close()

    assert conversation_id
    history = chat_router._history_for(conversation_id, "fa")
    assert history == [], history


# ── The 503 that is true keeps its own message ───────────────────────────

def test_a_dead_provider_is_still_a_503(client, monkeypatch):
    from app.db.queries import set_setting
    from app.routers import chat as chat_router
    set_setting("openai_enabled", "true")

    async def dead(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(chat_router, "select_records", dead)
    monkeypatch.setattr(chat_router, "classify_intent", dead)
    monkeypatch.setattr(chat_router, "get_openai_response", dead)
    _nothing_matches(monkeypatch)

    r = _ask(client, ANSWERLESS)
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "AI service unavailable"
    # And the two paths do not say the same thing.
    assert scope.no_answer_text("fa") not in r.text


def test_the_three_sentences_are_three_different_sentences(client):
    """A refusal, a no-answer and an outage mean different things to a
    visitor, so they must not converge on one string."""
    assert scope.refusal_text("fa") != scope.no_answer_text("fa")
    assert scope.refusal_text("en") != scope.no_answer_text("en")
    assert scope.hedge_text("fa") != scope.no_answer_text("fa")


# ── The hedge ────────────────────────────────────────────────────────────

def _serve_at(monkeypatch, score):
    from app.routers import chat as chat_router
    entry = {"id": "faq-hours", "title": "t", "text": "پاسخ محلی",
             "video_url": ""}
    monkeypatch.setattr(chat_router, "find_best_match", lambda q: (entry, score))
    monkeypatch.setattr(chat_router, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat_router, "classify_intent_local", lambda q: (None, 0.0))
    monkeypatch.setattr(chat_router, "resolve_named_entity", lambda q: (None, set()))
    monkeypatch.setattr(chat_router, "unknown_salient_tokens", lambda q: set())


def test_an_uncertain_answer_ends_by_inviting_the_correction(client, monkeypatch):
    """0.55: above LOCAL_FALLBACK (0.45), below TRUSTED (0.70). The visitor
    gets the answer we found AND is told to say so if it was the wrong one."""
    _serve_at(monkeypatch, 0.55)
    r = _ask(client, "ساعت بازدید نمایشگاه")
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    assert text.startswith("پاسخ محلی"), text
    assert text.rstrip().endswith("اگر منظورت چیز دیگه‌ای بود بهم بگو."), text


def test_a_confident_answer_carries_no_hedge(client, monkeypatch):
    """A line on every answer is a line nobody reads."""
    _serve_at(monkeypatch, 0.95)
    r = _ask(client, "ساعت بازدید نمایشگاه")
    assert r.json()["text"] == "پاسخ محلی"


def test_the_trust_bar_is_the_line(client, monkeypatch):
    """Exactly at TRUSTED_MATCH_THRESHOLD is confident, not uncertain."""
    from app.config import TRUSTED_MATCH_THRESHOLD
    _serve_at(monkeypatch, TRUSTED_MATCH_THRESHOLD)
    assert _ask(client, "ساعت بازدید نمایشگاه").json()["text"] == "پاسخ محلی"


def test_the_english_hedge_is_english(client, monkeypatch):
    _serve_at(monkeypatch, 0.55)
    r = _ask(client, "opening hours", lang="en")
    assert r.json()["text"].rstrip().endswith(scope.DEFAULT_HEDGE_EN)


def test_the_hedge_wording_is_a_setting_too(client, monkeypatch):
    from app.db.queries import set_setting
    set_setting("hedge_text_fa", "اگر اشتباه بود بگو.")
    _serve_at(monkeypatch, 0.55)
    r = _ask(client, "ساعت بازدید نمایشگاه")
    assert r.json()["text"].rstrip().endswith("اگر اشتباه بود بگو.")


def test_the_hedge_is_stored_with_the_answer(client, monkeypatch):
    """The transcript is evidence: what was stored is what was shown."""
    _serve_at(monkeypatch, 0.55)
    r = _ask(client, "ساعت بازدید نمایشگاه")

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT text FROM messages WHERE role = 'assistant'"
                           " ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["text"] == r.json()["text"]


def test_the_no_answer_sentence_never_carries_a_hedge(client, monkeypatch):
    """"I have no answer, and if you meant something else tell me" is two
    apologies for one thing."""
    _nothing_matches(monkeypatch)
    text = _ask(client, ANSWERLESS).json()["text"]
    assert scope.DEFAULT_HEDGE_FA not in text
