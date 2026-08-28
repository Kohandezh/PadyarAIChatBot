"""How a visitor actually phrases a list question, versus how the tier read it.

WHAT HAPPENED (live on inotex.padyar.com, 2026-08-28). «شرکت های فعال در حوزه
هوش مصنوعی» returned the right numbered list. Every one of these returned the
generic exhibitor FAQ instead:

    شرکت های فعال در حوضه هوش مصنوعی
    من شرکت های فعال در حوزه هوش مصنوعی که در اینوتکس شرکت کرده اند رو اطلاعات شون رو میخوام
    شرکت های فعال در حوزه فناوری اطلاعات که در اینوتکس شرکت کرده اند رو اطلاعات شون رو میخوام!
    دیگه چه شرکت هایی داریم؟

The list-intent check fired on ALL of them. The break was one line later. Topic
keywords were "every content token that is not on the _MACHINERY blocklist",
and the filter required EVERY one of them to appear in a company's row:

    شرکت های فعال در حوضه هوش مصنوعی   -> {حوضه, هوش, مصنوعی}
    ...اطلاعات شون رو میخوام            -> {اطلاعات, اند, رو, شون, مصنوعی, میخوام, هوش, کرده}
    دیگه چه شرکت هایی داریم؟            -> {دیگه}

No company record contains «میخوام» or «شون» or «دیگه», so the match set was
empty, the tier returned None, and the question fell through to the AI path
that cannot see the company table at all. One misspelled letter («حوضه» for
«حوزه») did the same thing on its own.

A hand-written blocklist of "words that are not topics" can never be finished.
Any sentence longer than the three-word form carries words nobody listed.

THE FIX under test: the topic vocabulary comes from the DATA, not from a list
in the source. `company_profiles.activity_field` is a controlled vocabulary
(«هوش مصنوعی و داده», «فناوری اطلاعات، ارتباطات و نرم‌افزار», ...), and so are
`province` and `company_type`. A query token counts as a topic only if some
facet value contains it; the facet with the most overlapping tokens wins, and
everything the visitor said around it is ignored. Zero overlap means no filter,
which is the correct reading of «دیگه چه شرکت هایی داریم؟».
"""
import pytest

from tests.test_company_search import _seed, client, _mock_ai  # noqa: F401


# The real facet values, copied from the organizer's sheet. They are
# pipe-separated in one column, which is why a company can sit in three fields
# at once and why the fix has to split before it compares.
AI = "هوش مصنوعی و داده"
IT = "فناوری اطلاعات، ارتباطات و نرم‌افزار"
ROBOT = "اتوماسیون، رباتیک و هوشمندسازی"
HEALTH = "تجهیزات پزشکی و سلامت دیجیتال"
# Seeded so «فناوری» is a word TWO facets share, exactly as in production.
# With only one facet carrying it, a bare «فناوری» would look distinctive and
# the quantum test below would pass for the wrong reason.
NANO = "نانو فناوری"

COMPANIES = [
    ("co-ava", "شرکت آوا", "معرفی شرکت آوا: سامانه های پردازش تصویر.", AI),
    ("co-rayan", "شرکت رایان", "شرکت رایان سامانه های تصمیم یار می سازد.", AI),
    ("co-negar", "شرکت نگار", "شرکت نگار در زمینه گفتار کار می کند.",
     f"{AI} | {ROBOT}"),
    ("co-dekio", "شرکت دکیو", "اطلاعات درباره شرکت دکیو: سازنده سامانه های نرم افزاری.", IT),
    ("co-saba", "شرکت صبا", "شرکت صبا تجهیزات بیمارستانی می سازد.", HEALTH),
    ("co-nano", "شرکت نانو", "شرکت نانو پوشش های سطحی تولید می کند.", NANO),
]

AI_TITLES = {"شرکت آوا", "شرکت رایان", "شرکت نگار"}


def _titles(result):
    return {o["title"] for o in result["options"]}


def _ask(query, lang="fa"):
    from app.services.company_search import answer_company_list
    return answer_company_list(query, lang=lang)


# ── The four production failures ─────────────────────────────────────────

def test_a_long_natural_sentence_still_finds_the_field(client):
    """The sentence a visitor actually types. It CONTAINS «هوش مصنوعی», which
    works on its own, and everything else in it is conversation."""
    _seed(COMPANIES)
    r = _ask("من شرکت های فعال در حوزه هوش مصنوعی که در اینوتکس"
             " شرکت کرده اند رو اطلاعات شون رو میخوام")
    assert r is not None, "the tier refused a question it can answer exactly"
    assert r["count"] == 3, r
    assert _titles(r) == AI_TITLES, r


def test_the_common_misspelling_of_hoze_does_not_break_the_filter(client):
    """«حوضه» (with ض) for «حوزه» is one wrong letter and a common one. It is
    not a topic, it is a typo in the machinery, and the words that ARE topics
    are still right there."""
    _seed(COMPANIES)
    r = _ask("شرکت های فعال در حوضه هوش مصنوعی")
    assert r is not None, "one misspelled letter turned the tier off"
    assert _titles(r) == AI_TITLES, r


def test_asking_what_other_companies_there_are_lists_them_all(client):
    """«دیگه» is not a field. With no facet matched there is no filter, and
    the honest answer to "what other companies do we have" is all of them."""
    _seed(COMPANIES)
    r = _ask("دیگه چه شرکت هایی داریم؟")
    assert r is not None, r
    assert r["count"] == len(COMPANIES), r
    assert r["filter_label"] == "", (
        "no facet matched, so the headline must not claim a filter")


def test_the_information_technology_sentence_selects_that_field(client):
    """The IT twin of the AI sentence. «اطلاعات» is a real facet word here
    («فناوری اطلاعات...») and the visitor means it."""
    _seed(COMPANIES)
    r = _ask("شرکت های فعال در حوزه فناوری اطلاعات که در اینوتکس"
             " شرکت کرده اند رو اطلاعات شون رو میخوام!")
    assert r is not None, r
    assert _titles(r) == {"شرکت دکیو"}, r


def test_fanavari_written_as_two_words_still_finds_the_field(client):
    """Reported live: «شرکت های فعال در حوضه فن آوری اطلاعات چی؟». Two
    misspellings at once — «حوضه» for «حوزه», and «فن آوری» split into two
    words where the facet spells it «فناوری». normalize_persian keeps them as
    two tokens and does not fold آ to ا, so plain token equality can never
    join them. Adjacent tokens are therefore also compared JOINED, with the
    alef variants folded."""
    _seed(COMPANIES)
    r = _ask("شرکت های فعال در حوضه فن آوری اطلاعات چی؟")
    assert r is not None, "two spelling slips turned the tier off"
    assert _titles(r) == {"شرکت دکیو"}, r


def test_asking_which_other_companies_exist_lists_them_all(client):
    """The «هستند» twin of «داریم». Reported live as a second failure, and it
    is the same question."""
    _seed(COMPANIES)
    r = _ask("دیگه چه شرکت هایی هستند؟")
    assert r is not None, r
    assert r["count"] == len(COMPANIES), r


# ── Misspellings of the TOPIC itself ─────────────────────────────────────
#
# Ignoring an unknown word covers «حوضه», because «حوزه» is machinery. It does
# nothing when the visitor misspells the topic: «هوش مصنوی» names the facet and
# matches none of its tokens, and we would be back to fixing one report at a
# time.
#
# A closed vocabulary is what makes the general fix cheap. There are ~30 field
# names, so every query word can be compared against all of them by edit
# distance. That is more reliable here than asking a model, because the set of
# right answers is known and tiny.

def test_a_misspelled_topic_word_still_finds_the_field(client):
    _seed(COMPANIES)
    # BOTH words are wrong in each case. With one of them spelled right the
    # test passes on the distinctive-single-word rule and proves nothing about
    # fuzziness — the first version of this test did exactly that.
    for typo in ("شرکت های حوزه هووش مصنوی",      # doubled و AND missing ع
                 "شرکت های حوزه هوشش مصنوعیی",   # a doubled letter in each
                 "شرکت های حوزه هوش مصنوععی"):    # doubled ع, «هوش» correct
        r = _ask(typo)
        assert r is not None, f"{typo!r} turned the tier off"
        assert _titles(r) == AI_TITLES, (typo, r)


def test_a_misspelled_province_still_filters(client):
    """Same rule, a different facet column."""
    import app.db.connection as dbc
    _seed(COMPANIES)
    conn = dbc.get_db_connection()
    conn.execute("UPDATE company_profiles SET province = 'اصفهان'"
                 " WHERE dataset_id = 'co-saba'")
    conn.commit(); conn.close()
    r = _ask("شرکت های استان اصفحان")          # ح for ه
    assert r is not None and _titles(r) == {"شرکت صبا"}, r


def test_a_short_word_is_not_fuzzy_matched(client):
    """The guard. Edit distance on short words connects unrelated things —
    «موش» and «هوش» are one letter apart and mean mouse and mind. Fuzziness is
    only allowed where the word is long enough for it to mean something."""
    _seed(COMPANIES)
    r = _ask("شرکت های حوزه موش")
    assert r is None, r


def test_a_real_different_field_is_not_fuzzed_into_another(client):
    """«نانو فناوری» and «فناوری اطلاعات...» share a word and must stay
    distinct: a fuzzy match must never merge two facets that both exist."""
    _seed(COMPANIES)
    assert _titles(_ask("شرکت های حوزه نانو فناوری")) == {"شرکت نانو"}, "nano"


# ── The rule that resolves them ──────────────────────────────────────────

def test_the_best_matching_facet_wins_over_a_partial_one(client):
    """«اطلاعات شون» makes the IT facet overlap by one token while the AI
    facet overlaps by two. Taking every facet with any overlap would return
    the union and call it "AI companies"; the strongest match is the answer."""
    _seed(COMPANIES)
    r = _ask("شرکت های هوش مصنوعی رو اطلاعات شون رو میخوام")
    assert _titles(r) == AI_TITLES, r


def test_a_company_in_two_fields_is_found_under_either(client):
    """activity_field is pipe-separated, so «شرکت نگار» is both AI and
    robotics. Comparing against the raw column instead of its parts would
    match neither."""
    _seed(COMPANIES)
    assert _titles(_ask("شرکت های حوزه رباتیک")) == {"شرکت نگار"}, "robotics"
    assert "شرکت نگار" in _titles(_ask("شرکت های حوزه هوش مصنوعی")), "AI"


def test_a_field_nobody_is_in_still_defers_instead_of_listing_nothing(client):
    """The one case that must keep returning None. «کوانتومی» is a real field
    name in the taxonomy but no seeded company is in it, and printing "0
    companies" is a confident wrong answer where deferring is a correct one."""
    _seed(COMPANIES)
    assert _ask("شرکت های حوزه فناوری های کوانتومی") is None


def test_the_headline_names_the_facet_that_was_matched(client):
    """«۳ شرکت در زمینه ...» has to say WHICH zemine, and it has to say the
    facet the tier actually filtered by, not the words the visitor typed
    around it."""
    _seed(COMPANIES)
    r = _ask("من شرکت های فعال در حوزه هوش مصنوعی رو میخوام")
    assert "هوش" in r["filter_label"] and "مصنوعی" in r["filter_label"], r
    assert "میخوام" not in r["filter_label"], r
    assert "میخوام" not in r["text"], r


def test_a_question_about_one_named_company_is_still_not_a_list(client):
    """The guard that must not regress. Loosening the topic filter must not
    turn a single-company question into a list of everything."""
    _seed(COMPANIES)
    r = _ask("شرکت دکیو چه کار می کند؟")
    assert r is None or r["count"] == 1, r


def test_province_filtering_still_works(client):
    """province and company_type are facets for the same reason
    activity_field is: the visitor points at a column the database holds."""
    import app.db.connection as dbc
    _seed(COMPANIES)
    conn = dbc.get_db_connection()
    conn.execute("UPDATE company_profiles SET province = 'اصفهان'"
                 " WHERE dataset_id = 'co-saba'")
    conn.execute("UPDATE company_profiles SET province = 'تهران'"
                 " WHERE dataset_id <> 'co-saba'")
    conn.commit()
    conn.close()
    r = _ask("شرکت های استان اصفهان")
    assert r is not None and _titles(r) == {"شرکت صبا"}, r


# ── Naming a field IS asking for its companies ───────────────────────────
#
# Found by scripts/persona_probe.py against the live install, 2026-08-28: only
# ONE of 28 conversation turns reached this tier. Nine went to the old
# single-document path. The trigger required the word «شرکت» (or an attached
# plural of it), and a visitor who names a FIELD is asking for its companies
# whether or not they happen to say that word.

def test_naming_a_field_without_the_word_sherkat_is_a_list_question(client):
    """A school student's actual phrasing. There is no «شرکت» anywhere in it,
    and it is unmistakably a request for the robotics exhibitors."""
    _seed(COMPANIES)
    r = _ask("من به رباتیک علاقه دارم چیزی هست؟")
    assert r is not None, "naming a field was not read as asking for it"
    assert _titles(r) == {"شرکت نگار"}, r


def test_the_colloquial_plural_sherketaye_is_a_list_question(client):
    """«شرکتای» is how people type «شرکت‌های». The intent check knew three
    spellings and not this one."""
    _seed(COMPANIES)
    r = _ask("شرکتای هوش مصنوعی کیا هستن؟")
    assert r is not None, r
    assert _titles(r) == AI_TITLES, r


def test_a_question_that_names_no_field_is_still_not_a_list(client):
    """The guard. Reading a facet match as list intent must not turn every
    question into a list: «ورودی پول میخواد؟» and «تا کی بازه؟» name no field
    and are ordinary FAQ questions."""
    _seed(COMPANIES)
    for q in ("ورودی پول میخواد؟", "تا کی بازه؟", "اینجا چه خبره؟"):
        assert _ask(q) is None, q


# ── When the DATA is wrong ───────────────────────────────────────────────
#
# Found by replaying the personas against a copy of the production content,
# 2026-08-28. Two of the organizer's 170 rows have the company's whole
# DESCRIPTION pasted into the «حوزه فعالیت» column instead of a category. Those
# became facets, and a paragraph contains «فناوری», «هوش», «سلامت», «برق» and
# «آموزش», so it matched almost every question: «تا کی بازه؟» came back as a
# list of one company.
#
# Reading the vocabulary from the data means the data can poison it. A category
# label is SHORT by nature, so a value the length of a paragraph is not one.

PROSE_IN_THE_FIELD_COLUMN = (
    "شرکت آکادمی روبوآموز در زمینه آموزش برنامه نویسی و هوش مصنوعی به کودکان "
    "و نوجوانان فعالیت می کند. این مجموعه با ارائه دوره های آموزشی اسکرچ، "
    "پایتون، هوش مصنوعی و ساخت بازی و پروژه های دیجیتال به کودکان کمک می کند "
    "تا ضمن یادگیری فناوری، مهارت های حل مسئله و تفکر الگوریتمی خود را تقویت "
    "کنند. جهت کسب اطلاعات بیشتر به وب سایت شرکت مراجعه کنید."
)


def test_a_paragraph_in_the_field_column_is_not_a_facet(client):
    _seed(COMPANIES + [("co-bad", "شرکت بدداده",
                        "معرفی شرکت بدداده.", PROSE_IN_THE_FIELD_COLUMN)])
    # A plain FAQ question must not become a company list just because one row
    # has a paragraph where its category should be.
    assert _ask("تا کی بازه؟") is None
    assert _ask("برنامه استیج امروز چیه؟") is None
    # And a real field question must still return the real field, not the row
    # whose paragraph happens to contain the same words.
    r = _ask("شرکت های حوزه هوش مصنوعی")
    assert r is not None and _titles(r) == AI_TITLES, r


def test_the_word_sherkat_inside_a_facet_value_is_not_a_topic(client):
    """«نوع مجموعه» holds values like «صندوق سرمایه‌گذاری خطرپذیر شرکتی». The
    word «شرکت» is how a visitor asks for companies AT ALL, so counting it as a
    topic made «دیگه چه شرکت هایی هستند؟» filter down to the three rows whose
    company_type happens to spell it."""
    import app.db.connection as dbc
    _seed(COMPANIES)
    conn = dbc.get_db_connection()
    conn.execute("UPDATE company_profiles SET company_type ="
                 " 'صندوق سرمایه گذاری خطرپذیر شرکتی' WHERE dataset_id = 'co-ava'")
    conn.commit(); conn.close()
    r = _ask("دیگه چه شرکت هایی هستند؟")
    assert r is not None and r["count"] == len(COMPANIES), r


def test_a_greeting_is_not_fuzzed_into_a_field(client):
    """«سلام» and «سلامت» are one letter apart, and «تجهیزات پزشکی و سلامت
    دیجیتال» is a real facet. Measured against a copy of the production content
    on 2026-08-28: «سلام» came back as a list of 16 health companies.

    The rule that stops it: only correct a word the corpus does not already
    know. «سلام» is a real word and a real FAQ title here, so it is taken at
    face value; «اصفحان» is not a word at all, so it is corrected."""
    _seed(COMPANIES, extra_dataset=[
        ("faq-hello", "سلام", "سلام! خوشحالم که اینجا همراه شما هستم.")])
    assert _ask("سلام") is None
    assert _ask("سلام من دانشجوی کامپیوترم") is None
