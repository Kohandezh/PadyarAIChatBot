"""The selection call is billed on EVERY exit, so chat_logs must record it.

WHAT WAS BROKEN: `select_records()` is a real provider round-trip. The router
carried its `tokens`/`cost` into `chat_logs` on two of its four exits (mode
"answer" and mode "options") and dropped them on the other two:

  * mode "none" goes straight to `get_openai_response` and logged only that
    second call, and
  * the fall-through — the model named a record we could no longer resolve —
    logged only the `classify_intent` call that followed.

The provider billed for the selection call in all four cases. The admin
dashboard sums `tokens`/`cost` out of `chat_logs`, so on those two exits it
under-reported what the install actually spent. A booth owner reading that
dashboard would plan a day's budget on a number that is too small.

THE PROVIDER IS STUBBED IN EVERY TEST. `padyar_ai.generate` is replaced on the
process-wide instance and the Tier 2 tail is replaced on the router module, so
nothing here can reach a network.
"""
import json

import pytest
from fastapi.testclient import TestClient


# ── The corpus ───────────────────────────────────────────────────────────
#
# Same shape as tests/test_grounded_selection.py: FAQ rows that supply the
# vocabulary of the neutral query (so the unknown-token guard stays quiet and
# the ladder really walks down to the selection tier), plus two companies for
# the model to choose between.

FAQ_GUIDE_TEXT = (
    "درباره غرفه ها و ساعت کاری توضیح کامل در ورودی نمایشگاه موجود است."
)
FAQ_HOURS_TEXT = "ساعت کاری نمایشگاه از نه صبح تا شش بعد از ظهر است."
ALFA_TEXT = "معرفی شرکت آلفا: فعال در هوش مصنوعی و پردازش تصویر در غرفه خود."
BETA_TEXT = "شرکت بتا سامانه های هوش مصنوعی صنعتی می سازد و در غرفه حضور دارد."

DATASET = [
    ("faq-guide", "اطلاعات نمایشگاه", FAQ_GUIDE_TEXT, ""),
    ("faq-hours", "ساعت کاری", FAQ_HOURS_TEXT, ""),
    ("co-alfa", "شرکت آلفا", ALFA_TEXT, "ghorfe-01.mp4"),
    ("co-beta", "شرکت بتا", BETA_TEXT, "ghorfe-02.mp4"),
]

NEUTRAL_QUERY = "درباره غرفه ها توضیح بده"

# What the stubbed provider bills for one selection call, and what the two
# tail calls bill. Three distinct numbers so a dropped term is visible in the
# failure message instead of hiding behind a coincidence.
SELECTION_TOKENS, SELECTION_COST = 42, 0.001
CLASSIFY_TOKENS, CLASSIFY_COST = 5, 0.002
GENERATE_TOKENS, GENERATE_COST = 7, 0.004

# No digits and no URL-ish token: the prose firewall must pass this, so the
# turn lands on the "openai" exit and not on the refusal exit.
GENERATED_TEXT = "پاسخ آزاد دستیار"


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

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "accounting.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        # TF-IDF backend: no embedding model, no trained intent classifier —
        # deterministic and offline.
        set_setting("search_backend", "tfidf")

        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _stub_provider(monkeypatch, decision: dict):
    """Scripted selection reply, billed at SELECTION_TOKENS/SELECTION_COST."""
    from app.services.ai import wrapper
    from app.services.ai.request import AIResponse

    async def fake_generate(messages, **kw):
        return AIResponse(content=json.dumps(decision), finish_reason="stop",
                          task=kw.get("task", "chat"), provider_type="stubprov",
                          model="stub-model", tokens_total=SELECTION_TOKENS,
                          cost=SELECTION_COST)

    monkeypatch.setattr(wrapper.padyar_ai, "generate", fake_generate)


def _stub_ai_tail(monkeypatch, classified=None):
    import app.routers.chat as chat

    async def fake_classify(query):
        return classified, CLASSIFY_TOKENS, CLASSIFY_COST

    async def fake_generate(query, lang="fa"):
        return GENERATED_TEXT, GENERATE_TOKENS, GENERATE_COST

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)


def _force_tier2(monkeypatch):
    """Null every local tier so the ladder reaches the selection tier."""
    import app.routers.chat as chat
    monkeypatch.setattr(chat, "find_best_match", lambda q: (None, 0.0))
    monkeypatch.setattr(chat, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat, "classify_intent_local", lambda q: (None, 0.0))


def _fake_candidates(monkeypatch, ids):
    import app.routers.chat as chat
    from app.services import search

    scores = [0.60 - 0.05 * i for i in range(len(ids))]
    cands = [(search.dataset_lookup[i], s, {"lexical": s})
             for i, s in zip(ids, scores)]
    monkeypatch.setattr(chat, "find_top_matches", lambda query, k=8: cands)


def _ask(client, message, lang="fa"):
    return client.post("/chat", json={"message": message, "lang": lang})


def _last_billing():
    """(source, tokens, cost) of the most recent answered turn."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT source, tokens, cost FROM chat_logs ORDER BY id DESC"
            " LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None, "the turn was not logged at all"
    row = dict(row)
    return row["source"], row["tokens"], row["cost"]


# ── Exit 3: mode "none" → a written answer ───────────────────────────────

def test_mode_none_logs_the_selection_call_as_well_as_the_written_answer(
        client, monkeypatch):
    """"None of these" is a paid answer. The turn costs TWO provider calls and
    the log has to show both, or the dashboard bills the install for one."""
    _seed()
    _force_tier2(monkeypatch)
    _fake_candidates(monkeypatch, ["co-alfa", "co-beta"])
    _stub_ai_tail(monkeypatch)
    _stub_provider(monkeypatch, {"mode": "none", "ids": [], "lead": "",
                                 "reason": "nothing here fits"})

    r = _ask(client, NEUTRAL_QUERY)
    assert r.status_code == 200, r.text

    source, tokens, cost = _last_billing()
    assert source == "openai", source
    assert tokens == SELECTION_TOKENS + GENERATE_TOKENS, tokens
    assert cost == pytest.approx(SELECTION_COST + GENERATE_COST), cost


# ── Exit 4: a chosen record that can no longer be resolved ───────────────

def test_an_unresolvable_choice_still_logs_what_the_selection_call_cost(
        client, monkeypatch):
    """The model named a record and an admin deleted the row mid-turn, so the
    router falls through to the old classifier path. The selection call still
    happened and was still billed."""
    _seed()
    _force_tier2(monkeypatch)
    _fake_candidates(monkeypatch, ["co-alfa", "co-beta"])

    import app.routers.chat as chat
    from app.services import search
    # The row vanished between retrieval and render — the one way the "answer"
    # branch reaches the fall-through without the model breaking its contract.
    monkeypatch.setattr(chat, "get_entry", lambda entry_id: None)
    _stub_ai_tail(monkeypatch, classified=search.dataset_lookup["co-beta"])
    _stub_provider(monkeypatch, {"mode": "answer", "ids": ["co-alfa"],
                                 "lead": "", "reason": "این یکی"})

    r = _ask(client, NEUTRAL_QUERY)
    assert r.status_code == 200, r.text

    source, tokens, cost = _last_billing()
    assert source == "openai_classified", source
    assert tokens == SELECTION_TOKENS + CLASSIFY_TOKENS, tokens
    assert cost == pytest.approx(SELECTION_COST + CLASSIFY_COST), cost


# ── The two exits that already accounted correctly must stay correct ─────

def test_the_answer_exit_still_logs_exactly_the_selection_call(
        client, monkeypatch):
    """A record served straight from the choice costs ONE provider call, and
    the log must not grow a second one when the fall-through is fixed."""
    _seed()
    _force_tier2(monkeypatch)
    _fake_candidates(monkeypatch, ["co-alfa", "co-beta"])
    _stub_ai_tail(monkeypatch)
    _stub_provider(monkeypatch, {"mode": "answer", "ids": ["co-alfa"],
                                 "lead": "", "reason": "این یکی"})

    r = _ask(client, NEUTRAL_QUERY)
    assert r.status_code == 200, r.text

    source, tokens, cost = _last_billing()
    assert source == "ai_selected", source
    assert tokens == SELECTION_TOKENS, tokens
    assert cost == pytest.approx(SELECTION_COST), cost
