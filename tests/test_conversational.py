"""The conversational gates: small talk, self-introductions, gibberish, and
affirmative/negative replies to the bot's own offers.

TWO LIVE FAILURES THIS FILE PINS (Elecomp, 2026-08-31):

1. «سلام چطوری؟ اسم من سینا هست اسم تو چی هست؟» — a visitor introducing
   THEMSELF. The named-entity anchor read «سینا», matched the company
   «گسترش فناوری‌های پیشرفته و هوشمند سینا», and served its profile (which
   contains the CEO's name). A visitor's own name must never trigger the
   anchor: a self-introduction nulls every local tier and the model, which
   reads the whole sentence, answers.

2. «بگو» after the bot offered something — answered as a brand-new query and
   re-introduced the exhibition. «بگو» is an AFFIRMATIVE to the last offer:
   it replays the stored proposal query, or re-serves the offered list.

Everything sits behind the `chat_conversational_tier` settings row. The
kill-switch test proves "0" restores the old behaviour — including the old
wrong answer — with no deploy.
"""
import pytest
from fastapi.testclient import TestClient


# ── The corpora ───────────────────────────────────────────────────────────

SMALLTALK_PHRASES = [
    "چطوری", "چطورم", "خوبی", "حالت چطوره", "چه حالی", "ممنون", "مرسی",
    "دستت درد نکنه", "خداحافظ", "بای", "فعلا",
    "تو کی هستی", "تو چی هستی", "اسمت چیه", "اسم تو چیه",
    "چه کمکی می‌تونی بکنی", "چه کارهایی می‌تونی بکنی", "قابلیت‌هات چیه",
]

SINA_TITLE = "گسترش فناوری‌های پیشرفته و هوشمند سینا"
# «هست» is in here on purpose: the kill-switch test walks the pre-gate
# pipeline, and the unknown-entity gate must not fire on the incident
# message's common words — it would defer to AI and make the old wrong
# answer (entity rescue serving this company) non-deterministic.
SINA_TEXT = ("شرکت گسترش فناوری‌های پیشرفته و هوشمند سینا از شرکت‌های حوزه "
             "هوش مصنوعی است و مدیرعامل آن مهدی روحانی نژاد هست.")
SINA_COMPANY = [("co-sina", SINA_TITLE, SINA_TEXT, "")]

FAQ_ROWS = [
    ("faq-guide", "اطلاعات نمایشگاه",
     "درباره غرفه ها و ساعت کاری توضیح کامل در ورودی نمایشگاه موجود است.", ""),
    ("faq-news", "اخبار نمایشگاه",
     "برنامه روزهای اسفند و هر خبر تازه در ورودی نمایشگاه اعلام می شود.", ""),
]

LIST_COMPANIES = [
    ("co-1", "شرکت آلفا", "معرفی شرکت آلفا: فعال در هوش مصنوعی.", "v1.mp4"),
    ("co-2", "شرکت بتا", "شرکت بتا سامانه های هوش مصنوعی می سازد.", "v2.mp4"),
    ("co-3", "شرکت گاما", "شرکت گاما در زمینه هوش مصنوعی کار می کند.", "v3.mp4"),
]

LIST_QUESTION = "شرکت‌های هوش مصنوعی را معرفی کن"
INCIDENT_MESSAGE = "اسم من سینا هست اسم تو چی هست؟"

GIBBERISH_REPLY = "متوجه منظورت نشدم. می‌تونی سؤالت رو یه جور دیگه بپرسی؟"
DECLINE_REPLY = "باشه! اگه سؤال دیگه‌ای درباره نمایشگاه داری در خدمتم."


def _seed(companies=(), extra=FAQ_ROWS):
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM companies")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for i, title, text, video in extra:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, ?)", (i, title, text, video))
    for i, title, text, video in companies:
        conn.execute(
            "INSERT INTO companies (id, title, text, video_url, activity_field)"
            " VALUES (?, ?, ?, ?, 'هوش مصنوعی')", (i, title, text, video))
    conn.commit()
    conn.close()

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "conversational.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")

        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _mock_ai(monkeypatch, classified=None,
             generated="مرسی که پرسیدید! من دستیار نمایشگاه هستم."):
    """Patch the AI tier the way chat.py imports it, and COUNT the calls.

    Counted rather than made fatal: chat.py wraps the whole Tier 2 block in
    `except Exception`, so raising inside the stub would be swallowed and the
    test would report a confusing fallback instead of "the AI was called".
    Same helper shape as tests/test_chat_options_pick.py.
    """
    import app.routers.chat as chat
    calls = {"classify": 0, "generate": 0, "provider": 0}

    async def fake_classify(query):
        calls["classify"] += 1
        return classified, 1, 0.0

    async def fake_generate(query, lang="fa"):
        calls["generate"] += 1
        return generated, 2, 0.0

    from app.services.ai import wrapper
    from app.services.ai.request import AIResponse

    async def fake_provider(messages, **kw):
        calls["provider"] += 1
        return AIResponse(content="{}", provider_type="stubprov", model="stub")

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)
    monkeypatch.setattr(wrapper.padyar_ai, "generate", fake_provider)
    return calls


def _ask(client, message, lang="fa"):
    return client.post("/chat", json={"message": message, "lang": lang})


# ── classify_conversational: small talk ───────────────────────────────────

@pytest.mark.parametrize("phrase", SMALLTALK_PHRASES)
def test_smalltalk_phrase_alone_is_smalltalk(phrase):
    from app.services.conversational import classify_conversational
    assert classify_conversational(phrase) == ("smalltalk", None)


@pytest.mark.parametrize("phrase", SMALLTALK_PHRASES)
def test_smalltalk_phrase_with_leading_greeting(phrase):
    from app.services.conversational import classify_conversational
    assert classify_conversational(f"سلام {phrase}") == ("smalltalk", None)


def test_smalltalk_tolerates_arabic_yk_forms():
    # «كي»/«ك» (Arabic yāʾ/kāf) must normalize to the Persian forms before
    # the phrase comparison — the normalizer folds them, so one spelling is
    # enough.
    from app.services.conversational import classify_conversational
    assert classify_conversational("تو كي هستي؟")[0] == "smalltalk"


# ── classify_conversational: self-introduction ────────────────────────────

@pytest.mark.parametrize("message,name", [
    ("اسم من سینا هست", "سینا"),
    ("اسم من سینا هستم", "سینا"),
    ("اسمم سینا هست", "سینا"),
    ("اسمم سینا هستم", "سینا"),
    ("من سینا هستم", "سینا"),
    ("من سینا ام", "سینا"),
    ("اسم من سید محمد حسینی هست", "سید محمد حسینی"),
])
def test_self_intro_variants(message, name):
    from app.services.conversational import classify_conversational
    assert classify_conversational(message) == ("self_intro", name)


def test_incident_message_is_self_intro():
    # The exact production message (minus its greeting): small talk in front,
    # the introduction, and the bot's-name question behind it. All of it is
    # ONE conversational turn, and none of it names the company.
    from app.services.conversational import classify_conversational
    assert classify_conversational(
        "سلام چطوری؟ اسم من سینا هست اسم تو چی هست؟") == ("self_intro", "سینا")


def test_intro_plus_real_question_is_none():
    # THE RULE THAT DECIDES THE HARD CASE: an introduction followed by a real
    # question is a QUESTION. Classifying it self_intro would null the local
    # tiers and bury the very thing the visitor asked.
    from app.services.conversational import classify_conversational
    assert classify_conversational(
        "اسم من سینا هست، شرکت سینا کجاست؟")[0] == "none"


def test_ordinary_question_and_bare_greeting_are_none():
    from app.services.conversational import classify_conversational
    assert classify_conversational(LIST_QUESTION)[0] == "none"
    # A bare greeting stays on the intro-entry path that predates this tier.
    assert classify_conversational("سلام")[0] == "none"


# ── is_gibberish ──────────────────────────────────────────────────────────

_GIBBERISH_VOCAB = {"الکامپ", "سینا", "شرکت", "های", "سالن", "پنج", "نمایشگاه"}


def test_gibberish_single_unknown_token():
    from app.services.conversational import is_gibberish
    assert is_gibberish("ثطسث", _GIBBERISH_VOCAB) is True


def test_gibberish_two_unknown_short_tokens():
    from app.services.conversational import is_gibberish
    assert is_gibberish("ثطسث بسل", _GIBBERISH_VOCAB) is True


def test_known_token_is_not_gibberish():
    from app.services.conversational import is_gibberish
    assert is_gibberish("الکامپ", _GIBBERISH_VOCAB) is False
    assert is_gibberish("سینا", _GIBBERISH_VOCAB) is False


def test_long_unknown_token_is_not_gibberish():
    # Five characters is room for a real word we simply do not know — that is
    # the unknown-entity gate's job, not the gibberish answer's.
    from app.services.conversational import is_gibberish
    assert is_gibberish("ققنوس", _GIBBERISH_VOCAB) is False


def test_three_token_message_is_not_gibberish():
    from app.services.conversational import is_gibberish
    assert is_gibberish("شرکت‌های سالن پنج", _GIBBERISH_VOCAB) is False


def test_empty_vocab_never_claims_gibberish():
    # With no corpus loaded, nothing can testify that a token is unknown.
    from app.services.conversational import is_gibberish
    assert is_gibberish("ثطسث", set()) is False


# ── The proposal store ────────────────────────────────────────────────────

def test_proposal_round_trip_consumes(client):
    from app.services import conversational
    conversational.store_proposal("conv-x", LIST_QUESTION)
    assert conversational.take_proposal("conv-x") == LIST_QUESTION
    # Consumed: a second affirmative must not replay the same offer.
    assert conversational.take_proposal("conv-x") is None


def test_proposal_is_per_conversation(client):
    from app.services import conversational
    conversational.store_proposal("conv-a", LIST_QUESTION)
    assert conversational.take_proposal("conv-b") is None


# ── Integration: the incident, gated and ungated ──────────────────────────

def test_self_intro_does_not_serve_the_namesake_company(client, monkeypatch):
    """THE Elecomp incident. The anchor alone would serve the company profile
    (the kill-switch test below proves it); the conversational gate must
    deflect the whole message to the model instead."""
    _seed(SINA_COMPANY)
    _mock_ai(monkeypatch)
    r = _ask(client, INCIDENT_MESSAGE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "روحانی نژاد" not in body["text"]
    assert body["source"] not in ("local_entity", "local", "local_questions",
                                  "local_intent", "local_company_field",
                                  "local_company_search")


def test_kill_switch_restores_the_old_behaviour(client, monkeypatch):
    """`chat_conversational_tier = "0"` is the production kill switch: the
    pre-gate pipeline runs, entity rescue serves the namesake company, and
    the incident comes back — on demand, with no deploy."""
    _seed(SINA_COMPANY)
    _mock_ai(monkeypatch)
    from app.db.queries import set_setting
    set_setting("chat_conversational_tier", "0")
    r = _ask(client, INCIDENT_MESSAGE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_entity"
    assert "روحانی نژاد" in body["text"]


def test_smalltalk_is_answered_by_the_model_not_local_tiers(client, monkeypatch):
    # Product decision, 2026-08-31: ALL small talk goes to the model — canned
    # replies age badly in a white-label product where every customer's tone
    # is different. The local tiers must not answer it either.
    _seed(SINA_COMPANY)
    calls = _mock_ai(monkeypatch)
    r = _ask(client, "چطوری؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] not in ("local_entity", "local", "local_questions",
                                  "local_intent")
    assert "روحانی نژاد" not in body["text"]
    assert calls["generate"] == 1


# ── Integration: gibberish ────────────────────────────────────────────────

def test_gibberish_answers_locally_with_no_ai_call(client, monkeypatch):
    _seed(SINA_COMPANY)
    calls = _mock_ai(monkeypatch)
    r = _ask(client, "ثطسث")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_gibberish"
    assert body["text"] == GIBBERISH_REPLY
    assert calls == {"classify": 0, "generate": 0, "provider": 0}


# ── Integration: affirmative / negative replies to an offer ───────────────

def test_affirmative_reserves_the_offered_list(client, monkeypatch):
    """«بگو» after a numbered list is YES to that list, not a new query. The
    whole exchange must also work with the AI provider switched off — same
    property the pick tier is arranged around."""
    _seed(LIST_COMPANIES)
    calls = _mock_ai(monkeypatch)

    r1 = _ask(client, LIST_QUESTION)
    assert r1.status_code == 200, r1.text
    first = r1.json()
    assert first["source"] == "local_company_search"
    assert "آلفا" in first["text"]

    r2 = _ask(client, "بگو")
    assert r2.status_code == 200, r2.text
    again = r2.json()
    assert again["source"] == "local_affirm"
    assert "آلفا" in again["text"]
    # The re-served list stays pickable: the offer was re-stored.
    r3 = _ask(client, "۲")
    assert r3.status_code == 200, r3.text
    assert r3.json()["source"] == "local_pick"

    assert calls == {"classify": 0, "generate": 0, "provider": 0}


def test_negation_declines_locally(client, monkeypatch):
    _seed(LIST_COMPANIES)
    _mock_ai(monkeypatch)
    r1 = _ask(client, LIST_QUESTION)
    assert r1.json()["source"] == "local_company_search"

    r2 = _ask(client, "نه")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["source"] == "local_decline"
    assert body["text"] == DECLINE_REPLY


def test_affirmative_without_an_offer_is_just_a_query(client, monkeypatch):
    # No list, no proposal: «بگو» falls through to today's treatment instead
    # of inventing something to replay.
    _seed(SINA_COMPANY)
    _mock_ai(monkeypatch)
    r = _ask(client, "بگو")
    assert r.status_code == 200, r.text
    assert r.json()["source"] != "local_affirm"


# ── Integration: the converse decision, end to end ────────────────────────
# _mock_ai counts calls but always answers "{}" — these tests need the
# provider to return a real selection JSON, so they stub it themselves.

import json as _json


def _mock_ai_json(monkeypatch, content, calls=None, captured=None):
    """A canned provider response for select_records (same boundary as
    _mock_ai's fake_provider, but with a body we choose per test). `content`
    is one string or a list — a list hands out one reply per call, in
    order, so a multi-turn exchange can be scripted. `captured` collects
    the user message of each call, so a test can prove WHICH query the
    model received."""
    from app.services.ai import wrapper
    from app.services.ai.request import AIResponse

    replies = list(content) if isinstance(content, list) else None

    async def fake_provider(messages, **kw):
        if calls is not None:
            calls["provider"] += 1
        if captured is not None:
            captured.append(messages[-1].content)
        body = replies.pop(0) if replies else content
        return AIResponse(content=body, finish_reason="stop",
                          provider_type="stubprov", model="stub")

    monkeypatch.setattr(wrapper.padyar_ai, "generate", fake_provider)


def test_converse_decision_is_served_as_ai_converse(client, monkeypatch):
    _seed(SINA_COMPANY)
    _mock_ai_json(monkeypatch, _json.dumps({
        "mode": "converse", "ids": [],
        "lead": "سلام! من دستیار نمایشگاه هستم.",
        "reason": "small talk"}, ensure_ascii=False))
    r = _ask(client, "چطوری؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "ai_converse"
    assert body["text"] == "سلام! من دستیار نمایشگاه هستم."
    # The namesake company stayed out of a greeting, end to end.
    assert "روحانی نژاد" not in body["text"]


def test_converse_proposal_then_bego_replays_the_offered_query(
        client, monkeypatch):
    """The proposal handshake, v1 semantics: the model answers
    conversationally AND offers («می‌خوای … بگم؟»); the router stores the
    query that earned the offer. The visitor's «بگو» must RE-SEND that
    stored query to the model — never be read as a brand-new question
    (the old failure: a re-introduction). What the model does with the
    re-ask is then its own conversational move; a knowledge-base list can
    never arrive through converse (its firewall forbids record facts), so
    the honest assertion here is the routing, not the payload."""
    _seed(LIST_COMPANIES)
    calls = {"provider": 0}
    captured = []
    replies = [
        _json.dumps({
            "mode": "converse", "ids": [],
            "lead": "حتماً! می‌خوای لیست شرکت‌های هوش مصنوعی رو بگم؟",
            "reason": "offers the list"}, ensure_ascii=False),
        _json.dumps({
            "mode": "converse", "ids": [],
            "lead": "بفرما! از شرکت‌های هوش مصنوعی: آلفا، بتا و گاما.",
            "reason": "delivers on the offer"}, ensure_ascii=False),
    ]
    _mock_ai_json(monkeypatch, replies, calls, captured)

    r1 = _ask(client, "چطوری؟")
    assert r1.status_code == 200, r1.text
    assert r1.json()["source"] == "ai_converse"
    assert calls["provider"] == 1

    r2 = _ask(client, "بگو")
    assert r2.status_code == 200, r2.text
    again = r2.json()
    # «بگو» was resolved as the affirmative to the stored proposal: the
    # STORED query («چطوری؟») went back to the model, not the word «بگو».
    assert again["source"] == "ai_converse"
    assert again["text"].startswith("بفرما")
    assert calls["provider"] == 2
    assert captured[1] == "چطوری؟"
