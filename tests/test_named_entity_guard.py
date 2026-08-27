"""Entity confusion between KNOWN entries: the named entity always wins.

WHAT HAPPENED (live, 2026-08-27): the unknown-entity guard only protects
against entities the corpus does NOT know. Two failures with KNOWN entities:

  * «شماره مدیرعامل دوندگان لبه علم» — the company «دوندگان لبه علم» exists
    in the dataset, but the questions blend anchored on «شماره/تلفن» and
    served the دبیرخانه phone FAQ at 0.87. Wrong entity, high confidence.
  * «درباره دکیو بهم بگو» — دکیو exists in the dataset, but the blend only
    reached 0.691 (< 0.70), so the query paid for an LLM call and got a
    refusal, when the local dekio entry was the right answer.

THE FIX under test: `resolve_named_entity()` maps distinctive title tokens
(title document-frequency == 1) to their entry. When a query names exactly
ONE known entity, /chat (a) overrides a trusted local answer that is a
different entry and never mentions the entity, and (b) rescues the query
before the AI tier when no local tier qualified. Tier 0 (near-exact curated
hit) stays authoritative, ambiguity (two entities) never guesses, and the
unknown-entity guard still runs first.
"""
import pytest
from fastapi.testclient import TestClient


# A small controlled corpus. Title-token document frequencies decide what is
# "distinctive": شرکت appears in two titles (not distinctive), نمایشگاه in
# two titles (not distinctive); دوندگان/لبه/علم, دکیو, دبیرخانه and
# تاریخ/برگزاری each appear in exactly one title.
DATASET = [
    ("edgerunners", "شرکت دوندگان لبه علم",
     "شرکت دوندگان لبه علم یک شرکت دانش بنیان است. "
     "مدیرعامل شرکت در غرفه حضور دارد و راه ارتباط با شرکت از طریق غرفه است."),
    ("dekio", "شرکت دکیو",
     "اطلاعات درباره شرکت دکیو: دکیو سازنده سامانه های نرم افزاری است "
     "و در نمایشگاه حضور دارد."),
    ("phone-faq", "دبیرخانه نمایشگاه",
     "شماره تلفن دبیرخانه نمایشگاه ۰۲۱۱۲۳۴۵۶۷۸ است "
     "و راه ارتباط و تماس همین شماره است."),
    ("inotex-date", "تاریخ برگزاری نمایشگاه",
     "نمایشگاه اینوتکس در خرداد برگزار می شود."),
]

QUESTIONS = [
    # The lexical anchor the incident rode on: a curated phone question whose
    # tokens dominate a "phone number of company X" query.
    (1, "شماره تلفن و راه ارتباط", "phone-faq"),
    (2, "تاریخ برگزاری نمایشگاه اینوتکس", "inotex-date"),
    # Hand-curated mapping used by the Tier 0 test: an exact hit on this row
    # must stay authoritative even though the question names the company.
    (3, "شماره تماس دوندگان لبه علم", "phone-faq"),
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "guard.db"))
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        # TF-IDF backend: no embedding model, no trained intent classifier —
        # the scores in these tests stay deterministic and offline.
        set_setting("search_backend", "tfidf")

        import app.db.connection as dbc
        conn = dbc.get_db_connection()
        conn.execute("DELETE FROM dataset")
        conn.execute("DELETE FROM questions")
        # Empty synonym table: expansion must not change token overlaps and
        # blur the Jaccard scores these tests are built on.
        conn.execute("DELETE FROM synonyms")
        conn.executemany(
            "INSERT INTO dataset (id, title, text, video_url) VALUES (?, ?, ?, ?)",
            [(i, t, x, "") for i, t, x in DATASET])
        conn.executemany(
            "INSERT INTO questions (id, question, dataset_id, video_url) VALUES (?, ?, ?, ?)",
            [(i, q, d, "") for i, q, d in QUESTIONS])
        conn.commit()
        conn.close()

        from app.services import search
        search.load_dataset_internal()

        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _mock_ai(monkeypatch, classified=None, generated="پاسخ تولیدشدهٔ AI",
             forbid=False):
    """Patch the AI tier the way chat.py imports it (by name, on the router).

    ``forbid=True`` turns any AI call into a test failure — for the tests
    proving a named entity is answered locally without an LLM.
    """
    import app.routers.chat as chat

    async def fake_classify(query):
        if forbid:
            pytest.fail("the AI tier must not be called for this query")
        return classified, 1, 0.0

    async def fake_generate(query, lang="fa"):
        if forbid:
            pytest.fail("the AI tier must not be called for this query")
        return generated, 2, 0.0

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)


def _ask(client, message):
    return client.post("/chat", json={"message": message, "lang": "fa"})


def _text_of(dataset_id):
    return next(x for i, _t, x in DATASET if i == dataset_id)


# ── Unit: what the resolver returns ──────────────────────────────────────

def test_resolver_finds_the_single_named_entity(client):
    from app.services import search
    entry, tokens = search.resolve_named_entity("شماره مدیرعامل دوندگان لبه علم")
    assert entry is not None and entry["id"] == "edgerunners"
    assert tokens == {"دوندگان", "لبه", "علم"}


def test_resolver_refuses_to_guess_between_two_entities(client):
    from app.services import search
    entry, tokens = search.resolve_named_entity("دوندگان لبه علم یا دکیو")
    assert entry is None and tokens == set()


def test_shared_title_words_are_not_distinctive(client):
    from app.services import search
    # شرکت appears in two titles, نمایشگاه in two — neither names an entity.
    entry, _ = search.resolve_named_entity("شرکت در نمایشگاه")
    assert entry is None


# ── Integration: the ladder through the real route ──────────────────────

def test_query_naming_a_company_is_not_answered_by_the_phone_faq(client, monkeypatch):
    """The incident shape: the query's phone words give the curated phone
    question a trusted blend score, but the visitor named the company — the
    company's own entry must be served, source local_entity."""
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شماره تلفن و راه ارتباط با دوندگان")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_entity", body
    assert body["text"] == _text_of("edgerunners")


def test_known_entity_below_threshold_is_served_locally_without_an_llm(client, monkeypatch):
    """The دکیو shape: generic similarity is mediocre, but the entity is known —
    answered from its own entry, and the AI tier is never called."""
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "درباره دکیو بهم بگو")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_entity", body
    assert body["text"] == _text_of("dekio")


def test_query_naming_two_entities_gets_no_override(client, monkeypatch):
    """Ambiguity never guesses: two named entities → the normal pipeline
    (here: the AI tier, since no local tier qualifies)."""
    _mock_ai(monkeypatch)
    r = _ask(client, "دوندگان لبه علم یا دکیو")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] != "local_entity", body
    assert body["source"] == "openai"


def test_query_with_no_named_entity_conflict_keeps_its_trusted_local_answer(client, monkeypatch):
    """Regression: the trusted local tier still serves as before — the anchor
    resolves to the same entry that wins retrieval, so nothing changes."""
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "تاریخ برگزاری نمایشگاه")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local", body
    assert body["text"] == _text_of("inotex-date")


def test_tier0_exact_curated_hit_is_not_overridden(client, monkeypatch):
    """A near-exact hit on a hand-curated question is a deliberate mapping:
    it stays authoritative even though the question names another entity."""
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شماره تماس دوندگان لبه علم")   # == q3, mapped to phone-faq
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_questions", body
    assert body["text"] == _text_of("phone-faq")


def test_unknown_entity_guard_still_wins_over_the_entity_rescue(client, monkeypatch):
    """A query with an unknown salient token (the الکامپ shape) defers to AI
    even when it ALSO contains a known entity token — the guard nulls
    everything first and the rescue must not fire."""
    _mock_ai(monkeypatch)
    r = _ask(client, "تاریخ برگزاری نمایشگاه الکامپ با حضور دوندگان لبه علم")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "openai", body
    assert body["text"] == "پاسخ تولیدشدهٔ AI"
