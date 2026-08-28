"""The pick path must never hand a visitor an HTTP 500, and never fake a page.

WHY THIS FILE EXISTS. The pick tier runs at the TOP of `chat_endpoint`,
outside the `try/except` that wraps Tier 2, and the app registers no exception
handler. Anything that raises there is a 500 on the visitor's screen at the
booth. Three defects found in the adversarial review of 2026-08-28:

  1. `resolve_pick` gated on `str.isdigit()` and then called `int()`.
     `'²'.isdigit()` is True and `int('²')` raises ValueError, so a single
     character crashed the request after any options list. A digit run over
     4300 characters raises too (CPython's integer-string conversion limit).

  2. An options decision whose ids were ALL dropped by the grounding gate was
     relabelled mode "none", and `chat.py` reads "none" as "the model read the
     records and none match" — so it SKIPS `classify_intent`. A grounding
     failure then silently cost the visitor a whole working tier.

  3. The «بیشتر» pager rendered a page even when nothing survived, printing a
     zero count and still asking the visitor to choose one of nothing.
"""
import json
import re

import pytest
from fastapi.testclient import TestClient


# ── The corpus ───────────────────────────────────────────────────────────
#
# Eight companies in one field, so the default page of five leaves a second
# page for the pager tests, plus one FAQ row so the unknown-entity guard has
# corpus vocabulary to work with.

NAMES = ["آلفا", "بتا", "گاما", "دلتا", "اپسیلون", "زتا", "اتا", "تتا"]

COMPANIES = [
    (f"co-{n}", f"شرکت {name}", f"معرفی شرکت {name}: فعال در هوش مصنوعی.",
     f"ghorfe-{n:02d}.mp4")
    for n, name in enumerate(NAMES, start=1)
]

EXTRA = [
    ("faq-guide", "اطلاعات نمایشگاه",
     "درباره غرفه ها و ساعت کاری توضیح کامل در ورودی نمایشگاه موجود است.", ""),
]

LIST_QUESTION = "شرکت‌های هوش مصنوعی را معرفی کن"
NEUTRAL_QUERY = "درباره غرفه ها بگو"


def _seed(companies=COMPANIES, field="هوش مصنوعی"):
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for i, title, text, video in list(EXTRA) + list(companies):
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
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "resilience.db"))
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


def _mock_ai(monkeypatch, classified=None, generated="پاسخ تولیدشدهٔ AI"):
    """Stub the whole AI tail so no test in this file can reach a provider."""
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


_DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_FOLD.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})
_NUMBERED = re.compile(r"^\s*(\d+)\s*[.)\-–]\s*(\S.*?)\s*$")


def _numbered_lines(text):
    """The numbered names in a rendered answer, digit script folded away."""
    out = []
    for line in text.translate(_DIGIT_FOLD).splitlines():
        m = _NUMBERED.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


def _list(client):
    listed = _ask(client, LIST_QUESTION)
    assert listed.json()["source"] == "local_company_search", listed.text
    return listed.json()


# ══ DEFECT 1 — the pick tier may not crash on any input ═════════════════

# Every one of these passes `str.isdigit()`. None of them is something `int()`
# will parse, so the old `if bare.isdigit(): int(bare)` raised ValueError on
# each — outside any handler, so HTTP 500.
NOT_REALLY_A_NUMBER = ["²", "³", "¹", "٢²"]


@pytest.mark.parametrize("message", NOT_REALLY_A_NUMBER)
def test_a_superscript_after_a_list_does_not_crash_the_request(client, monkeypatch,
                                                               message):
    """VERIFIED CRASH: after any options list, sending «²» returned HTTP 500.

    `'²'.isdigit()` is True but `int('²')` raises ValueError. The visitor gets
    a broken screen at the booth for one stray character.
    """
    _seed()
    _mock_ai(monkeypatch)
    _list(client)

    r = _ask(client, message)
    assert r.status_code != 500, (message, r.text)
    assert r.status_code in (200, 503), (message, r.text)
    if r.status_code == 200:
        assert r.json()["source"] != "local_pick", r.json()


def test_a_very_long_digit_run_does_not_crash_the_request(client, monkeypatch):
    """CPython refuses to parse an integer string longer than 4300 digits.
    A paste, or a bored visitor holding a key down, is not a server error."""
    _seed()
    _mock_ai(monkeypatch)
    _list(client)

    r = _ask(client, "1" * 4400)
    assert r.status_code != 500, r.text
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert r.json()["source"] != "local_pick", r.json()


def test_the_pick_tier_returns_none_instead_of_raising_on_anything(client, monkeypatch):
    """The unit-level statement of the same rule: `resolve_pick` is total.

    Called straight, with the offer a real list turn stored, it must answer
    with an id or with None — never by raising — whatever the visitor typed.
    """
    _seed()
    _mock_ai(monkeypatch)
    from app.services.answer import resolve_pick

    offer = {"ids": [c[0] for c in COMPANIES], "shown": 5}
    for message in NOT_REALLY_A_NUMBER + ["1" * 4400, "٢" * 5000, "۳" * 4301]:
        assert resolve_pick(message, offer) is None, message


def test_a_persian_digit_still_resolves_after_the_fix(client, monkeypatch):
    """THE POSITIVE CONTROL. Tightening the digit test must not cost the
    visitor who reads «۳» off the screen and types «۳» back."""
    _seed()
    _mock_ai(monkeypatch)
    listed = _list(client)
    third = next(o for o in listed["options"] if o["n"] == 3)

    body = _ask(client, "۳").json()
    assert body["source"] == "local_pick", body
    assert body["video_url"] == third["video_url"], body

    body = _ask(client, "٣").json()      # Arabic-Indic three
    assert body["source"] == "local_pick", body
    assert body["video_url"] == third["video_url"], body


# ══ DEFECT 2 — a grounding failure is not a "none" verdict ══════════════

def _stub_provider(monkeypatch, content):
    from app.services.ai import wrapper
    from app.services.ai.request import AIResponse

    async def fake_generate(messages, **kw):
        return AIResponse(content=content, finish_reason="stop",
                          task=kw.get("task", "chat"), provider_type="stubprov",
                          model="stub-model", tokens_total=42, cost=0.001)

    monkeypatch.setattr(wrapper.padyar_ai, "generate", fake_generate)


def _force_tier2(monkeypatch):
    import app.routers.chat as chat
    monkeypatch.setattr(chat, "find_best_match", lambda q: (None, 0.0))
    monkeypatch.setattr(chat, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat, "classify_intent_local", lambda q: (None, 0.0))


def _fake_candidates(monkeypatch, ids, scores=None):
    import app.routers.chat as chat
    from app.services import search

    scores = scores or [0.60 - 0.05 * i for i in range(len(ids))]
    cands = [(search.dataset_lookup[i], s, {"lexical": s})
             for i, s in zip(ids, scores)]
    monkeypatch.setattr(chat, "find_top_matches", lambda query, k=8: cands)


def _decision(candidates, query=NEUTRAL_QUERY):
    import asyncio
    from app.services import answer
    return asyncio.run(answer.select_records(query, candidates, [], "fa"))


def _cands(*specs):
    from app.services import search
    return [{**search.dataset_lookup[i], "score": s} for i, s in specs]


def test_an_options_reply_naming_line_numbers_is_not_a_none_verdict(client, monkeypatch):
    """THE LIVE TRIGGER. Two shipped routes drop `response_format`: the sakoo
    adapter reports supports_json_object() == False, and the Anthropic adapter
    never reads the field. On either one a model answers
    {"mode":"options","ids":["1","2","3"]} — line numbers, not record ids.

    Every id is dropped by the grounding gate. That is a FAILURE to choose,
    not a considered "none of these records match", and the two must not
    arrive at the caller as the same value.
    """
    _seed()
    _stub_provider(monkeypatch, json.dumps({
        "mode": "options", "ids": ["1", "2", "3"], "lead": "", "reason": ""}))

    decision = _decision(_cands(("co-1", 0.50), ("co-2", 0.48)))
    assert decision is None, \
        "a decision that named nothing we proposed is no decision at all"


def test_a_grounding_failure_still_reaches_the_classifier(client, monkeypatch):
    """The cost of the mislabel, measured where the visitor feels it.

    mode "none" makes `chat.py` skip `classify_intent`. When the ids were
    merely rejected, nothing examined the records at all — so the classifier
    the ladder would otherwise have reached must still run.
    """
    _seed()
    _force_tier2(monkeypatch)
    _fake_candidates(monkeypatch, ["co-1", "co-2"])
    calls = {"classify": 0}

    import app.routers.chat as chat

    async def fake_classify(query):
        calls["classify"] += 1
        return None, 1, 0.0

    async def fake_generate(query, lang="fa"):
        return "پاسخ تولیدشدهٔ AI", 2, 0.0

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)
    _stub_provider(monkeypatch, json.dumps({
        "mode": "options", "ids": ["1", "2", "3"], "lead": "", "reason": ""}))

    r = _ask(client, NEUTRAL_QUERY)
    assert r.status_code == 200, r.text
    assert calls["classify"] == 1, \
        "a rejected id list must not skip the classifier the way a real 'none' does"


def test_a_grounding_failure_is_written_to_the_log(client, monkeypatch):
    """An operator has to be able to tell the two apart from the log explorer
    alone — a provider whose adapter drops response_format looks exactly like
    a corpus that has nothing to say."""
    _seed()
    _stub_provider(monkeypatch, json.dumps({
        "mode": "options", "ids": ["1", "2"], "lead": "", "reason": ""}))
    _decision(_cands(("co-1", 0.50), ("co-2", 0.48)))

    from app.services import applog
    conn = applog.get_logs_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM app_logs")]
    finally:
        conn.close()
    named = [r for r in rows if r["event_name"] == "selection.ids_rejected"]
    assert named, [r["event_name"] for r in rows]


def test_a_real_none_verdict_still_skips_the_classifier(client, monkeypatch):
    """THE POSITIVE CONTROL for the change above. A model that genuinely read
    the records and said "none of these" still saves the third provider call —
    that saving is the whole reason mode "none" exists."""
    _seed()
    _force_tier2(monkeypatch)
    _fake_candidates(monkeypatch, ["co-1", "co-2"])
    calls = {"classify": 0}

    import app.routers.chat as chat

    async def fake_classify(query):
        calls["classify"] += 1
        return None, 1, 0.0

    async def fake_generate(query, lang="fa"):
        return "پاسخ تولیدشدهٔ AI", 2, 0.0

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)
    _stub_provider(monkeypatch, json.dumps({
        "mode": "none", "ids": [], "lead": "", "reason": "nothing here fits"}))

    r = _ask(client, NEUTRAL_QUERY)
    assert r.status_code == 200, r.text
    assert calls["classify"] == 0, "a real 'none' still costs two calls, not three"


# ══ DEFECT 3 — an empty page is not a page ══════════════════════════════

def test_paging_after_the_rest_of_the_list_was_deleted_prints_no_empty_list(client, monkeypatch):
    """Staff bulk-edit the dataset WHILE visitors ask. The pager's guard
    counts the ids we STORED last turn, so it still passed after every one of
    them was deleted: the answer printed a zero count and then asked the
    visitor to choose one of nothing.
    """
    _seed()
    _mock_ai(monkeypatch)
    _list(client)

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("DELETE FROM dataset WHERE id LIKE 'co-%'")
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()

    more = _ask(client, "بیشتر")
    assert more.status_code in (200, 503), more.text
    if more.status_code == 200:
        body = more.json()
        assert body["options"] == [], body
        assert _numbered_lines(body["text"]) == [], body["text"]
        assert "کدام" not in body["text"], \
            "an empty page must not ask the visitor to choose"
        assert "0" not in body["text"] and "۰" not in body["text"], \
            "a zero count is not an answer"


def test_paging_when_only_the_names_already_shown_survive_prints_no_empty_list(client, monkeypatch):
    """The same hole, one step subtler: the first five still exist, everything
    after them is gone. The next page's slice is empty even though the list
    itself is not, so the count printed is real and the list under it is not.
    """
    _seed()
    _mock_ai(monkeypatch)
    listed = _list(client)
    shown_ids = {o["id"] for o in listed["options"]}

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    for cid, _t, _x, _v in COMPANIES:
        if cid not in shown_ids:
            conn.execute("DELETE FROM dataset WHERE id = ?", (cid,))
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()

    more = _ask(client, "بیشتر")
    assert more.status_code in (200, 503), more.text
    if more.status_code == 200:
        body = more.json()
        assert body["options"] == [], body
        assert _numbered_lines(body["text"]) == [], body["text"]
        assert "کدام" not in body["text"], \
            "an empty page must not ask the visitor to choose"


def test_paging_still_works_when_the_next_page_survives(client, monkeypatch):
    """THE POSITIVE CONTROL. The empty-page guard must not eat a real page."""
    _seed()
    _mock_ai(monkeypatch)
    _list(client)

    more = _ask(client, "بیشتر")
    assert more.status_code == 200, more.text
    body = more.json()
    assert [o["n"] for o in body["options"]] == [6, 7, 8], body["options"]
    assert [n for n, _t in _numbered_lines(body["text"])] == [6, 7, 8], body["text"]
    assert "کدام" in body["text"], body["text"]


# ══ DEFECT 3 × DEFECT 8 — the count and the list must agree ═════════════
#
# The empty-page guard above and the pager's whole-match headline (defect 8)
# landed as two separate fixes that meet in the same block. Between them sits
# the case neither one alone covers: SOME of the list is deleted, so the next
# page still prints a name and the guard lets it through, while the headline
# and the "and N more" tail still quote the count from before the deletion.

def test_paging_after_some_of_the_list_was_deleted_counts_only_what_survived(
        client, monkeypatch):
    """Staff delete two of the eight companies between the list and «بیشتر».

    Six records survive and the next page really does have one name on it, so
    the empty-page guard is not what protects the visitor here. The headline
    has to say six, and the tail must not promise two more names that no
    «بیشتر» can ever reach.

    The two are chosen from the names the visitor has NOT seen, read off the
    first page rather than hard-coded: the list is ranked, not in seed order,
    so «co-7» is not reliably on page two.
    """
    _seed()
    _mock_ai(monkeypatch)
    listed = _list(client)
    shown_ids = [o["id"] for o in listed["options"]]
    unshown = [cid for cid, _t, _x, _v in COMPANIES if cid not in shown_ids]
    assert len(unshown) == 3, unshown

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    for cid in unshown[:2]:
        conn.execute("DELETE FROM dataset WHERE id = ?", (cid,))
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()

    more = _ask(client, "بیشتر")
    assert more.status_code == 200, more.text
    body = more.json()

    # The page itself is real — this is not the empty-page case.
    assert [o["n"] for o in body["options"]] == [6], body["options"]
    assert [o["id"] for o in body["options"]] == [unshown[2]], body["options"]

    headline = body["text"].translate(_DIGIT_FOLD).splitlines()[0]
    assert re.match(r"^\s*6\b", headline), \
        f"the headline must count what survived, not what was stored: {headline!r}"
    assert "دیگر" not in body["text"], \
        "nothing is left to page to, so nothing may be promised: " + body["text"]


# ── A pick inside a sentence ─────────────────────────────────────────────
#
# Found by scripts/persona_probe.py on the live install, 2026-08-28. A list was
# offered, the visitor answered «دومی رو توضیح بده», and the model came back
# with "which one do you mean?". The ordinal rule required the message to be
# EXACTLY one token, so «دومی» resolved and «دومی رو توضیح بده» did not — and
# nobody answers a numbered list with a bare word.

def test_an_ordinal_inside_a_short_request_still_picks():
    from app.services.answer import resolve_pick
    offer = {"ids": ["co-1", "co-2", "co-3", "co-4", "co-5"], "shown": 5}
    for message, want in [
        ("دومی رو توضیح بده", "co-2"),
        ("اولی رو بیشتر بگو", "co-1"),
        ("سومی چیه؟", "co-3"),
        ("لطفا چهارمی را معرفی کن", "co-4"),
    ]:
        assert resolve_pick(message, offer) == want, message


def test_a_number_inside_a_short_request_still_picks():
    from app.services.answer import resolve_pick
    offer = {"ids": ["co-1", "co-2", "co-3", "co-4", "co-5"], "shown": 5}
    for message, want in [
        ("شماره ۳ چیکار میکنه؟", "co-3"),
        ("۲ رو بگو", "co-2"),
        ("مورد 5 را توضیح بده", "co-5"),
    ]:
        assert resolve_pick(message, offer) == want, message


def test_a_sentence_that_merely_contains_a_number_is_not_a_pick():
    """The guard the one-token rule was protecting, and it must survive.
    «سوم اسفند چه خبر است» opens with an ordinal and is a date question;
    «ساعت ۲ باز است؟» carries a number and is about opening time. Answering
    either with a company would be confidently wrong."""
    from app.services.answer import resolve_pick
    offer = {"ids": ["co-1", "co-2", "co-3", "co-4", "co-5"], "shown": 5}
    for message in ("سوم اسفند چه خبر است",
                    "ساعت ۲ باز است؟",
                    "۳ روز طول می کشد؟",
                    "دومین روز نمایشگاه چه خبر است"):
        assert resolve_pick(message, offer) is None, message
