"""Follow-up field questions: «کجاس؟» after a company answer.

TWO LIVE FAILURES THIS FILE PINS (Elecomp, 2026-09-01):
1. «کجاس» right after a company's answer → «متوجه منظورت نشدم».
2. «بابا کدوم غرفه س کدوم سالن» → a markdown essay asking WHICH company.

The company being discussed is one turn up: chat_logs.entry_id is the
memory (same staleness window as the pick tier's offer state), and the
company-field tier answers from that entry.
"""
import pytest
from fastapi.testclient import TestClient

CO_TITLE = "شرکت کهن سیستم فردا"
CO_TEXT = ("شرکت کهن سیستم فردا در حوزه فناوری اطلاعات و هوش مصنوعی"
           " فعالیت می‌کند.")
AI_CO = ("co-ai", "شرکت هوشمند نمونه",
         "شرکت هوشمند نمونه در حوزه هوش مصنوعی فعالیت می‌کند؛"
         " غرفه این شرکت در سالن ۶ است و در نقشه نمایشگاه کجاست مشخص است.")


@pytest.fixture
def client(tmp_path, monkeypatch):
    # The embeddings model is CACHED locally; a stray online revision check
    # has been observed hanging the whole suite behind a download (HF xet).
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "followup.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO companies (id, title, text, video_url, hall,"
            " booth_number, activity_field)"
            " VALUES ('co-ksf', ?, ?, '', 'سالن ۴۰', '40-12', 'فناوری')",
            (CO_TITLE, CO_TEXT))
        conn.execute(
            "INSERT INTO companies (id, title, text, video_url, hall,"
            " booth_number, activity_field)"
            " VALUES (?, ?, ?, '', 'سالن ۶', '6-1', 'هوش مصنوعی')", AI_CO)
        conn.execute(
            "INSERT INTO questions (question, dataset_id, video_url)"
            " VALUES ('شرکت کهن سیستم فردا چیست؟', 'co-ksf', '')")
        conn.commit()
        conn.close()
        # The vocabulary and the curated index build at app boot, before
        # these rows existed.
        from app.services.search import reindex_and_publish
        reindex_and_publish()
        yield c
    security._chat_rate_limits.clear()


def _ask(client, message):
    return client.post("/chat", json={"message": message, "lang": "fa"})


# ── the detection itself ──────────────────────────────────────────────────

def test_bare_field_followup_detection():
    from app.services.company_search import bare_field_followup
    assert bare_field_followup("کجاس")
    assert bare_field_followup("کجاست؟")
    assert bare_field_followup("کدوم غرفه س کدوم سالن")
    assert bare_field_followup("بابا کدوم غرفه س کدوم سالن")
    assert bare_field_followup("نشانی غرفه")
    # Content left over: a topic, an entity, a greeting — never a follow-up.
    assert not bare_field_followup("غرفه هوش مصنوعی کجاس")
    assert not bare_field_followup("سلام")
    assert not bare_field_followup("غرفه 377")


# ── end to end ────────────────────────────────────────────────────────────

def test_where_is_it_after_a_company_answer(client):
    r1 = _ask(client, "شرکت کهن سیستم فردا چیست؟")
    assert r1.status_code == 200, r1.text
    r2 = _ask(client, "کجاس")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["source"] == "local_company_field", body
    assert "سالن ۴۰" in body["text"] or "۴۰" in body["text"]


def test_which_booth_which_hall_after_a_company_answer(client):
    _ask(client, "شرکت کهن سیستم فردا چیست؟")
    r = _ask(client, "کدوم غرفه س کدوم سالن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body


def test_baba_prefix_does_not_pay_for_an_essay(client):
    _ask(client, "شرکت کهن سیستم فردا چیست؟")
    r = _ask(client, "بابا کدوم غرفه س کدوم سالن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert "پادیار" not in body["text"]  # no self-introduction for a follow-up


def test_no_history_means_no_followup(client):
    """A field word with nothing discussed yet must NOT invent an entity;
    the ordinary pipeline runs (offline it ends at AI-unavailable)."""
    r = _ask(client, "کجاس")
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert r.json()["source"] != "local_company_field"


def test_a_topic_question_is_still_a_list(client):
    """«غرفه هوش مصنوعی کجاس» names a facet: the company-list tier owns
    it, the follow-up tier must not steal it."""
    r = _ask(client, "غرفه هوش مصنوعی کجاس")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local_company_search", r.text


# ── the category overview (live failure: «شامل» glued one facet) ──────────

def test_exhibition_fields_question_gets_the_overview(client):
    r = _ask(client, "نمایشگاه امسال شامل چه حوزه های هست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_facet_overview", body
    assert "هوش مصنوعی" in body["text"]
    # Never one company, and never the glued facet's descriptive name.
    assert "سخت‌افزار و نرم‌افزار" not in body["text"]


def test_glue_word_no_longer_makes_a_facet_distinctive():
    """«شامل» inside one facet's name must not answer a query with that
    facet — the word organizes the description, nobody means it."""
    from app.services.company_search import (
        _facets, _select_facets)
    companies = [
        {"id": "c1", "title": "تست", "text": "تست فعالیت فناوری اطلاعات",
         "activity_field": "فناوری اطلاعات شامل سخت‌افزار و نرم‌افزار",
         "booth_number": "", "hall": ""},
    ]
    assert "شامل" in next(iter(_facets(companies).values()))
    # ...but selection ignores it:
    sel = _select_facets(["نمایشگاه", "امسال", "شامل", "چه", "حوزه", "های",
                          "هست"], companies)
    assert sel is None


def test_specific_field_question_is_still_a_list(client):
    r = _ask(client, "شرکت های حوزه هوش مصنوعی")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local_company_search", r.text
