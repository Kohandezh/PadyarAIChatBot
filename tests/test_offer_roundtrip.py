"""The offer round trip: what we WRITE and what we READ must be one shape.

WHAT WAS BROKEN (measured 2026-08-28). `render_options()` writes the query that
produced a list into `offer_state` under the key "q". `parse_offer()` reads "q"
and hands it back to callers under the key "query", because that is what reads
well at the call site. Two spellings, on purpose, and for a while nothing
owned the translation between them.

A pick turn re-stores the offer so a following "4" still resolves. It did that
with a plain `json.dumps(offer)`, which wrote the PARSED spelling: "query".
Nothing reads "query". So one pick between a list and «بیشتر» silently blanked
the source query, the pager lost the only thing it can rebuild the whole match
set from, and page 2 fell back to the stored ids, which `offer_state` caps at
OFFER_IDS_MAX. With 70 AI companies seeded the visitor was told there are 50,
and companies 51..70 became unreachable for good. No error, no log line: the
answer just quietly shrank.

`answer.dump_offer()` is now the single writer, and it is the only place both
spellings appear. This file pins that contract from three sides:

  1. the identity: a parsed offer written back out parses to itself
  2. the STORED key is "q", which is the half a plain `json.dumps(offer)` gets
     wrong and the half no reader would notice
  3. the whole thing end to end, because an install upgrades the round trip by
     upgrading chat.py, not by upgrading a unit test

Plus one compatibility case: an offer written before `total`, `filter` and `q`
existed must still parse. An install mid-upgrade has rows of the old shape in
chat_logs and it must page, not crash.
"""
import json

import pytest
from fastapi.testclient import TestClient


# ── The corpus ───────────────────────────────────────────────────────────
#
# 70 companies in one field, so the match set is larger than OFFER_IDS_MAX (50)
# and the stored ids genuinely cannot answer for the whole list. The exhibition
# really does have ~169 companies in one field, so this is the normal case.

MANY = [
    (f"co-{n}", f"شرکت واحد {n}",
     f"معرفی شرکت واحد {n}: فعال در هوش مصنوعی.", f"ghorfe-{n:02d}.mp4")
    for n in range(1, 71)
]

EXTRA = [
    ("faq-guide", "اطلاعات نمایشگاه",
     "درباره غرفه ها و ساعت کاری توضیح کامل در ورودی نمایشگاه موجود است.", ""),
]

LIST_QUESTION = "شرکت‌های هوش مصنوعی را معرفی کن"


def _seed(companies=MANY, extra=EXTRA, field="هوش مصنوعی"):
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for i, title, text, video in list(extra) + list(companies):
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, ?)", (i, title, text, video))
    conn.commit()
    conn.close()

    from app.services import leads
    leads.ensure_tables()
    conn = dbc.get_db_connection()
    for i, _t, _x, _v in companies:
        conn.execute(
            "INSERT INTO company_profiles (dataset_id, activity_field,"
            " province, created_at, updated_at)"
            " VALUES (?, ?, 'تهران', '2026-08-28', '2026-08-28')", (i, field))
    conn.commit()
    conn.close()

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "roundtrip.db"))
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


def _mock_ai(monkeypatch):
    """Switch the AI provider off. The list, the pick and the pager are all
    deterministic tiers, so nothing in this file needs a model. If one of them
    started calling out, that is itself a defect."""
    import app.routers.chat as chat
    calls = {"classify": 0, "generate": 0, "provider": 0}

    async def fake_classify(query):
        calls["classify"] += 1
        return None, 1, 0.0

    async def fake_generate(query, lang="fa"):
        calls["generate"] += 1
        return "پاسخ تولیدشدهٔ AI", 2, 0.0

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


_DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_FOLD.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})


def _numbers(text):
    """The numbers of the numbered lines, digit script folded away."""
    import re
    out = []
    for line in text.translate(_DIGIT_FOLD).splitlines():
        m = re.match(r"^\s*(\d+)\s*[.)]\s*\S", line)
        if m:
            out.append(int(m.group(1)))
    return out


def _stored_offers():
    """Every offer_state written so far, oldest first, already decoded."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = [r[0] for r in conn.execute(
            "SELECT offer_state FROM chat_logs WHERE offer_state <> ''"
            " ORDER BY id ASC")]
    finally:
        conn.close()
    return [json.loads(r) for r in rows]


# ── 1. The identity ──────────────────────────────────────────────────────

FULL_OFFER = {
    "ids": [f"co-{n}" for n in range(1, 51)],
    "shown": 15,
    "total": 70,
    "filter": "هوش مصنوعی",
    "query": LIST_QUESTION,
}


def test_a_parsed_offer_written_back_out_parses_to_exactly_itself():
    """The contract in one line. Every field a turn was handed has to survive
    being re-stored, or the next turn is working from less than this one had."""
    from app.services import answer
    assert answer.parse_offer(answer.dump_offer(FULL_OFFER)) == FULL_OFFER


def test_the_query_is_stored_under_the_key_the_reader_looks_for():
    """THE defect, at the smallest scale that can show it. `parse_offer` reads
    "q"; the parsed dict calls the same value "query". A writer that just
    serialises the parsed dict stores a key nothing reads, and the loss is
    invisible, because the offer still parses. It just comes back with an
    empty query. So the stored spelling is asserted directly."""
    from app.services import answer
    stored = json.loads(answer.dump_offer(FULL_OFFER))

    assert stored["q"] == LIST_QUESTION, stored
    assert "query" not in stored, stored
    assert stored["total"] == 70 and stored["filter"] == "هوش مصنوعی", stored


def test_an_offer_written_before_total_filter_and_query_existed_still_parses():
    """An install mid-upgrade has old rows in chat_logs. The pager must read
    them and page, not crash and not refuse: a visitor who typed «بیشتر» five
    minutes after the deploy is not interested in our migration."""
    from app.services import answer
    old = json.dumps({"ids": ["co-1", "co-2", "co-3"], "shown": 2},
                     ensure_ascii=False)

    offer = answer.parse_offer(old)
    assert offer is not None, old
    assert offer["ids"] == ["co-1", "co-2", "co-3"], offer
    assert offer["shown"] == 2, offer
    # The three young fields fall back to something usable rather than absent:
    # a total of "what we have", and no filter and no query to rebuild from.
    assert offer["total"] == 3, offer
    assert offer["filter"] == "", offer
    assert offer["query"] == "", offer
    # And it can be re-stored from there, which is what a pick turn does.
    assert answer.parse_offer(answer.dump_offer(offer)) == offer


# ── 2. The same thing where it actually bit: list → pick → «بیشتر» ───────

def test_a_pick_between_the_list_and_more_does_not_blank_the_source_query(
        client, monkeypatch):
    """END TO END, and this is the shape the defect had in the booth.

    Visitors compare: they list, they open one company, then they ask for more
    names. That middle turn re-stores the offer. With the query lost there, the
    pager can no longer re-derive the match set, falls back to the stored ids
    (capped at OFFER_IDS_MAX), and page 2 announces 50 companies out of 70
    while quietly making the last twenty unreachable.

    `options_shown` is 15 only to keep this to six requests; seventy names at
    five a page is fifteen turns and trips the rate limit."""
    _seed()
    from app.db.queries import set_setting
    set_setting("options_shown", "15")
    ai = _mock_ai(monkeypatch)

    first = _ask(client, LIST_QUESTION).json()
    assert first["source"] == "local_company_search", first
    assert "70" in first["text"].splitlines()[0].translate(_DIGIT_FOLD), first["text"]

    # The middle turn. It answers about one company and re-stores the list.
    picked = _ask(client, "3").json()
    assert picked["source"] == "local_pick", picked

    # The query has to still be in what that turn stored.
    assert _stored_offers()[-1].get("q") == LIST_QUESTION, _stored_offers()[-1]

    seen = _numbers(first["text"])
    for page in range(4):
        more = _ask(client, "بیشتر")
        assert more.status_code == 200, more.text
        body = more.json()
        head = body["text"].splitlines()[0].translate(_DIGIT_FOLD)
        assert "70" in head, (page, head)
        assert "هوش" in head and "مصنوعی" in head, (page, head)
        seen += _numbers(body["text"])

    assert seen == list(range(1, 71)), seen
    assert ai == {"classify": 0, "generate": 0, "provider": 0}, ai


def test_a_pick_after_a_pick_still_resolves_against_the_same_list(client, monkeypatch):
    """The reason a pick re-stores the offer at all, kept alongside the query
    assertion above so a future writer cannot fix one by dropping the other."""
    _seed(companies=MANY[:6])
    ai = _mock_ai(monkeypatch)

    listed = _ask(client, LIST_QUESTION).json()
    assert listed["source"] == "local_company_search", listed
    fourth = next(o for o in listed["options"] if o["n"] == 4)

    assert _ask(client, "2").json()["source"] == "local_pick"
    second_pick = _ask(client, "4").json()
    assert second_pick["source"] == "local_pick", second_pick
    assert second_pick["video_url"] == fourth["video_url"], (second_pick, fourth)
    assert ai == {"classify": 0, "generate": 0, "provider": 0}, ai
