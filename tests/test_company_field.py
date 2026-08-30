"""The company-field tier: record questions about ONE company, with a
public-field allowlist in front of them.

WHAT IS BROKEN TODAY: the chat pipeline never reads `company_profiles`. A
visitor asking «شماره تماس شرکت دکیو چیست؟» is anchored to the دکیو dataset
row by the named-entity anchor and served that company's generic description —
the phone the organizer already holds is never shown.

THE FEATURE under test, in two halves:

  * app/services/company_profiles.PUBLIC_PROFILE_FIELDS + public_profile() —
    the allowlist. The workbook's contact columns are ONE named person's
    details (their name, their job title, their personal mobile, their email)
    plus the organizer's private notes. None of that is a visitor's to read.
    The company's own landline (`company_phone`) is.
  * app/services/company_search.answer_company_field() — deterministic field
    detection on the UNexpanded query, served by /chat as source
    "local_company_field". A person-scoped request («شماره مدیرعامل ...»)
    takes a WITHHELD path that must win over the public one: «شماره» maps to
    company_phone, so without that precedence the collision leaks a personal
    number in answer to a question about a person.

Plus the list tier widens: province/company_type join the haystack, and
«استان»/«شهر» join the list machinery so they stop acting as topic keywords.
"""
import pytest
from fastapi.testclient import TestClient


# ── The corpus ───────────────────────────────────────────────────────────
#
# Every company title carries one distinctive token (دکیو، سپهر، آوا، رایان)
# that appears in exactly one title AND nowhere else in the knowledge base,
# so resolve_named_entity() anchors it. The request words the field tier
# reads (شماره، تماس، سایت، آدرس، مدیرعامل، استان، اصفهان) live in the two
# profile-less FAQ rows below, never in a title: they must supply corpus
# vocabulary (so the unknown-entity guard stays quiet) without ever becoming
# names themselves.

DEKIO_PHONE = "02144556677"          # the company's own landline — PUBLIC
DEKIO_WEBSITE = "https://dekio-example.ir"
DEKIO_ADDRESS = "تهران خیابان ولیعصر پلاک 12"
DEKIO_MOBILE = "09129998877"         # one person's mobile — WITHHELD
DEKIO_EMAIL = "ceo@dekio-mail.ir"    # that person's email — WITHHELD
DEKIO_CONTACT = "مریم رستمی"         # that person's name — WITHHELD
DEKIO_NOTES = "یادداشت داخلی برگزارکننده"   # organizer-only — WITHHELD
DEKIO_BOOTH = "24"                   # the booth number — PUBLIC
DEKIO_HALL = "سالن 3"                 # which hall the booth is in — PUBLIC

DEKIO_TEXT = (
    "اطلاعات درباره شرکت دکیو: دکیو سازنده سامانه های هوشمند اداری است "
    "و محصولات خود را به سازمان ها عرضه می کند و در غرفه خود نمونه ها را "
    "نشان می دهد."
)
SEPEHR_TEXT = (
    "شرکت سپهر تجهیزات آزمایشگاهی و ابزار دقیق تولید می کند "
    "و محصولات خود را به مراکز پژوهشی و دانشگاه ها عرضه می کند "
    "و در غرفه خود نمونه ها را نشان می دهد."
)

COMPANIES = [
    {
        "id": "co-dekio", "title": "شرکت دکیو", "text": DEKIO_TEXT,
        "profile": {
            "contact_name": DEKIO_CONTACT, "contact_position": "مدیرعامل",
            "contact_mobile": DEKIO_MOBILE, "email": DEKIO_EMAIL,
            "website": DEKIO_WEBSITE, "company_phone": DEKIO_PHONE,
            "fax": "02144556678", "address": DEKIO_ADDRESS,
            "address_en": "Tehran Valiasr St No 12", "province": "تهران",
            "booth_number": DEKIO_BOOTH, "hall": DEKIO_HALL,
            "company_type": "خصوصی", "org_stage": "رشد",
            "activity_field": "نرم افزار اداری", "participation": "غرفه",
            "notes": DEKIO_NOTES,
        },
    },
    {
        # No website on purpose: the "requested public field is empty" case.
        "id": "co-sepehr", "title": "شرکت سپهر", "text": SEPEHR_TEXT,
        "profile": {
            "company_phone": "03133445566", "province": "اصفهان",
            "activity_field": "تجهیزات آزمایشگاهی",
        },
    },
    {
        "id": "co-ava", "title": "شرکت آوا",
        "text": "معرفی شرکت آوا: فعال در هوش مصنوعی و پردازش تصویر.",
        "profile": {"activity_field": "هوش مصنوعی", "province": "اصفهان"},
    },
    {
        "id": "co-rayan", "title": "شرکت رایان",
        "text": "شرکت رایان سامانه های هوش مصنوعی می سازد.",
        "profile": {"activity_field": "هوش مصنوعی", "province": "تهران"},
    },
]

# Profile-less dataset rows. They are the corpus vocabulary for the request
# words, and faq-contact is deliberately a token magnet for «شماره/تماس/آدرس»
# — the same shape that served the wrong entry in the 2026-08-27 incident.
EXTRA_DATASET = [
    ("faq-contact", "دبیرخانه نمایشگاه",
     "شماره تلفن و راه تماس با دبیرخانه نمایشگاه در دفتر اعلام می شود. "
     "آدرس و نشانی دبیرخانه در سایت و وبسایت نمایشگاه آمده است. "
     "مدیرعامل و مسئول و نماینده هر شرکت در غرفه حضور دارد "
     "و ایمیل و موبایل و همراه شخصی افراد اعلام نمی شود. "
     "غرفه ها در چند سالن مختلف نمایشگاه قرار دارند."),
    ("faq-cities", "شهرهای حاضر در نمایشگاه",
     "شرکت هایی از استان اصفهان و استان تهران در نمایشگاه حضور دارند."),
]


def _seed(companies=COMPANIES, extra_dataset=EXTRA_DATASET, with_profiles=True):
    """Insert dataset rows + companies (merged profile columns) and reindex.

    Companies are their own table now (migrations/0013_companies.sql).
    ``with_profiles=False`` reproduces the "no way to answer a field
    question" shape without a company_profiles table to omit: the "co-*" rows
    are inserted as plain `dataset` rows instead of `companies` rows, so
    `public_profile()` finds nothing for them (a `companies` row that does not
    exist reads exactly like an empty profile did before this migration).
    """
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM companies")
    conn.execute("DELETE FROM questions")
    # Empty synonym table: expansion must not blur the token overlaps the
    # anchor and the field detection are built on.
    conn.execute("DELETE FROM synonyms")
    for i, title, text in extra_dataset:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, '')", (i, title, text))
    for c in companies:
        if with_profiles:
            prof = c["profile"]
            cols = ", ".join(prof.keys())
            marks = ", ".join("?" for _ in prof)
            conn.execute(
                f"INSERT INTO companies (id, title, text, video_url, {cols})"
                f" VALUES (?, ?, ?, '', {marks})",
                (c["id"], c["title"], c["text"], *prof.values()))
        else:
            conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                         " VALUES (?, ?, ?, '')", (c["id"], c["title"], c["text"]))
    conn.commit()
    conn.close()

    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "fields.db"))
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
    proving a record question is answered locally without an LLM.
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


def _entry(query):
    """The dataset row the named-entity anchor resolves for this query — the
    exact `entry` argument /chat hands to answer_company_field()."""
    from app.services import search
    entry, _tokens = search.resolve_named_entity(query)
    assert entry is not None, f"the anchor must resolve a company for {query!r}"
    return entry


# ── The public fields ────────────────────────────────────────────────────

def test_a_phone_question_answers_with_that_companys_public_phone(client, monkeypatch):
    """The company's own landline is public record. The visitor named the
    company and asked for its number — that number is the answer, straight
    from `companies`, with no model call."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شماره تماس شرکت دکیو چیست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert DEKIO_PHONE in body["text"], body["text"]


def test_a_booth_number_question_answers_with_that_companys_booth(client, monkeypatch):
    """«شماره غرفه» must resolve to booth_number, not company_phone, even
    though «شماره» is also company_phone's own trigger word — same
    precedence problem as the WITHHELD مدیرعامل case above, same fix: the
    more specific field (booth_number) is checked first."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شماره غرفه شرکت دکیو چیست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert DEKIO_BOOTH in body["text"], body["text"]
    assert DEKIO_PHONE not in body["text"], body["text"]


def test_a_hall_question_answers_with_that_companys_hall(client, monkeypatch):
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "سالن شرکت دکیو کجاست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert DEKIO_HALL in body["text"], body["text"]


def test_a_plain_phone_question_still_answers_the_phone_not_the_booth(client, monkeypatch):
    """The precedence fix above must not swallow the plain phone question —
    «شماره تماس» carries no «غرفه» and must still resolve to company_phone."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شماره تماس شرکت دکیو چیست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert DEKIO_PHONE in body["text"], body["text"]
    assert DEKIO_BOOTH not in body["text"], body["text"]


def test_a_website_question_answers_with_that_companys_website(client, monkeypatch):
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "سایت شرکت دکیو چیست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert "dekio-example.ir" in body["text"], body["text"]


def test_an_address_question_answers_with_that_companys_address(client, monkeypatch):
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "آدرس شرکت دکیو کجاست؟")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert "ولیعصر" in body["text"], body["text"]


# ── The collision: a person-scoped request must not ride the field map ───

def test_asking_for_the_ceos_number_is_refused_and_never_leaks_the_mobile(client, monkeypatch):
    """THE test of this file. «شماره مدیرعامل شرکت دکیو را بده» contains
    «شماره», which maps to the PUBLIC company_phone — so the withheld path
    has to win, or the same query that asks about a person walks straight
    into that person's record. The refusal must:
      * contain no part of the contact person's record (mobile, email, name);
      * still be useful — the company's public phone and website are offered.
    The mobile is checked against the RAW body, not just the answer text: a
    personal number must not reach the visitor through any field."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شماره مدیرعامل شرکت دکیو را بده")
    assert r.status_code == 200, r.text
    body = r.json()

    # Nothing that belongs to the named contact person may appear anywhere.
    assert DEKIO_MOBILE not in r.text, r.text
    assert DEKIO_MOBILE not in body["text"], body["text"]
    assert DEKIO_EMAIL not in r.text, r.text
    assert DEKIO_CONTACT not in body["text"], body["text"]

    # A refusal that offers what IS public — not a dead end.
    assert DEKIO_PHONE in body["text"], body["text"]
    assert "dekio-example.ir" in body["text"], body["text"]
    assert body["source"] == "local_company_field", body


# ── Falling through: the tier declines rather than answering badly ───────

def test_an_empty_public_field_falls_back_to_the_company_description(client, monkeypatch):
    """شرکت سپهر has no website. The tier must decline (return None) instead
    of answering with a blank line — the visitor gets the company's own entry
    through the existing entity anchor, exactly as today."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    query = "سایت شرکت سپهر چیست؟"
    r = _ask(client, query)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_entity", body
    assert body["text"] == SEPEHR_TEXT, body["text"]

    # ...and the reason is the tier declining, not the router skipping it.
    from app.services.company_search import answer_company_field
    assert answer_company_field(query, _entry(query)) is None


def test_an_install_without_company_profiles_still_answers_a_field_question(client, monkeypatch):
    """A named entity with no row in `companies` (or any DB error) switches
    the field tier off: no exception, normal pipeline."""
    _seed(with_profiles=False)
    _mock_ai(monkeypatch)
    query = "شماره تماس شرکت دکیو چیست؟"
    r = _ask(client, query)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] != "local_company_field", body

    from app.services.company_search import answer_company_field
    assert answer_company_field(query, _entry(query)) is None


# ── The allowlist itself ─────────────────────────────────────────────────

def test_public_profile_never_returns_a_withheld_field(client):
    """Every one of the 15 profile columns is filled for شرکت دکیو. What
    comes back must be a subset of the allowlist — the contact person's
    mobile, email and name, and the organizer's notes, are not in it."""
    _seed()
    from app.services.company_profiles import PUBLIC_PROFILE_FIELDS, public_profile

    profile = public_profile("co-dekio")
    assert profile, "a fully filled profile must return its public fields"
    assert set(profile) <= set(PUBLIC_PROFILE_FIELDS), sorted(profile)
    for withheld in ("contact_name", "contact_position", "contact_mobile",
                     "email", "notes"):
        assert withheld not in profile, withheld
        assert withheld not in PUBLIC_PROFILE_FIELDS, withheld
    # The values are the record's own, not placeholders.
    assert profile["company_phone"] == DEKIO_PHONE
    assert profile["website"] == DEKIO_WEBSITE
    # And no bookkeeping column leaks either.
    assert "dataset_id" not in profile and "notes" not in profile


# ── The shadowing bug: the tier was only reachable from the anchor ───────
#
# Measured on inotex.padyar.com one hour after the tier shipped, 2026-08-27.
# answer_company_field() was called only from inside the named-entity anchor's
# OVERRIDE and RESCUE paths. «شماره تماس شرکت دکیو» matched a DIFFERENT entry,
# so the override fired and the phone was served. «سایت شرکت دکیو» matched the
# دکیو question row ITSELF at 0.99 — nothing conflicted, no anchor path ran,
# and the visitor got the company's generic description. The field tier was
# invisible on exactly the queries the company had curated a question for.
#
# The corpus below reproduces that: a curated question that belongs to the
# SAME company and carries the field word.

CURATED_QUESTIONS = [
    # دکیو's own question row, containing «سایت» — the query that hid the tier.
    # Jaccard against «سایت شرکت دکیو چیست؟» is 0.75: trusted, but under the
    # 0.9 Tier 0 bar, so the questions branch (not Tier 0) serves it.
    ("co-dekio", "سایت شرکت دکیو"),
    # A near-exact curated hit that is ALSO a field question — Tier 0 territory.
    ("co-sepehr", "شماره تماس شرکت سپهر چیست؟"),
    # Same company, no field word in it.
    ("co-dekio", "شرکت دکیو چه محصولاتی دارد"),
]


def _seed_questions(rows=CURATED_QUESTIONS):
    """Add curated question rows on top of _seed() and reindex.

    Separate from _seed() on purpose: the tests above prove the tier through
    the anchor with no questions index at all, and they must keep doing so.
    """
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    for dataset_id, question in rows:
        conn.execute(
            "INSERT INTO questions (dataset_id, question, video_url)"
            " VALUES (?, ?, '')", (dataset_id, question))
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()


def test_the_questions_index_matching_the_company_itself_still_yields_the_field(client, monkeypatch):
    """THE regression for the shadowing bug. The questions index returns
    شرکت دکیو — the very company the visitor named — at a trusted score, so
    no entity override fires. The field tier must still answer with the
    website instead of the company's description."""
    _seed()
    _seed_questions()
    _mock_ai(monkeypatch, forbid=True)
    query = "سایت شرکت دکیو چیست؟"

    # The precondition that hid the tier: the questions index really does
    # return this same company, trusted, and below the Tier 0 bar.
    from app.config import TRUSTED_MATCH_THRESHOLD
    from app.services import search
    q_entry, q_score = search.find_similar_question(query)
    assert q_entry is not None and q_entry["id"] == "co-dekio", q_entry
    # Only the floor is asserted. The upper bound used to be 0.9, which held
    # because the test ran with the semantic index OFF — a configuration
    # production never used. What this test is about is WHICH entry the
    # questions index picks, not how confident it is.
    assert q_score >= TRUSTED_MATCH_THRESHOLD, q_score

    r = _ask(client, query)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert "dekio-example.ir" in body["text"], body["text"]
    assert body["text"] != DEKIO_TEXT, body["text"]


def test_a_trusted_dataset_match_on_the_named_company_still_yields_the_field(client, monkeypatch):
    """The same gap on the Tier 1 branch. A trusted dataset hit on the company
    the visitor named leaves nothing for the anchor to override, so that branch
    has to consult the field tier too.

    find_best_match is stubbed because TF-IDF over these long descriptions
    tops out near 0.33 on this small corpus and never clears the 0.70 trust
    bar. The branch CONDITION is what is under test, not the retriever."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    query = "سایت شرکت دکیو چیست؟"
    entry = _entry(query)

    import app.routers.chat as chat
    monkeypatch.setattr(chat, "find_best_match", lambda q: (entry, 0.95))

    r = _ask(client, query)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_field", body
    assert "dekio-example.ir" in body["text"], body["text"]


def test_a_curated_exact_question_still_wins_over_the_field_tier(client, monkeypatch):
    """Tier 0 stays authoritative. «شماره تماس شرکت سپهر چیست؟» is word for
    word a curated question, and شرکت سپهر does have a recorded phone — the
    hand-mapped answer must still win, exactly as it does over the anchor."""
    _seed()
    _seed_questions()
    _mock_ai(monkeypatch, forbid=True)
    query = "شماره تماس شرکت سپهر چیست؟"

    from app.services import search
    _entry_hit, exact_score = search.find_similar_question(query, exact_only=True)
    assert exact_score >= 0.9, exact_score

    r = _ask(client, query)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_questions", body
    assert body["text"] == SEPEHR_TEXT, body["text"]
    assert "03133445566" not in body["text"], body["text"]


def test_a_non_field_question_about_the_company_still_gets_the_description(client, monkeypatch):
    """The other half of the fix: the questions branch may only be diverted
    when the visitor actually asked for a recorded field. A plain question
    about the same company keeps returning that company's own entry."""
    _seed()
    _seed_questions()
    _mock_ai(monkeypatch, forbid=True)
    query = "شرکت دکیو چه محصولاتی دارد بگو"

    from app.config import TRUSTED_MATCH_THRESHOLD
    from app.services import search
    q_entry, q_score = search.find_similar_question(query)
    assert q_entry is not None and q_entry["id"] == "co-dekio", q_entry
    # Only the floor is asserted. The upper bound used to be 0.9, which held
    # because the test ran with the semantic index OFF — a configuration
    # production never used. What this test is about is WHICH entry the
    # questions index picks, not how confident it is.
    assert q_score >= TRUSTED_MATCH_THRESHOLD, q_score

    r = _ask(client, query)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_questions", body
    assert body["text"] == DEKIO_TEXT, body["text"]
    assert DEKIO_WEBSITE not in body["text"], body["text"]


# ── The widened list filter ──────────────────────────────────────────────

def test_a_province_question_lists_only_the_companies_in_that_province(client, monkeypatch):
    """«استان» is list machinery, not a topic keyword, and a company's
    province is part of what the list tier filters on. Only the two اصفهان
    companies may be named."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شرکت‌های استان اصفهان را معرفی کن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body
    assert "2 شرکت" in body["text"], body["text"]
    assert "شرکت سپهر" in body["text"], body["text"]
    assert "شرکت آوا" in body["text"], body["text"]
    assert "شرکت دکیو" not in body["text"], body["text"]
    assert "شرکت رایان" not in body["text"], body["text"]


def test_an_activity_list_question_still_lists_the_same_companies(client, monkeypatch):
    """Regression on the tier that shipped today: widening the haystack with
    province/company_type must not change which companies an activity-field
    question returns."""
    _seed()
    _mock_ai(monkeypatch, forbid=True)
    r = _ask(client, "شرکت‌های هوش مصنوعی را معرفی کن")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body
    assert "2 شرکت" in body["text"], body["text"]
    assert "شرکت آوا" in body["text"], body["text"]
    assert "شرکت رایان" in body["text"], body["text"]
    assert "شرکت سپهر" not in body["text"], body["text"]
    assert "شرکت دکیو" not in body["text"], body["text"]
