"""Several options, numbered — then the visitor picks one.

THE PRODUCT OWNER'S NUMBER-ONE COMPLAINT: "we have ~200 companies in AI, the
bot always retrieves the FIRST option. Instead it should give several options
as a numbered list and then ask which one the visitor wants to know more
about."

Today the deterministic company-list tier answers a list question with up to
FIFTEEN bullet points and the sentence "ask about any company by name". A
visitor at a booth, on a touch screen, with no keyboard, cannot act on that.

WHAT THIS FILE PINS, in three parts:

  A. THE LIST RENDERING — same tier, same SELECTION (`_wants_company_list`,
     the JOIN, the strict `keywords <= hay` subset test are all untouched),
     new output: up to `options_shown` NUMBERED names, the applied filter words
     printed in the headline so a wrong SET is visible, a remainder line, and a
     closing "which one?". `options_shown` is the kill switch: typing 15 in the
     admin panel restores today's answer count with no deploy.

  B. THE PICK — a bare number, an ordinal word, or an offered title, resolved
     against the ids stored on the PREVIOUS turn. Zero network calls, so it
     works with the AI provider switched off, and it lands in the unchanged
     `_answer_from_entry`, so the company's booth video plays for free.

  C. THE PAGER — «بیشتر» prints the next page instead of losing the visitor
     who wanted the sixth name.

WHY IDS AND NOT TEXT are stored: re-parsing the rendered answer would break
the moment the wording changes and could never recover `video_url`.
"""
import json
import re

import pytest
from fastapi.testclient import TestClient


# ── The corpus ───────────────────────────────────────────────────────────
#
# 18 companies in one field (so the default cap of 5 and the old cap of 15 are
# both visible) plus two FAQ rows. Every company name is unique to its own
# title AND text, which is what makes it a distinctive entity token; the FAQ
# rows exist to give the negative tests («سوم اسفند چه خبر است») corpus
# vocabulary, so the unknown-entity guard cannot answer them by accident.

NAMES = ["آلفا", "بتا", "گاما", "دلتا", "اپسیلون", "زتا", "اتا", "تتا",
         "یوتا", "کاپا", "لامبدا", "سیگما", "امگا", "پارس", "آریا", "کاوه",
         "هخامنش", "البرز"]

COMPANIES = [
    (f"co-{n}", f"شرکت {name}", f"معرفی شرکت {name}: فعال در هوش مصنوعی.",
     f"ghorfe-{n:02d}.mp4")
    for n, name in enumerate(NAMES, start=1)
]

EXTRA = [
    ("faq-guide", "اطلاعات نمایشگاه",
     "درباره غرفه ها و ساعت کاری توضیح کامل در ورودی نمایشگاه موجود است.", ""),
    ("faq-news", "اخبار نمایشگاه",
     "برنامه روزهای اسفند و هر خبر تازه در ورودی نمایشگاه اعلام می شود.", ""),
]

LIST_QUESTION = "شرکت‌های هوش مصنوعی را معرفی کن"


def _seed(companies=COMPANIES, extra=EXTRA, field="هوش مصنوعی"):
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
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "options.db"))
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
    """Patch the AI tier the way chat.py imports it, and COUNT the calls.

    Counted rather than made fatal: chat.py wraps the whole Tier 2 block in
    `except Exception`, so raising inside the stub would be swallowed and the
    test would report a confusing fallback instead of "the AI was called".
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


def _assert_no_ai(calls):
    """Everything in parts A, B and C must work with the AI provider switched
    off — that is the property this whole design is arranged around."""
    assert calls == {"classify": 0, "generate": 0, "provider": 0}, calls


def _ask(client, message, lang="fa"):
    return client.post("/chat", json={"message": message, "lang": lang})


_DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_FOLD.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})
_NUMBERED = re.compile(r"^\s*(\d+)\s*[.)\-–]\s*(\S.*?)\s*$")


def _numbered_lines(text):
    """The numbered names in a rendered answer, digit script folded away.

    Persian digits are what read naturally in a Persian UI, so the renderer is
    free to use them; this helper reads either script so the tests pin the
    STRUCTURE (n -> title) and not the typography."""
    out = []
    for line in text.translate(_DIGIT_FOLD).splitlines():
        m = _NUMBERED.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


def _offer_state(conversation_row=-1):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT offer_state FROM chat_logs WHERE offer_state <> ''"
            " ORDER BY id ASC")]
    finally:
        conn.close()
    assert rows, "no offer was stored"
    return json.loads(rows[conversation_row]["offer_state"])


# ══ PART A — the list rendering ═════════════════════════════════════════

def test_a_long_list_shows_five_numbered_names_and_says_how_many_are_left(client, monkeypatch):
    """THE change the owner asked for. Fifteen bullets a visitor cannot act on
    become five numbered choices and an invitation to pick one."""
    _seed()
    ai = _mock_ai(monkeypatch)
    r = _ask(client, LIST_QUESTION)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body

    lines = _numbered_lines(body["text"])
    assert [n for n, _t in lines] == [1, 2, 3, 4, 5], body["text"]
    assert "18" in body["text"].translate(_DIGIT_FOLD), body["text"]
    assert "13" in body["text"].translate(_DIGIT_FOLD), \
        "the remainder must be stated, not silently dropped"
    assert "•" not in body["text"], "bullets are replaced by numbers"
    _assert_no_ai(ai)


def test_the_answer_ends_by_asking_which_one(client, monkeypatch):
    """A list with no question at the end is a dead end. The closing line is
    what turns the answer into a choice."""
    _seed()
    ai = _mock_ai(monkeypatch)
    body = _ask(client, LIST_QUESTION).json()
    tail = body["text"].rstrip().splitlines()[-1]
    assert "کدام" in tail, body["text"]
    assert "؟" in tail, body["text"]
    _assert_no_ai(ai)


def test_the_headline_names_the_filter_that_was_applied(client, monkeypatch):
    """«۶۹ شرکت در این زمینه» hides which zemine. Printing «هوش مصنوعی» makes
    a wrong SET visible to the visitor and correctable in one sentence,
    instead of silently confident."""
    _seed()
    ai = _mock_ai(monkeypatch)
    body = _ask(client, LIST_QUESTION).json()
    head = body["text"].splitlines()[0]
    assert "هوش" in head and "مصنوعی" in head, head
    _assert_no_ai(ai)


def test_setting_options_shown_to_fifteen_restores_the_old_answer_length(client, monkeypatch):
    """THE KILL SWITCH, and the reason it lives ABOVE the AI gate: the
    rendering change ships whether or not a provider is configured, so without
    a settings value an operator would have no way to revert it except a
    deploy. Fifteen was the cap this tier shipped with on 2026-08-27."""
    _seed()
    from app.db.queries import set_setting
    set_setting("options_shown", "15")
    ai = _mock_ai(monkeypatch)
    body = _ask(client, LIST_QUESTION).json()
    assert len(_numbered_lines(body["text"])) == 15, body["text"]
    assert len(body["options"]) == 15, body["options"]
    _assert_no_ai(ai)


def test_the_list_turn_offers_tappable_options_carrying_id_title_and_video(client, monkeypatch):
    """At a booth this is the difference between one tap and a visitor hunting
    for the on-screen keyboard. `video_url` rides along so a title and its clip
    can never drift apart, even though the chip click round-trips through /chat
    today."""
    _seed()
    ai = _mock_ai(monkeypatch)
    body = _ask(client, LIST_QUESTION).json()

    options = body["options"]
    assert len(options) == 5, options
    assert [o["n"] for o in options] == [1, 2, 3, 4, 5], options
    for o in options:
        assert o["id"].startswith("co-"), o
        assert o["title"].startswith("شرکت "), o
        assert o["video_url"] and o["video_url"].endswith(".mp4"), o
    _assert_no_ai(ai)


def test_the_printed_list_and_the_stored_offer_can_never_disagree(client, monkeypatch):
    """One function renders the slice AND produces `offer_state`, so the count
    printed and the count stored come from the same place. The base design
    wrote offer state from three separate call sites; that is where a "3" that
    resolves to the wrong company comes from."""
    _seed()
    ai = _mock_ai(monkeypatch)
    body = _ask(client, LIST_QUESTION).json()

    offer = _offer_state()
    lines = _numbered_lines(body["text"])
    assert offer["shown"] == len(lines) == len(body["options"]), (offer, lines)
    assert offer["ids"][:offer["shown"]] == [o["id"] for o in body["options"]]
    # The full matched set is kept for paging, capped so the column stays small.
    assert len(offer["ids"]) == 18, offer
    _assert_no_ai(ai)


def test_the_whole_list_turn_works_with_the_ai_provider_switched_off(client, monkeypatch):
    """The property the design is arranged around: with AI off the visitor
    still gets a numbered list and can still choose from it."""
    _seed()
    from app.db.queries import set_setting
    set_setting("openai_enabled", "false")
    ai = _mock_ai(monkeypatch)

    body = _ask(client, LIST_QUESTION).json()
    assert body["source"] == "local_company_search", body
    assert len(_numbered_lines(body["text"])) == 5, body["text"]

    picked = _ask(client, "2").json()
    assert picked["source"] == "local_pick", picked
    assert picked["type"] == "video", picked
    _assert_no_ai(ai)


# ── The SELECTION half of the tier is untouched ─────────────────────────

def test_a_topic_no_company_matches_still_declines_the_tier(client, monkeypatch):
    """REGRESSION over existing behaviour. Listing zero companies would be a
    confident non-answer, so the tier returns None and the AI tier judges."""
    _seed()
    _mock_ai(monkeypatch)
    body = _ask(client, "شرکت‌های زیست فناوری را معرفی کن").json()
    assert body["source"] != "local_company_search", body
    assert body["options"] == [], body


def test_the_all_keywords_subset_rule_is_unchanged(client, monkeypatch):
    """REGRESSION with one new assertion. The strict subset test — every topic
    keyword must be present — is what keeps «هوش مصنوعی» from listing every
    company that merely says «هوش» somewhere. The keywords are now RETURNED so
    the renderer can print them."""
    _seed()
    from app.services.company_search import answer_company_list
    res = answer_company_list(LIST_QUESTION)
    assert res is not None
    assert res["count"] == 18, res
    assert set(res["keywords"]) == {"هوش", "مصنوعی"}, res
    assert len(res["displayed_ids"]) == 5, res
    assert len(res["matched_ids"]) == 18, res


def test_a_query_naming_one_company_is_still_not_turned_into_a_list(client, monkeypatch):
    """REGRESSION over existing behaviour. «شرکت آلفا چیست؟» names ONE company
    — the answer is that company's own entry, never a list."""
    _seed()
    ai = _mock_ai(monkeypatch)
    body = _ask(client, "شرکت آلفا چیست؟").json()
    assert body["source"] != "local_company_search", body
    assert body["options"] == [], body


# ══ PART B — the pick ═══════════════════════════════════════════════════

def _list_then(client, message):
    listed = _ask(client, LIST_QUESTION)
    assert listed.json()["source"] == "local_company_search", listed.text
    return listed.json(), _ask(client, message)


def test_typing_three_serves_the_third_offered_company_with_its_booth_video(client, monkeypatch):
    """The whole point of the feature. The visitor picks by number, the record
    is looked up by the id we stored, and because the answer goes through the
    unchanged `_answer_from_entry` that company's ghorfe clip plays."""
    _seed()
    ai = _mock_ai(monkeypatch)
    listed, picked = _list_then(client, "3")

    assert picked.status_code == 200, picked.text
    body = picked.json()
    third = next(o for o in listed["options"] if o["n"] == 3)
    assert body["source"] == "local_pick", body
    assert body["type"] == "video", body
    assert body["video_url"] == third["video_url"], (body, third)
    assert third["title"].split()[-1] in body["text"], (body["text"], third)
    _assert_no_ai(ai)


def test_a_persian_digit_resolves_exactly_like_an_ascii_one(client, monkeypatch):
    """The list is printed in Persian digits because that reads naturally, and
    a visitor on a laptop keyboard types "3". Input is folded, output is not."""
    _seed()
    ai = _mock_ai(monkeypatch)
    listed, picked = _list_then(client, "۳")
    body = picked.json()
    third = next(o for o in listed["options"] if o["n"] == 3)
    assert body["source"] == "local_pick", body
    assert body["video_url"] == third["video_url"], body
    _assert_no_ai(ai)


def test_a_number_with_a_trailing_dot_still_resolves(client, monkeypatch):
    """People copy the line they are answering. "2." is a pick, not a typo."""
    _seed()
    ai = _mock_ai(monkeypatch)
    listed, picked = _list_then(client, "2.")
    body = picked.json()
    second = next(o for o in listed["options"] if o["n"] == 2)
    assert body["source"] == "local_pick", body
    assert body["video_url"] == second["video_url"], body
    _assert_no_ai(ai)


def test_an_ordinal_word_resolves_the_same_way_as_the_number(client, monkeypatch):
    """Plenty of visitors will write «دومی» rather than «2». A fixed, short
    word list keeps this deterministic."""
    _seed()
    ai = _mock_ai(monkeypatch)
    listed, picked = _list_then(client, "دومی")
    body = picked.json()
    second = next(o for o in listed["options"] if o["n"] == 2)
    assert body["source"] == "local_pick", body
    assert body["video_url"] == second["video_url"], body
    _assert_no_ai(ai)


def test_sending_the_exact_offered_title_resolves_to_that_record(client, monkeypatch):
    """This is what a chip tap sends: `sendPreset(option.title)`. A tap and a
    typed number converge on the same next-turn resolution, so no new endpoint
    is needed."""
    _seed()
    ai = _mock_ai(monkeypatch)
    listed = _ask(client, LIST_QUESTION).json()
    fourth = next(o for o in listed["options"] if o["n"] == 4)

    body = _ask(client, fourth["title"]).json()
    assert body["source"] == "local_pick", body
    assert body["video_url"] == fourth["video_url"], body
    _assert_no_ai(ai)


def test_a_bare_number_with_no_offer_behind_it_does_not_resolve(client, monkeypatch):
    """Nothing was offered, so "3" is just a message. It must go through the
    normal pipeline rather than resolving against whatever happens to be in
    the index.

    The second half is the POSITIVE CONTROL: the same "3" after a list DOES
    resolve. Without it this test would pass on an install that has no pick
    tier at all, which is exactly the state it is meant to detect."""
    _seed()
    _mock_ai(monkeypatch)
    r = _ask(client, "3")
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert r.json()["source"] != "local_pick", r.json()

    listed, picked = _list_then(client, "3")
    assert picked.json()["source"] == "local_pick", picked.json()


def test_a_number_higher_than_what_was_shown_does_not_resolve(client, monkeypatch):
    """A pick resolves against `ids[0:shown]` only. Five names were printed, so
    "6" names nothing the visitor ever saw — resolving it would serve a company
    chosen by arithmetic.

    "5" is the POSITIVE CONTROL: the boundary itself must work, or this test
    would pass on an install with no pick tier at all."""
    _seed()
    _mock_ai(monkeypatch)
    listed, at_boundary = _list_then(client, "5")
    assert at_boundary.json()["source"] == "local_pick", at_boundary.json()

    picked = _ask(client, "6")
    assert picked.status_code in (200, 503), picked.text
    if picked.status_code == 200:
        assert picked.json()["source"] != "local_pick", picked.json()


def test_an_offer_older_than_the_window_does_not_resolve(client, monkeypatch):
    """A booth kiosk is one browser and one cookie shared by many people. A
    bare "3" typed twenty minutes after a stranger's list must not land on
    that stranger's third company."""
    _seed()
    _mock_ai(monkeypatch)
    listed, fresh = _list_then(client, "3")
    # POSITIVE CONTROL: inside the window the very same pick resolves.
    assert fresh.json()["source"] == "local_pick", fresh.json()

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE chat_logs SET created_at = datetime('now','-20 minutes')")
    conn.commit()
    conn.close()

    stale = _ask(client, "3")
    assert stale.status_code in (200, 503), stale.text
    if stale.status_code == 200:
        assert stale.json()["source"] != "local_pick", stale.json()


def test_an_ordinal_inside_a_real_question_does_not_resolve_as_a_pick(client, monkeypatch):
    """«سوم اسفند چه خبر است» starts with an ordinal word and is not a pick. A
    loose ordinal rule would answer a date question with whichever company
    happened to be third.

    The bare «سوم» is the POSITIVE CONTROL: the ordinal itself must resolve,
    or this test would pass on an install with no pick tier at all."""
    _seed()
    _mock_ai(monkeypatch)
    listed, bare = _list_then(client, "سوم")
    assert bare.json()["source"] == "local_pick", bare.json()

    sentence = _ask(client, "سوم اسفند چه خبر است")
    assert sentence.status_code in (200, 503), sentence.text
    if sentence.status_code == 200:
        assert sentence.json()["source"] != "local_pick", sentence.json()


def test_a_pick_keeps_the_offer_alive_for_the_next_pick(client, monkeypatch):
    """Visitors compare. Re-storing the SAME offer on the pick turn means a
    following "4" still works, and it restarts the freshness clock so a slow
    reader is not timed out mid-comparison."""
    _seed()
    ai = _mock_ai(monkeypatch)
    listed, first_pick = _list_then(client, "3")
    assert first_pick.json()["source"] == "local_pick", first_pick.json()

    second_pick = _ask(client, "4")
    body = second_pick.json()
    fourth = next(o for o in listed["options"] if o["n"] == 4)
    assert body["source"] == "local_pick", body
    assert body["video_url"] == fourth["video_url"], body


def test_a_pick_whose_record_was_deleted_between_turns_falls_through_quietly(client, monkeypatch):
    """Staff correct content WHILE visitors ask — that is why `_maybe_refresh`
    and INDEX_VERSION_KEY exist at all. An id that no longer resolves must fall
    through to normal retrieval, not raise and not serve a stale dict."""
    _seed()
    _mock_ai(monkeypatch)
    listed = _ask(client, LIST_QUESTION).json()
    third = next(o for o in listed["options"] if o["n"] == 3)

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("DELETE FROM dataset WHERE id = ?", (third["id"],))
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()

    picked = _ask(client, "3")
    assert picked.status_code in (200, 503), picked.text
    if picked.status_code == 200:
        assert picked.json()["source"] != "local_pick", picked.json()


# ══ PART C — the pager ══════════════════════════════════════════════════

def test_asking_for_more_prints_the_next_five_and_keeps_them_pickable(client, monkeypatch):
    """Without this, capping at five is a straight loss for the visitor who
    wanted the sixth name. The numbering continues rather than restarting, so
    "7" means the same company on both turns."""
    _seed()
    ai = _mock_ai(monkeypatch)
    listed = _ask(client, LIST_QUESTION).json()

    more = _ask(client, "بیشتر")
    assert more.status_code == 200, more.text
    body = more.json()
    assert [n for n, _t in _numbered_lines(body["text"])] == [6, 7, 8, 9, 10], \
        body["text"]
    assert [o["n"] for o in body["options"]] == [6, 7, 8, 9, 10], body["options"]
    assert body["video_url"] is None, "a paging turn offers, it does not answer"

    offer = _offer_state()
    assert offer["shown"] == 10, offer
    assert offer["ids"][:5] == [o["id"] for o in listed["options"]], offer

    seventh = next(o for o in body["options"] if o["n"] == 7)
    picked = _ask(client, "7").json()
    assert picked["source"] == "local_pick", picked
    assert picked["video_url"] == seventh["video_url"], picked


def test_asking_for_more_with_nothing_left_does_not_page(client, monkeypatch):
    """A short list has no next page. «بیشتر» must not produce an empty list
    or repeat the same five."""
    _seed(companies=COMPANIES[:3])
    _mock_ai(monkeypatch)
    listed = _ask(client, LIST_QUESTION).json()
    assert len(listed["options"]) == 3, listed

    more = _ask(client, "بیشتر")
    assert more.status_code in (200, 503), more.text
    if more.status_code == 200:
        assert more.json()["options"] == [], more.json()
        assert _numbered_lines(more.json()["text"]) == [], more.json()["text"]


def test_deleting_a_name_from_page_one_does_not_step_over_the_next_company(
        client, monkeypatch):
    """WHAT WAS BROKEN (measured 2026-08-28): exactly one company vanished from
    the whole list, silently, whenever staff deleted a record that was already
    on screen.

    The pager resolves the offered ids back into records and drops the ones an
    admin deleted mid-conversation (a gap in the numbering would be worse than
    a shorter list). But it then sliced that COMPACTED list at the ABSOLUTE
    position `shown + 1`. Compacting shifts everything after the hole one place
    to the left, so slicing at the old position steps over the first name of
    page 2. It was printed on no page, and nothing said so.

    The next page starts after whatever is LEFT of what the visitor actually
    saw, so the prefix and the tail are resolved separately and the start index
    is `len(prefix) + 1`.

    Staff correct content WHILE visitors ask. That is the whole reason
    `_maybe_refresh` and INDEX_VERSION_KEY exist, so this is a live case, not
    a contrived one."""
    _seed()
    ai = _mock_ai(monkeypatch)

    listed = _ask(client, LIST_QUESTION).json()
    matched = _offer_state()["ids"]
    assert len(matched) == 18, matched

    from app.services import search
    third = next(o for o in listed["options"] if o["n"] == 3)
    # The one that was going to open page 2, and the one the old slice lost.
    sixth_title = search.get_entry(matched[5])["title"]

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("DELETE FROM dataset WHERE id = ?", (third["id"],))
    conn.commit()
    conn.close()
    search.load_dataset_internal()

    more = _ask(client, "بیشتر")
    assert more.status_code == 200, more.text
    page_two = [t for _n, t in _numbered_lines(more.json()["text"])]
    assert sixth_title in page_two, (sixth_title, page_two)
    assert third["title"] not in page_two, page_two

    # ...and nothing else was stepped over either. Page through to the end and
    # account for every surviving company exactly once, because a start index
    # off by one loses a DIFFERENT name on every page.
    seen = [t for _n, t in _numbered_lines(listed["text"])] + page_two
    for _page in range(2):
        body = _ask(client, "بیشتر").json()
        seen += [t for _n, t in _numbered_lines(body["text"])]

    expected = [search.get_entry(i)["title"] for i in matched
                if i != third["id"]]
    assert sorted(seen) == sorted(expected + [third["title"]]), \
        sorted(set(expected) - set(seen))
    _assert_no_ai(ai)


# A match set bigger than OFFER_IDS_MAX (50). The exhibition really does have
# ~169 companies in one field, so this is the normal case, not an edge case.
MANY = [
    (f"co-{n}", f"شرکت واحد {n}",
     f"معرفی شرکت واحد {n}: فعال در هوش مصنوعی.", f"ghorfe-{n:02d}.mp4")
    for n in range(1, 71)
]


def test_paging_keeps_the_true_count_the_filter_words_and_reaches_the_last_match(
        client, monkeypatch):
    """Measured 2026-08-28 with 70 AI companies seeded: page 1 said «۷۰ شرکت
    در زمینه «هوش مصنوعی»», and every «بیشتر» after it said «۵۰ شرکت» — a
    wrong count, the filter words gone from the headline, and companies 51..70
    unreachable for good.

    All three came from the same line: the pager rebuilt its page out of the
    stored ids, which `offer_state` caps at OFFER_IDS_MAX so one 169-company
    match cannot write a kilobyte into a chat_logs row. The cap stays; the
    page is re-derived from the query that produced the list, so it costs the
    visitor nothing.

    `options_shown` is set to 15 only to keep this test to five requests —
    seventy names at five a page is fourteen turns and trips the rate limit.
    """
    _seed(companies=MANY)
    from app.db.queries import set_setting
    set_setting("options_shown", "15")
    ai = _mock_ai(monkeypatch)

    first = _ask(client, LIST_QUESTION).json()
    assert first["source"] == "local_company_search", first
    head = first["text"].splitlines()[0].translate(_DIGIT_FOLD)
    assert "70" in head, head
    assert "هوش" in head and "مصنوعی" in head, head

    seen = [n for n, _t in _numbered_lines(first["text"])]
    for page in range(4):
        more = _ask(client, "بیشتر")
        assert more.status_code == 200, more.text
        body = more.json()
        page_head = body["text"].splitlines()[0].translate(_DIGIT_FOLD)
        assert "70" in page_head, (page, page_head)
        assert "هوش" in page_head and "مصنوعی" in page_head, (page, page_head)
        seen += [n for n, _t in _numbered_lines(body["text"])]

    assert seen == list(range(1, 71)), seen
    _assert_no_ai(ai)


def test_the_company_after_the_id_cap_can_still_be_picked(client, monkeypatch):
    """Reaching name 51 in the list is only half the fix — the visitor has to
    be able to choose it. A pick resolves against the ids stored last turn, so
    the stored list must always cover what was actually printed."""
    _seed(companies=MANY)
    from app.db.queries import set_setting
    set_setting("options_shown", "15")
    ai = _mock_ai(monkeypatch)

    _ask(client, LIST_QUESTION)             # 1..15
    _ask(client, "بیشتر")                   # 16..30
    _ask(client, "بیشتر")                   # 31..45
    page = _ask(client, "بیشتر").json()     # 46..60 — crosses the id cap
    fifty_first = next(o for o in page["options"] if o["n"] == 51)

    picked = _ask(client, "51").json()
    assert picked["source"] == "local_pick", picked
    assert picked["type"] == "video", picked
    assert picked["video_url"] == fifty_first["video_url"], (picked, fifty_first)
    _assert_no_ai(ai)


# ══ The response model ══════════════════════════════════════════════════

def test_the_options_field_is_additive_so_no_existing_response_changes(client):
    """`options` defaults to an empty list, so every answer the chatbot gives
    today keeps its exact shape and no existing test has to change."""
    from app.models import ChatResponse, ChatOption

    plain = ChatResponse(type="text", text="سلام", confidence=0.9, source="local")
    assert plain.options == [], plain

    withopts = ChatResponse(
        type="text", text="کدام یک؟", confidence=0.9, source="ai_options",
        options=[ChatOption(n=1, id="co-1", title="شرکت آلفا",
                            video_url="ghorfe-01.mp4"),
                 ChatOption(n=2, id="co-2", title="شرکت بتا")])
    assert withopts.options[0].n == 1
    assert withopts.options[1].video_url is None
    assert json.loads(withopts.model_dump_json())["options"][0]["id"] == "co-1"
