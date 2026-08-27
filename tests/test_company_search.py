"""The company-list tier: list questions are answered from the database.

WHAT HAPPENED (live, 2026-08-27): «شرکت‌های هوش مصنوعی اینوتکس را معرفی کن»
is a LIST question, but single-document retrieval can only ever pick one
entry. The faq-20 entry — literally the out-of-scope REFUSAL text — contains
«هوش مصنوعی اینوتکس» and acted as a token magnet: Tier 1 served the refusal
at 0.81. The dataset holds one row per company plus a company_profiles row
carrying activity_field, so the right answer was a list of the AI companies.

THE FIX under test: app/services/company_search.answer_company_list() detects
list intent deterministically, filters dataset × company_profiles by the
query's remaining topic keywords, and /chat serves the result as source
"local_company_search" BEFORE the trusted T1/questions block — gated so an
unknown salient token still defers to AI and a query naming one specific
company is never hijacked into a list.
"""
import pytest
from fastapi.testclient import TestClient


# The refusal-shaped FAQ from the incident: its text mentions the exact topic
# words, which is what made it a token magnet for Tier 1. Its text also keeps
# the corpus vocabulary rich enough (معرفی، حوزه، داریم، زیست، فناوری) that
# the unknown-entity guard does not fire on the test queries.
REFUSAL_TEXT = (
    "این سوال خارج از حوزه هوش مصنوعی اینوتکس است. "
    "ما فقط درباره نمایشگاه پاسخ داریم و معرفی شرکت های حاضر، "
    "از هوش مصنوعی تا زیست فناوری، از طریق همین گفتگو انجام می شود."
)

# Three companies in AI plus one unrelated company. Titles avoid the topic
# words on purpose: a topic token that is distinctive to ONE title would make
# resolve_named_entity() claim the query and gate the list tier off.
COMPANIES = [
    ("co-ava", "شرکت آوا", "معرفی شرکت آوا: فعال در هوش مصنوعی و پردازش تصویر.",
     "هوش مصنوعی"),
    ("co-rayan", "شرکت رایان", "شرکت رایان سامانه های هوش مصنوعی می سازد.",
     "هوش مصنوعی"),
    ("co-negar", "شرکت نگار", "شرکت نگار در زمینه هوش مصنوعی گفتاری کار می کند.",
     "هوش مصنوعی"),
    ("co-dekio", "شرکت دکیو", "اطلاعات درباره شرکت دکیو: دکیو سازنده سامانه های نرم افزاری است.",
     "نرم افزار"),
]

AI_COMPANY_TITLES = ("شرکت آوا", "شرکت رایان", "شرکت نگار")


def _seed(companies, extra_dataset=(), with_profiles=True):
    """Insert dataset rows (+ optional company_profiles) and reindex."""
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    # Empty synonym table: expansion must not blur the token overlaps the
    # intent/keyword checks are built on.
    conn.execute("DELETE FROM synonyms")
    for i, title, text in extra_dataset:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, '')", (i, title, text))
    for i, title, text, _field in companies:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, '')", (i, title, text))
    conn.commit()
    conn.close()

    if with_profiles:
        # The leads module owns the SQLite mirror of company_profiles — the
        # same ensure call the app itself uses.
        from app.services import leads
        leads.ensure_tables()
        conn = dbc.get_db_connection()
        for i, _title, _text, field in companies:
            conn.execute(
                "INSERT INTO company_profiles (dataset_id, activity_field,"
                " created_at, updated_at) VALUES (?, ?, '2026-08-27', '2026-08-27')",
                (i, field))
        conn.commit()
        conn.close()

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "companies.db"))
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


def _mock_ai(monkeypatch, classified=None, generated="پاسخ تولیدشدهٔ AI",
             forbid=False):
    """Patch the AI tier the way chat.py imports it (by name, on the router).

    ``forbid=True`` turns any AI call into a test failure — for the tests
    proving a list question is answered locally without an LLM.
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


def test_a_list_question_lists_the_ai_companies_instead_of_the_refusal_faq(client, monkeypatch):
    """The measured incident shape: the refusal FAQ is a token magnet for
    «هوش مصنوعی اینوتکس», but the visitor asked for a LIST — every AI company
    is named, the refusal text is nowhere, and no LLM is involved."""
    _seed(COMPANIES, extra_dataset=[("faq-20", "سوال خارج از موضوع", REFUSAL_TEXT)])
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شرکت‌های هوش مصنوعی را معرفی کن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body
    for title in AI_COMPANY_TITLES:
        assert title in body["text"], body["text"]
    assert "شرکت دکیو" not in body["text"], body["text"]
    assert REFUSAL_TEXT not in body["text"]
    assert "خارج از حوزه" not in body["text"], body["text"]


def test_a_count_question_answers_from_the_list_tier_with_the_count(client, monkeypatch):
    _seed(COMPANIES, extra_dataset=[("faq-20", "سوال خارج از موضوع", REFUSAL_TEXT)])
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "چند شرکت در حوزه هوش مصنوعی داریم؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body
    assert "3 شرکت" in body["text"], body["text"]


def test_a_query_naming_one_company_is_not_hijacked_by_the_list_tier(client, monkeypatch):
    """«شرکت دکیو چیست؟» names ONE company — the answer is that company's own
    entry, never a list."""
    _seed(COMPANIES, extra_dataset=[("faq-20", "سوال خارج از موضوع", REFUSAL_TEXT)])
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شرکت دکیو چیست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] != "local_company_search", body
    assert body["text"] == next(t for i, _ti, t, _f in COMPANIES if i == "co-dekio")


def test_a_list_topic_no_company_matches_falls_through_to_the_pipeline(client, monkeypatch):
    """List intent with a keyword («زیست فناوری») no company works in: the
    tier returns None and the normal pipeline (here: the AI tier) judges."""
    _seed(COMPANIES, extra_dataset=[("faq-20", "سوال خارج از موضوع", REFUSAL_TEXT)])
    _mock_ai(monkeypatch)
    r = _ask(client, "شرکت‌های زیست فناوری را معرفی کن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] != "local_company_search", body


def test_without_a_company_profiles_table_the_list_query_still_answers(client, monkeypatch):
    """An install without the leads module has no company_profiles table.
    The tier must silently switch off — no exception, normal pipeline."""
    _seed(COMPANIES, extra_dataset=[("faq-20", "سوال خارج از موضوع", REFUSAL_TEXT)],
          with_profiles=False)
    _mock_ai(monkeypatch)
    r = _ask(client, "شرکت‌های هوش مصنوعی را معرفی کن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] != "local_company_search", body


def test_a_long_list_is_capped_at_fifteen_names_and_mentions_the_rest(client, monkeypatch):
    many = [(f"co-{n}", f"شرکت نمونه {n}",
             f"معرفی شرکت نمونه {n}: فعال در هوش مصنوعی.", "هوش مصنوعی")
            for n in range(1, 19)]
    _seed(many, extra_dataset=[("faq-20", "سوال خارج از موضوع", REFUSAL_TEXT)])
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شرکت‌های هوش مصنوعی را معرفی کن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body
    assert body["text"].count("•") == 15, body["text"]
    assert "و 3 شرکت دیگر" in body["text"], body["text"]
    assert "18 شرکت" in body["text"], body["text"]
