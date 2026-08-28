"""Two firewalls over model-written text, both applied AFTER the model spoke.

WHY THIS FILE EXISTS. The selection tier lets the model write exactly one
string a visitor ever reads: the single `lead` sentence above a numbered list.
And `app/services/openai.py::get_openai_response` already returns `resp.content`
straight into `ChatResponse.text` with ZERO checks of any kind — the largest
live fabrication hole in the product today.

FIREWALL 1 — `answer.frame_is_grounded(text, body, question, lang)`, six
checks over the lead, all of which must pass:
    A length   — <= LEAD_MAX_CHARS
    B digits   — ANY digit at all, after folding Persian/Arabic-Indic to ASCII
    C shapes   — "@", "http", "www."
    D counts   — a number spelled out in words, banned outright like a digit
    E vocabulary — every content token must already appear in the visitor's
                   question, in the FRAME we wrote around the list, or in
                   FRAME_VOCAB (connector/courtesy words only)
    F names    — no token belonging to a listed record's name
Check E is the one that catches most. A digit filter alone passes «ورود رایگان
است» (a price with no digit) and «شرکت آلفا بهترین گزینه است» (a superlative
naming a real exhibitor, which an organizer legally cannot say). But E can only
ask WHERE a word came from, and our own closing question hands «یک» over for
free, which is why D is a separate, absolute ban.

FIREWALL 2 — `answer.generated_prose_is_grounded(text, lang)` over the free
prose. Only checks B and C run, and the source set is deliberately
`assistant_knowledge + assistant_phone + assistant_website` — the VISITOR'S OWN
MESSAGE IS EXCLUDED. Including it would turn the verifier into a laundering
channel: «نمایشگاه ۱۵ اسفند برگزار می‌شود؟» would license the answer
«بله، نمایشگاه ۱۵ اسفند برگزار می‌شود».

A rejection never costs the visitor an answer: the lead is replaced by the
deterministic template head, and the prose is replaced by the configured
refusal text at HTTP 200.
"""
import json

import pytest
from fastapi.testclient import TestClient


# ── The corpus ───────────────────────────────────────────────────────────

FAQ_TEXT = "درباره غرفه ها و ساعت کاری توضیح کامل در ورودی نمایشگاه موجود است."

DATASET = [
    ("faq-guide", "اطلاعات نمایشگاه", FAQ_TEXT, ""),
    ("co-alfa", "شرکت آلفا", "معرفی شرکت آلفا: فعال در هوش مصنوعی.", "ghorfe-01.mp4"),
    ("co-beta", "شرکت بتا", "شرکت بتا سامانه های هوش مصنوعی می سازد.", "ghorfe-02.mp4"),
    ("co-gama", "شرکت گاما", "شرکت گاما در زمینه هوش مصنوعی کار می کند.", "ghorfe-03.mp4"),
]

LIST_QUESTION = "شرکت‌های هوش مصنوعی را معرفی کن"

# The assistant's own recorded facts — the ONLY source the prose verifier
# accepts a number or a link from.
KNOWLEDGE = "نمایشگاه امسال در روز 20 مرداد گشایش می یابد."
PHONE = "021-12345678"
WEBSITE = "https://inotex.example.ir"


def _seed(rows=DATASET, with_profiles=True):
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

    if with_profiles:
        from app.services import leads
        leads.ensure_tables()
        conn = dbc.get_db_connection()
        for i, _t, _x, _v in rows:
            if not i.startswith("co-"):
                continue
            conn.execute(
                "INSERT INTO company_profiles (dataset_id, activity_field,"
                " created_at, updated_at)"
                " VALUES (?, 'هوش مصنوعی', '2026-08-28', '2026-08-28')", (i,))
        conn.commit()
        conn.close()

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "firewall.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        set_setting("search_backend", "tfidf")
        set_setting("assistant_knowledge", KNOWLEDGE)
        set_setting("assistant_phone", PHONE)
        set_setting("assistant_website", WEBSITE)

        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _ask(client, message, lang="fa"):
    return client.post("/chat", json={"message": message, "lang": lang})


def _log_rows(event=None):
    from app.services import applog
    conn = applog.get_logs_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM app_logs")]
    finally:
        conn.close()
    return [r for r in rows if event is None or r["event_name"] == event]


def _entries(*ids):
    from app.services import search
    return [search.dataset_lookup[i] for i in ids]


def _render(entries, lead, lang="fa", start_index=1, total=None, filter_label=""):
    from app.services import answer
    return answer.render_options(entries, lead, lang, start_index,
                                 total if total is not None else len(entries),
                                 filter_label)


def _stub_ai_tail(monkeypatch, classified=None, generated="پاسخ تولیدشدهٔ AI"):
    """The Tier 2 tail. `generated` is what the model "wrote" — the string the
    prose verifier has to judge."""
    import app.routers.chat as chat
    seen = {"classify": 0, "generate": 0}

    async def fake_classify(query):
        seen["classify"] += 1
        return classified, 1, 0.0

    async def fake_generate(query, lang="fa"):
        seen["generate"] += 1
        return generated, 2, 0.0

    monkeypatch.setattr(chat, "classify_intent", fake_classify)
    monkeypatch.setattr(chat, "get_openai_response", fake_generate)
    return seen


def _stub_selection_off(monkeypatch):
    """Make the selection tier return None so the turn reaches the free-prose
    path — the code under test in the second half of this file."""
    from app.services.ai import wrapper
    from app.services.ai.errors import AIError

    async def fake_generate(messages, **kw):
        raise AIError(code="provider_unavailable")

    monkeypatch.setattr(wrapper.padyar_ai, "generate", fake_generate)


def _force_tier2(monkeypatch):
    import app.routers.chat as chat
    monkeypatch.setattr(chat, "find_best_match", lambda q: (None, 0.0))
    monkeypatch.setattr(chat, "find_similar_question",
                        lambda q, exact_only=False: (None, 0.0))
    monkeypatch.setattr(chat, "classify_intent_local", lambda q: (None, 0.0))


# ── Check A: length ──────────────────────────────────────────────────────

def test_a_lead_longer_than_the_cap_is_rejected(client):
    """The lead sits above a numbered list. A paragraph there buries the list
    the visitor actually has to read."""
    _seed()
    from app.services import answer
    from app.config import LEAD_MAX_CHARS

    body = "1. شرکت آلفا\n2. شرکت بتا"
    long_lead = "شرکت " * (LEAD_MAX_CHARS // 5 + 10)
    ok, reason = answer.frame_is_grounded(long_lead, body, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "length", reason


# ── Check B: digits, in either script ────────────────────────────────────

def test_a_lead_containing_an_ascii_digit_is_rejected(client):
    """The only number under a lead is the count WE computed in Python. A
    number the model wrote is a number nobody verified, so the filter is
    absolute rather than an allowlist."""
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(
        "3 شرکت در این زمینه هستند", "1. شرکت آلفا", LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "digit", reason


def test_a_lead_containing_a_persian_digit_is_rejected_the_same_way(client):
    """«۳» is a digit to a visitor and to a checker that folds first. A filter
    that only knew ASCII would pass every fabricated number a Persian model
    writes."""
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(
        "۳ شرکت در این زمینه هستند", "1. شرکت آلفا", LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "digit", reason


def test_folding_digits_maps_both_persian_and_arabic_indic_to_ascii(client):
    """One helper, used by the digit check and by the pick tier, so a visitor
    who sees «۳» and types "3" on a laptop keyboard is understood."""
    _seed()
    from app.services import answer
    assert answer.fold_digits("۳") == "3"
    assert answer.fold_digits("٣") == "3"
    assert answer.fold_digits("۱۲۳") == "123"
    assert answer.fold_digits("بدون عدد") == "بدون عدد"


# ── Check C: contact shapes ──────────────────────────────────────────────

@pytest.mark.parametrize("lead", [
    "به info@example.com ایمیل بزنید",
    "به http://example.com سر بزنید",
    "به www.example.com سر بزنید",
])
def test_a_lead_carrying_a_contact_shape_is_rejected(client, lead):
    """An address or a link in a framing sentence is a fact, and no framing
    sentence has a legitimate reason to carry one."""
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(lead, "1. شرکت آلفا", LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "shape", reason


# ── Check E: the vocabulary firewall — the one that catches most ─────────

def test_a_spelled_out_count_is_rejected_although_it_holds_no_digit(client):
    """«هفت شرکت در این زمینه فعالیت می‌کنند» above a list whose real count is
    69. No digit filter can see this.

    It now fails at check D (counts are banned outright) rather than at the
    vocabulary check. Both verdicts are a rejection and the visitor sees the
    same thing; the reason letter is asserted because it is what the operator
    reads in the log when tuning FRAME_VOCAB, and «هفت» is not a vocabulary
    problem to solve."""
    _seed()
    from app.services import answer
    body = "3 شرکت در زمینه «هوش مصنوعی»:\n1. شرکت آلفا\n2. شرکت بتا\n3. شرکت گاما"
    ok, reason = answer.frame_is_grounded(
        "هفت شرکت در این زمینه فعالیت می‌کنند", body, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "count", reason


def test_a_price_claim_with_no_digit_is_rejected(client):
    """«ورود به نمایشگاه رایگان است» is a commercial claim the organizer never
    made. «رایگان» appears neither in the visitor's question nor in the list
    below it, so it cannot be framing — it is new information."""
    _seed()
    from app.services import answer
    body = "3 شرکت:\n1. شرکت آلفا\n2. شرکت بتا\n3. شرکت گاما"
    ok, reason = answer.frame_is_grounded(
        "ورود به نمایشگاه رایگان است", body, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "vocab", reason


def test_a_superlative_naming_a_real_exhibitor_is_rejected(client):
    """«شرکت آلفا بهترین گزینه است» names a company that IS in the list, so
    every proper noun in it is grounded — and it is still a ranking claim an
    exhibition organizer legally cannot make. «بهترین» is the token that must
    fail, which is why FRAME_VOCAB carries no evaluative word."""
    _seed()
    from app.services import answer
    body = "3 شرکت:\n1. شرکت آلفا\n2. شرکت بتا\n3. شرکت گاما"
    ok, reason = answer.frame_is_grounded(
        "شرکت آلفا بهترین گزینه است", body, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "vocab", reason


def test_a_lead_naming_a_company_that_is_not_in_the_list_is_rejected(client):
    """A company name the model produced from memory is the worst case of all:
    it reads exactly like a recommendation, and the company may not even be at
    the exhibition."""
    _seed()
    from app.services import answer
    body = "2 شرکت:\n1. شرکت آلفا\n2. شرکت بتا"
    ok, reason = answer.frame_is_grounded(
        "شرکت زتا هم گزینه خوبی است", body, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "vocab", reason


def test_a_lead_built_only_from_the_visitors_own_words_is_accepted(client):
    """The firewall must not reject everything, or the feature is dead weight.
    A sentence assembled entirely from words the visitor typed adds no fact and
    is exactly what a useful lead looks like."""
    _seed()
    from app.services import answer
    body = "3 شرکت:\n1. شرکت آلفا\n2. شرکت بتا\n3. شرکت گاما"
    ok, reason = answer.frame_is_grounded(
        "شرکت های هوش مصنوعی", body, LIST_QUESTION, "fa")
    assert ok is True, reason


# ── Check D: a count spelled out in words ────────────────────────────────
#
# WHAT WAS BROKEN (measured 2026-08-28). Check E can only ask WHERE a word came
# from, and the frame it reads is our own writing, including the closing
# question «کدام‌یک را می‌خواهید؟», which normalize_persian folds at the ZWNJ
# into «کدام» + «یک». So «یک» was a legal frame token on every single list, and
# «یک شرکت پیدا کردم:» ("I found ONE company" over a list of three) passed
# length, digits, shapes, vocabulary and names.
#
# It could not be caught downstream either: an accepted lead REPLACES the
# true-count headline the renderer computed, so nothing left on the screen
# contradicted it. The visitor read "I found one company" above three names.
#
# The English side has the same hole from the same source: our closing question
# is "Which one would you like to know more about?".
#
# A lead INTRODUCES the list. The list does the counting. So a number in words
# is banned outright, exactly like a digit is.

LIST_BODY_FA = ("3 شرکت در زمینه «هوش مصنوعی»:\n1. شرکت آلفا\n2. شرکت بتا\n"
                "3. شرکت گاما\n"
                "کدام‌یک را می‌خواهید بیشتر بشناسید؟ شماره‌اش را بنویسید یا اسمش را بزنید.")

LIST_BODY_EN = ("3 companies:\n1. Alpha Co\n2. Beta Co\n3. Gama Co\n"
                "Which one would you like to know more about? "
                "Send its number or its name.")

LIST_QUESTION_EN = "list the ai companies"


def test_the_word_one_cannot_ride_in_on_our_own_closing_question(client):
    """THE leak, at full strength. Every token of «یک شرکت پیدا کردم:» is
    grounded: «یک» comes from the closing question WE print, «شرکت» from the
    headline WE print, «پیدا» and «کردم» are connector words in FRAME_VOCAB.
    Five of the six checks pass it. Above three companies it says there is
    one."""
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(
        "یک شرکت پیدا کردم:", LIST_BODY_FA, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "count", reason


def test_a_fabricated_count_in_words_is_rejected_as_a_count(client):
    """«هفت» has nowhere to come from, so check E would have caught it too.
    It is pinned here as well because a future FRAME_VOCAB entry, or a visitor
    who happens to type the word, would hand it a source. A count must stay
    banned no matter where its word came from."""
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(
        "هفت شرکت پیدا کردم:", LIST_BODY_FA, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "count", reason


def test_a_vague_count_in_words_is_rejected_too(client):
    """«چندین» is not a number, it is a quantity claim, and it is wrong in the
    same way for the same reason: the list right below already says how many."""
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(
        "چندین شرکت پیدا کردم:", LIST_BODY_FA, LIST_QUESTION, "fa")
    assert ok is False
    assert reason == "count", reason


def test_the_english_side_leaks_the_same_word_from_the_same_place(client):
    """"I found one option:". The word "one" comes from our own "Which one
    would you like…", and "found" and "option" are FRAME_VOCAB connectors. Nothing else in
    the firewall can see it. An English install must not be the one that ships
    the fabricated count."""
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(
        "I found one option:", LIST_BODY_EN, LIST_QUESTION_EN, "en")
    assert ok is False
    assert reason == "count", reason


def test_the_true_count_headline_survives_a_lead_that_miscounts(client):
    """Where it would have reached a visitor. An accepted lead REPLACES the
    headline, so the renderer must print its own count and drop the sentence.
    Otherwise the only number on the screen is the one nobody verified."""
    _seed()
    text, options, _offer = _render(
        _entries("co-alfa", "co-beta", "co-gama"),
        "یک شرکت پیدا کردم:", "fa", 1, 3, "هوش مصنوعی")

    assert text.splitlines()[0].startswith("3 "), text
    assert "پیدا کردم" not in text, text
    assert len(options) == 3, options


# THE FALSE-NEGATIVE GUARD. A count check that eats ordinary leads degrades
# every list answer the bot gives, so an honest lead being rejected is a
# failure of this feature, not a safe default.

@pytest.mark.parametrize("lead", [
    "این شرکت های هوش مصنوعی هستند",
    "فهرست شرکت های هوش مصنوعی",
    "شرکت های هوش مصنوعی را اینجا ببینید",
])
def test_an_honest_persian_lead_is_still_accepted(client, lead):
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(lead, LIST_BODY_FA, LIST_QUESTION, "fa")
    assert ok is True, (lead, reason)


@pytest.mark.parametrize("lead", [
    "Here are the AI companies:",
    "These are the companies you can see below:",
])
def test_an_honest_english_lead_is_still_accepted(client, lead):
    _seed()
    from app.services import answer
    ok, reason = answer.frame_is_grounded(lead, LIST_BODY_EN, LIST_QUESTION_EN, "en")
    assert ok is True, (lead, reason)


# ── The shipped vocabulary file ──────────────────────────────────────────

FORBIDDEN_VOCAB = [
    # Negation: with any of these in the vocabulary, «این شرکت در این زمینه
    # فعال نیست» passes — every other token comes from the body.
    "نیست", "ندارد", "نه", "خیر", "not", "no", "never",
    # Evaluative: these license a ranking claim over real exhibitors.
    "بهترین", "تنها", "فقط", "best", "only", "worst",
]


def test_the_shipped_frame_vocabulary_holds_no_negation_and_no_judgement(client):
    """Asserted against the FILE, not against a constant, because the file is
    what ships. One negation word here would quietly license a defamatory
    sentence built entirely from grounded tokens."""
    import os
    from app.config import BASE_DIR

    path = os.path.join(BASE_DIR, "data", "frame-vocabulary.json")
    with open(path, encoding="utf-8") as f:
        vocab = json.load(f)

    assert set(vocab) >= {"fa", "en"}, sorted(vocab)
    assert len(vocab["fa"]) >= 20 and len(vocab["en"]) >= 20, \
        "a vocabulary this small rejects every usable lead"
    words = {w.strip() for w in vocab["fa"]} | {w.strip().lower() for w in vocab["en"]}
    for banned in FORBIDDEN_VOCAB:
        assert banned not in words, banned


def test_an_unreadable_vocabulary_file_makes_the_firewall_reject_everything(client):
    """Fail CLOSED. A missing or corrupt safety file must make the bot plainer,
    never looser: with no connector vocabulary, every lead fails check D and
    the deterministic template head is used instead."""
    _seed()
    from app.services import answer
    monkey_vocab = {}
    original = answer.FRAME_VOCAB
    try:
        answer.FRAME_VOCAB = monkey_vocab
        body = "3 شرکت:\n1. شرکت آلفا\n2. شرکت بتا\n3. شرکت گاما"
        # Even a lead made of the visitor's own words plus one connector fails,
        # because the connector has nowhere to come from.
        ok, reason = answer.frame_is_grounded(
            "این شرکت های هوش مصنوعی هستند", body, LIST_QUESTION, "fa")
        assert ok is False
        assert reason == "vocab", reason
    finally:
        answer.FRAME_VOCAB = original


# ── The firewall is applied where the body exists: render_options ────────

def test_a_rejected_lead_is_dropped_and_the_list_still_ships(client):
    """A rejected lead costs the visitor nothing. The deterministic head takes
    over, the numbered names are unchanged, and the answer goes out."""
    _seed()
    text, options, offer_state = _render(
        _entries("co-alfa", "co-beta", "co-gama"),
        "هفت شرکت در این زمینه فعالیت می‌کنند", "fa", 1, 3, "هوش مصنوعی")

    assert "هفت" not in text, text
    assert "شرکت آلفا" in text and "شرکت گاما" in text, text
    assert len(options) == 3, options


def test_a_rejected_lead_is_logged_so_the_rejection_rate_is_measurable(client):
    """FRAME_VOCAB has to be tuned against real traffic, not guessed. Every
    rejection writes its check letter and the language, so an operator can see
    whether the firewall is protecting visitors or just muting the bot."""
    _seed()
    # A price claim, so the row this asserts on is a VOCABULARY rejection —
    # the check FRAME_VOCAB actually decides, and therefore the one whose rate
    # an operator has to be able to watch.
    _render(_entries("co-alfa", "co-beta"),
            "ورود به نمایشگاه رایگان است", "fa", 1, 2, "")

    rows = _log_rows("answer.frame.rejected")
    assert rows, [r["event_name"] for r in _log_rows()]
    meta = json.loads(rows[0]["metadata"] or "{}")
    assert meta.get("reason") == "vocab", meta
    assert meta.get("lang") == "fa", meta


def test_an_accepted_lead_is_shown_above_the_numbered_names(client):
    """The other half: a grounded lead reaches the visitor, which is the only
    reason the model is allowed to write anything at all."""
    _seed()
    text, _options, _offer = _render(
        _entries("co-alfa", "co-beta", "co-gama"),
        "شرکت های هوش مصنوعی", "fa", 1, 3, "هوش مصنوعی")
    assert "شرکت های هوش مصنوعی" in text, text


# ── FIREWALL 2: the free prose ───────────────────────────────────────────

def test_prose_repeating_a_recorded_phone_number_is_accepted(client):
    """The verifier is a containment check, not a ban on numbers. The
    assistant's own recorded phone is exactly what a written answer should be
    able to say."""
    _seed()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        f"می توانید با {PHONE} تماس بگیرید.", "fa")
    assert ok is True, reason


def test_prose_inventing_a_date_is_rejected(client):
    """«۱۵ اسفند» appears in no setting and in no record. Today this string
    reaches the visitor untouched — app/services/openai.py returns resp.content
    raw into ChatResponse.text with no check of any kind."""
    _seed()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        "نمایشگاه در ۱۵ اسفند برگزار می شود.", "fa")
    assert ok is False
    assert reason == "digit", reason


def test_prose_inventing_a_website_is_rejected(client):
    """A link is the most damaging thing a chatbot can invent: a visitor will
    follow it."""
    _seed()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        "جزئیات در https://not-our-site.example.com آمده است.", "fa")
    assert ok is False
    assert reason == "shape", reason


def test_prose_repeating_the_configured_website_is_accepted(client):
    _seed()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        f"جزئیات در {WEBSITE} آمده است.", "fa")
    assert ok is True, reason


def test_a_rejected_answer_is_replaced_by_the_configured_refusal_at_status_200(client, monkeypatch):
    """End to end. The model confirms a date nobody recorded; the visitor gets
    the refusal wording the operator typed, not a 503 and not the fabrication.
    200, not 503, because we DID answer — we said we cannot answer this."""
    _seed()
    _force_tier2(monkeypatch)
    _stub_selection_off(monkeypatch)
    from app.db.queries import set_setting
    refusal = "من فقط درباره این رویداد پاسخ می‌دهم."
    set_setting("refusal_text_fa", refusal)
    _stub_ai_tail(monkeypatch, generated="بله، نمایشگاه در ۱۵ اسفند برگزار می شود.")

    r = _ask(client, "درباره غرفه ها توضیح بده")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "۱۵ اسفند" not in r.text, r.text
    assert body["text"] == refusal, body
    assert body["source"] == "refuse", body

    rows = _log_rows("generation.prose.rejected")
    assert rows, [x["event_name"] for x in _log_rows()]


def test_a_number_the_visitor_supplied_does_not_license_the_model_to_confirm_it(client, monkeypatch):
    """THE laundering test. The visitor asks «نمایشگاه ۱۵ اسفند برگزار
    می‌شود؟» and the model answers «بله، نمایشگاه ۱۵ اسفند برگزار می‌شود».
    Every digit in the answer now appears in the conversation — and the answer
    is still a fabrication. The verifier's source set is the assistant's own
    recorded facts ONLY; the visitor's message is deliberately excluded."""
    _seed()
    _force_tier2(monkeypatch)
    _stub_selection_off(monkeypatch)
    from app.db.queries import set_setting
    refusal = "من فقط درباره این رویداد پاسخ می‌دهم."
    set_setting("refusal_text_fa", refusal)
    _stub_ai_tail(monkeypatch, generated="بله، نمایشگاه در ۱۵ اسفند برگزار می شود.")

    r = _ask(client, "نمایشگاه ۱۵ اسفند برگزار می شود؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "۱۵ اسفند" not in body["text"], body["text"]
    assert body["text"] == refusal, body


def test_a_grounded_written_answer_is_served_unchanged(client, monkeypatch):
    """REGRESSION over existing behaviour, and it passes today on purpose.

    The verifier must not eat ordinary answers. A written reply whose only
    number is the assistant's own recorded phone goes out exactly as the model
    wrote it — this is the guard against the new check being tightened into a
    filter that mutes the chatbot."""
    _seed()
    _force_tier2(monkeypatch)
    _stub_selection_off(monkeypatch)
    good = f"می توانید با {PHONE} تماس بگیرید."
    _stub_ai_tail(monkeypatch, generated=good)

    r = _ask(client, "درباره غرفه ها توضیح بده")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == good, body
    assert body["source"] == "openai", body


# ── The refusal wording is a value, not only a prompt instruction ────────

def test_the_refusal_text_is_read_from_settings_so_a_new_customer_can_change_it(client):
    """It exists so the same string the model is TOLD to say is the string the
    code EMITS. A new deployment in a different category changes one setting,
    not a Python literal."""
    _seed()
    from app.db.queries import set_setting
    from app.services import scope

    assert scope.refusal_text("fa"), "there must be a working default"
    set_setting("refusal_text_fa", "فقط درباره بیمارستان پاسخ می‌دهم.")
    set_setting("refusal_text_en", "I only answer about this hospital.")
    assert scope.refusal_text("fa") == "فقط درباره بیمارستان پاسخ می‌دهم."
    assert scope.refusal_text("en") == "I only answer about this hospital."


# ── DEFECT 4: whole numbers and whole links, never substrings ────────────
#
# The verifier used to join the three recorded facts into ONE string and ask
# `run not in sources`. Measured against the SHIPPED defaults on 2026-08-28,
# that string holds 2026, 11, 14, 1405 and ۰۲۱۸۸۵۰۳۰۳۰ — so every single digit
# except 7 and 9 was already a substring of it, and so was every short prefix
# of the recorded website. These tests run against those shipped defaults on
# purpose: the hole only opens on a real install's data.

def _shipped_defaults():
    """Put the DEFAULT INOTEX facts in the settings, replacing the fixture's
    deliberately number-poor ones. This is the data a live install has."""
    from app.db.queries import set_setting
    from app.services.openai import (DEFAULT_ASSISTANT_KNOWLEDGE,
                                     DEFAULT_ASSISTANT_PHONE,
                                     DEFAULT_ASSISTANT_WEBSITE)
    set_setting("assistant_knowledge", DEFAULT_ASSISTANT_KNOWLEDGE)
    set_setting("assistant_phone", DEFAULT_ASSISTANT_PHONE)
    set_setting("assistant_website", DEFAULT_ASSISTANT_WEBSITE)


def test_prose_inventing_a_hall_number_is_rejected(client):
    """«سالن ۳ در ضلع شمالی است» — an invented hall number, read by a visitor
    who is standing at a booth and will walk there. The digit 3 appears inside
    the recorded phone ۰۲۱۸۸۵۰۳۰۳۰, so a substring test called it grounded."""
    _seed()
    _shipped_defaults()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded("سالن ۳ در ضلع شمالی است.", "fa")
    assert ok is False
    assert reason == "digit", reason


@pytest.mark.parametrize("prose", [
    "نمایشگاه ۵ روز باز است.",
    "غرفه شماره ۴۰ در سالن اصلی است.",
    "ورودی از درب ۶ است.",
])
def test_prose_inventing_a_small_number_is_rejected(client, prose):
    """Each of these digits is a substring of a recorded number and none of
    them is a recorded number. One and two-digit fabrications are the common
    case at an exhibition: hall numbers, booth numbers, gate numbers."""
    _seed()
    _shipped_defaults()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(prose, "fa")
    assert ok is False
    assert reason == "digit", reason


def test_prose_inventing_a_link_that_is_a_substring_of_the_recorded_site_is_rejected(client):
    """"otex.com" is not a site anybody recorded. It passed because it is a
    substring of the recorded inotex.com, and a visitor will follow a link."""
    _seed()
    _shipped_defaults()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        "جزئیات در otex.com آمده است.", "fa")
    assert ok is False
    assert reason == "shape", reason


def test_prose_repeating_the_recorded_dates_is_still_accepted(client):
    """The guard against over-tightening. The recorded dates are exactly what
    a written answer should be able to say."""
    _seed()
    _shipped_defaults()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        "نمایشگاه از ۱۱ تا ۱۴ شهریور ۱۴۰۵ برگزار می شود.", "fa")
    assert ok is True, reason


def test_prose_repeating_the_recorded_phone_with_a_separator_is_accepted(client):
    """A recorded number the model re-punctuated is still that number:
    «۰۲۱-۸۸۵۰۳۰۳۰» is the recorded «۰۲۱۸۸۵۰۳۰۳۰». Whole-number matching must
    not turn a correct phone number into a refusal at a live booth."""
    _seed()
    _shipped_defaults()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        "با ۰۲۱-۸۸۵۰۳۰۳۰ تماس بگیرید.", "fa")
    assert ok is True, reason


# ── A link the checker did not recognise as a link ───────────────────────
#
# WHAT WAS BROKEN (measured 2026-08-28). The prose verifier decided what a link
# IS from an allowlist of four TLDs: .com .ir .org .net, plus "@", a scheme and
# "www.". Anything else was not a link at all, so it never entered the check:
# «padyar.dev» and «inotex.co» were shipped to a visitor with nothing verifying
# them. A visitor will follow a link, and the modern TLD list is thousands long,
# so an allowlist of four was a guarantee of holes rather than a filter.
#
# `_looks_like_link()` now decides by STRUCTURE (labels, a dot, a final label
# of two or more letters, the whole token), and BOTH loops of the function use
# it: the one that reads the recorded facts and the one that reads the model's
# answer. Two different answers to "is this a link" would be a hole by
# construction: a shape the source loop misses is a shape the answer loop can
# never match against.

@pytest.mark.parametrize("host", [
    "padyar.dev",
    "inotex.co",
    "inotex.info",
    "exhibition.app",
    "booth.xyz",
    "inotex.tehran-expo.online",
])
def test_prose_inventing_a_link_on_an_unlisted_tld_is_rejected(client, host):
    """None of these is a recorded site and every one of them is a link a
    visitor would tap. Under a four-TLD allowlist not one of them was even
    looked at."""
    _seed()
    _shipped_defaults()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(
        f"جزئیات در {host} آمده است.", "fa")
    assert ok is False
    assert reason == "shape", (host, reason)


def test_a_recorded_site_on_an_unlisted_tld_is_recognised_on_both_sides(client):
    """The other half, and the reason ONE helper answers the question for both
    loops. An install whose own website is padyar.dev must be able to say so:
    if only the answer loop recognised the shape, the site the operator
    recorded would be refused as a fabrication on every turn. A neighbouring
    invented host on the same domain is still rejected, which is what shows the
    check is matching the whole token and not merely being switched off."""
    _seed()
    from app.db.queries import set_setting
    set_setting("assistant_website", "padyar.dev")
    from app.services import answer

    ok, reason = answer.generated_prose_is_grounded(
        "نشانی ما padyar.dev است.", "fa")
    assert ok is True, reason

    ok, reason = answer.generated_prose_is_grounded(
        "نشانی ما padyar.app است.", "fa")
    assert ok is False
    assert reason == "shape", reason


@pytest.mark.parametrize("prose", [
    "ورودی نمایشگاه در ضلع شرقی سالن اصلی قرار دارد.",
    "غرفه ها هر روز صبح باز می شوند و عصر بسته می شوند.",
    "The main entrance is on the east side of the hall.",
    "Several halls are open, e.g. the main one, and staff can guide you.",
    "Ask at the desk, i.e. the one next to the entrance.",
])
def test_ordinary_prose_with_no_link_is_not_falsely_rejected(client, prose):
    """THE FALSE-NEGATIVE GUARD, and it is the half that decides whether the
    check is usable. A structural host pattern is easy to write too loosely:
    "e.g." and "i.e." are a dot between two labels and would be links to a
    careless rule. They are not, because a one-letter final label is not a TLD.
    A verifier that refuses ordinary sentences turns every written answer into
    the refusal text, which is a worse product than the fabrication was."""
    _seed()
    _shipped_defaults()
    from app.services import answer
    ok, reason = answer.generated_prose_is_grounded(prose, "fa")
    assert ok is True, (prose, reason)


# ── DEFECT 5: the lead may not name the records it introduces ────────────

RELATION_LEAD = "شرکت آلفا شرکت بتا را دارد"


def test_a_lead_claiming_a_relation_between_two_listed_companies_is_rejected(client):
    """VERIFIED end to end on 2026-08-28: with آلفا/بتا/گاما listed, this lead
    passed length, digit, shape and the vocabulary subset test, because every
    one of its tokens appears somewhere in the rendering. It says one real
    exhibitor owns another. A set of words cannot judge a claim between two
    names, so the names are kept out of the lead entirely."""
    _seed()
    from app.services import answer
    body = ("3 شرکت در زمینه «هوش مصنوعی»:\n1. شرکت آلفا\n2. شرکت بتا\n"
            "3. شرکت گاما\nکدام‌یک را می‌خواهید بیشتر بشناسید؟")
    ok, reason = answer.frame_is_grounded(RELATION_LEAD, body, "", "fa")
    assert ok is False
    assert reason == "vocab", reason


def test_a_relational_lead_is_dropped_and_the_deterministic_head_ships(client):
    """The same case through the renderer, which is where it would reach a
    visitor: the headline WE computed is printed and the model's sentence is
    not."""
    _seed()
    text, options, _offer = _render(
        _entries("co-alfa", "co-beta", "co-gama"),
        RELATION_LEAD, "fa", 1, 3, "هوش مصنوعی")

    assert not text.startswith(RELATION_LEAD), text
    assert text.splitlines()[0].startswith("3 "), text
    assert len(options) == 3, options


def test_a_company_name_the_visitor_typed_does_not_license_naming_it(client):
    """The check that cannot live in the vocabulary test. Here the visitor
    typed both names, so «آلفا» and «بتا» are grounded in the question and the
    subset test passes — and the sentence is still an invented relation
    between two real exhibitors."""
    _seed()
    from app.services import answer
    body = ("3 شرکت:\n1. شرکت آلفا\n2. شرکت بتا\n3. شرکت گاما\n"
            "کدام‌یک را می‌خواهید بیشتر بشناسید؟")
    ok, reason = answer.frame_is_grounded(
        RELATION_LEAD, body, "آلفا و بتا چه فرقی دارند؟", "fa")
    assert ok is False
    assert reason == "names", reason


def test_a_lead_naming_one_listed_company_is_rejected_too(client):
    """One name is enough. «شرکت آلفا را ببینید» reads as a recommendation the
    organizer never made, and the list right below it already says the names."""
    _seed()
    from app.services import answer
    body = "2 شرکت:\n1. شرکت آلفا\n2. شرکت بتا\nکدام‌یک را می‌خواهید؟"
    ok, reason = answer.frame_is_grounded(
        "شرکت آلفا را ببینید", body, "آلفا و بتا", "fa")
    assert ok is False
    assert reason in ("vocab", "names"), reason


# ── DEFECT 6: the headline noun follows what the records ARE ─────────────
#
# The ai_options branch ranks over the WHOLE corpus — 169 exhibitor rows AND
# ~54 FAQ rows — and the renderer printed the collection noun regardless.
# Verified on 2026-08-28: three FAQ records rendered as «3 شرکت:\n1. اطلاعات
# نمایشگاه\n2. ساعت کاری…», our own deterministic renderer stating a falsehood.

MIXED_DATASET = DATASET + [
    ("faq-hours", "ساعت کاری", "نمایشگاه هر روز از صبح باز است.", ""),
    ("faq-entry", "ورودی نمایشگاه", "ورودی اصلی در ضلع شرقی قرار دارد.", ""),
]


def test_a_list_of_faq_records_is_not_called_companies(client):
    """None of these three rows is a company: none has a company_profiles row.
    Calling them «شرکت» is the renderer inventing a fact."""
    _seed(MIXED_DATASET)
    text, _options, _offer = _render(
        _entries("faq-guide", "faq-hours", "faq-entry"), "", "fa", 1, 3, "")

    head = text.splitlines()[0]
    assert "شرکت" not in head, text
    assert head == "3 مورد:", text


def test_a_list_mixing_one_faq_row_with_companies_is_not_called_companies(client):
    """A mixed list is the live case: the model picks the best few records and
    they do not all come from the same kind of row."""
    _seed(MIXED_DATASET)
    text, _options, _offer = _render(
        _entries("co-alfa", "co-beta", "faq-hours"), "", "fa", 1, 3, "")
    assert "شرکت" not in text.splitlines()[0], text


def test_an_english_mixed_list_is_not_called_companies(client):
    _seed(MIXED_DATASET)
    text, _options, _offer = _render(
        _entries("co-alfa", "faq-hours"), "", "en", 1, 2, "")
    assert "companies" not in text.splitlines()[0], text
    assert text.splitlines()[0] == "2 items:", text


def test_a_list_of_only_companies_keeps_the_collection_noun(client):
    """The guard against over-correcting. When every listed record IS a
    company, the operator's configured noun is what the visitor reads."""
    _seed(MIXED_DATASET)
    text, _options, _offer = _render(
        _entries("co-alfa", "co-beta", "co-gama"), "", "fa", 1, 3, "")
    assert text.splitlines()[0] == "3 شرکت:", text


def test_an_install_with_no_profile_data_keeps_the_configured_noun(client):
    """A hospital that never ordered the leads module has no way to tell one
    row from another. This check only ever DOWNGRADES a claim it can show is
    wrong, so with nothing to check against, the configured noun stands.

    Here the company_profiles TABLE does not exist at all, so the read raises
    and the noun is left alone. The next test covers the other shape of "nothing to
    check against", which reaches a different line."""
    _seed(MIXED_DATASET, with_profiles=False)
    from app.db.queries import set_setting
    set_setting("collection_noun_fa", "بخش")
    text, _options, _offer = _render(
        _entries("faq-guide", "faq-hours"), "", "fa", 1, 2, "")
    assert text.splitlines()[0] == "2 بخش:", text


def test_an_install_whose_profiles_table_is_empty_keeps_the_configured_noun(client):
    """DAY ONE of a leads install: the table is there, and nobody has filled in
    a single profile yet. The read succeeds and returns an empty set, so this
    does NOT go through the missing-table arm above. It reaches the emptiness
    guard on the comparison itself.

    Without that guard the subset test is `ids <= set()`, which is false for
    every non-empty list, and so every list on a brand-new install would be
    downgraded to «مورد», a real customer's configured noun replaced by a
    placeholder because their staff had not started entering data. The check
    exists to correct a claim it can PROVE wrong; an empty table proves
    nothing."""
    _seed(MIXED_DATASET, with_profiles=False)
    from app.services import leads
    leads.ensure_tables()
    from app.db.queries import set_setting
    set_setting("collection_noun_fa", "بخش")

    text, _options, _offer = _render(
        _entries("faq-guide", "faq-hours"), "", "fa", 1, 2, "")
    assert text.splitlines()[0] == "2 بخش:", text

    text_en, _o, _s = _render(_entries("co-alfa", "faq-hours"), "", "en", 1, 2, "")
    assert text_en.splitlines()[0] == "2 companies:", text_en
