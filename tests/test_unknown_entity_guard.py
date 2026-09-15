"""The unknown-entity incident: an unknown named entity must not get a confident local answer.

WHAT HAPPENED (live, 2026-08-26): a query naming a rival exhibition —
«تاریخ برگزاری نمایشگاه هانوفر» carries the shape — was served
the event's own date at 0.844 confidence. The rival's name appears in no document, no curated
question and no synonym; the lexical retrievers silently drop unknown tokens,
so the query degraded to its common words and matched strongly — while the
single word that made the question about ANOTHER exhibition vanished. The AI
tier, which can actually judge an out-of-domain entity, was never reached
because a local tier had already answered confidently.

THE FIX under test: `unknown_salient_tokens()` flags salient tokens the whole
corpus knows nothing about (with edit-1 typo tolerance), and /chat then
refuses to serve from ANY local tier for that query — the ladder goes to the
AI tier, or 503 if the AI is down. Never a confidently wrong local answer.
"""
import datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "guard.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        # The guard flags only tokens the WHOLE corpus does not know, so the
        # tests must own their vocabulary: installs no longer ship a seeded
        # knowledge base, and leaning on one would make these tests pass or
        # fail with the default content instead of with the guard.
        import app.db.connection as dbc
        conn = dbc.get_db_connection()
        conn.execute("DELETE FROM synonyms")
        conn.executemany(
            "INSERT INTO dataset (id, title, text, video_url) VALUES (?, ?, ?, '')",
            [("event-date", "تاریخ برگزاری نمایشگاه",
              "تاریخ برگزاری نمایشگاه پردیار امسال خرداد ماه است."),
             ("faq-ask", "پرسش های پرتکرار",
              "پاسخ هر پرسش را بپرسید: نمایشگاه چیست و غرفه هر شرکت کجاست."),
             ("faq-en", "Tickets", "How do I book a booth for the show?")])
        conn.executemany(
            "INSERT INTO questions (id, question, dataset_id, video_url)"
            " VALUES (?, ?, ?, '')",
            [(1, "تاریخ برگزاری نمایشگاه پردیار", "event-date")])
        conn.commit()
        conn.close()
        from app.services import search
        search.load_dataset_internal()
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _mock_ai(monkeypatch, classified=None, generated="پاسخ تولیدشدهٔ AI", fail=False):
    """Patch the AI tier the way chat.py imports it (by name, on the router)."""
    import app.routers.chat as chat

    async def fake_classify(query):
        if fail:
            raise RuntimeError("provider down")
        return classified, 1, 0.0

    async def fake_generate(query, lang="fa"):
        return generated, 2, 0.0

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)


def _ask(client, message):
    return client.post("/chat", json={"message": message, "lang": "fa"})


# ── Unit: what the guard flags ───────────────────────────────────────────
# (the `client` fixture boots the app, which seeds and loads the index these
# read — the guard is a no-op before any index exists)

def test_unknown_salient_tokens_on_the_incident_query(client):
    from app.services import search
    assert search.unknown_salient_tokens("تاریخ برگزاری نمایشگاه هانوفر") == ["هانوفر"]


def test_known_queries_flag_nothing(client):
    from app.services import search
    for q in ("تاریخ برگزاری نمایشگاه پردیار",
              "غرفه هر شرکت کجاست؟",
              "How do I book a booth?"):
        assert search.unknown_salient_tokens(q) == [], q


def test_typo_tolerance_keeps_a_one_edit_word_known(client):
    from app.services import search
    # برگذاری (ذ) is one substitution away from برگزاری — a typo, not an
    # unknown entity. Flagging it would defer every query with a common
    # Persian spelling slip.
    assert "برگذاری" not in search.unknown_salient_tokens("تاریخ برگذاری نمایشگاه پردیار")


def test_unimported_company_name_is_unknown(client):
    from app.services import search
    assert search.unknown_salient_tokens("شرکت دکیو چیست") == ["دکیو"]


# ── Integration: the ladder through the real route ──────────────────────

def test_unknown_entity_gets_the_ai_answer_not_a_local_one(client, monkeypatch):
    _mock_ai(monkeypatch)   # classifier says out_of_domain → generation runs
    r = _ask(client, "تاریخ برگزاری نمایشگاه هانوفر")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "openai", \
        "the unknown-entity query must not be served by any local tier"
    assert body["text"] == "پاسخ تولیدشدهٔ AI"


def test_known_entity_still_answers_locally(client, monkeypatch):
    """The guard must not become a blanket deferral: a query about something
    the corpus owns keeps its local (free, instant) answer."""
    _mock_ai(monkeypatch)
    r = _ask(client, "تاریخ برگزاری نمایشگاه پردیار")
    assert r.status_code == 200, r.text
    assert r.json()["source"].startswith("local"), r.json()["source"]


def test_unknown_entity_with_ai_down_is_503_not_a_wrong_local_answer(client, monkeypatch):
    _mock_ai(monkeypatch, fail=True)
    r = _ask(client, "تاریخ برگزاری نمایشگاه هانوفر")
    assert r.status_code == 503, r.text
