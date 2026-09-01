"""Hall lists, booth lookup, and the بانک-style facet stem.

THREE live failures (Elecomp, 2026-09-01) fixed together because they share
one root: facts the organizer already records per company — `hall`
(«سالن ۶», «سالن ۳۸B», «میلاد (31B)») and `booth_number` («377», «6-10») —
had no tier that read them.

  «شرکت های سالن 6»  -> refused: no hall dimension anywhere in the pipeline.
  «غرفه 377»          -> out of scope: nothing looks a booth number up.
  «چه بانک هایی هستن تو نمایشگاه» -> «اطلاعات دقیقی ندارم»: the visitor's
      word «بانک» never matched the facet token «بانکداری» — suffix
      derivation, one closed rule added at facet matching time.

The tests call the SERVICES directly, the way tests/test_guide_service.py
does: the chat-router wiring is a separate change and is not exercised here.
"""
import json

import pytest
from fastapi.testclient import TestClient


# Six companies across the three hall label shapes the site really uses,
# plus one company with no hall and no booth — a row that must never appear
# in any hall or booth answer. Titles avoid the words سالن/غرفه on purpose
# so a title token can never satisfy the hall/booth detection by itself.
# (id, title, text, activity_field, hall, booth_number)
COMPANIES = [
    ("co-ava", "شرکت آوا", "معرفی شرکت آوا: فعال در هوش مصنوعی و پردازش تصویر.",
     "هوش مصنوعی", "سالن ۶", "377"),
    ("co-rayan", "شرکت رایان", "شرکت رایان سامانه های هوش مصنوعی می سازد.",
     "هوش مصنوعی", "سالن ۶", "6-10"),
    ("co-rgb", "شرکت ربات ساز", "شرکت ربات ساز ربات های صنعتی می سازد.",
     "رباتیک", "سالن ۶", "6-12"),
    ("co-negar", "شرکت نگار", "شرکت نگار خدمات بانکداری و سامانه های بانک ارائه می کند.",
     "بانکداری", "سالن ۳۸B", "38-1"),
    ("co-milad", "شرکت کوشا", "شرکت کوشا در حوزه آموزش فعال است.",
     "آموزش", "میلاد (31B)", "31-2"),
    ("co-none", "شرکت نیلوفر", "شرکت نیلوفر نرم افزار حسابداری می سازد.",
     "نرم افزار", "", ""),
]

# Same rows with the hall/booth columns empty: the shape of an install whose
# import never carried them — both new tiers must switch off, not error.
COMPANIES_BARE = [
    (cid, title, text, field, "", "")
    for cid, title, text, field, _hall, _booth in COMPANIES
]

# One FAQ row so search._corpus_vocab is deterministic for THIS file. The
# vocab is module-global and survives across test files in one pytest run,
# and the fuzzy corrector only fires on words the corpus has never seen —
# without this row, «هستن» is unknown whenever this file runs alone and
# known-to-the-other-file's-dataset (or not) when it runs after others,
# which made the بانک test order-dependent. Seeding our own text pins it.
FAQ = ("faq-chatty", "سوال رایج",
       "در نمایشگاه چه شرکت هایی هستن و غرفه هر شرکت تو کدام سالن است.")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway database with the real schema, built by init_db() — same
    shape as tests/test_guide_service.py: the companies table must come out
    of the ordinary SQLite mirror (hall/booth_number included), not a private
    schema path."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "hall.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    yield


def _seed(monkeypatch, companies=COMPANIES):
    """Fill companies (+ one dataset row for shape) and pin the corpus vocab.

    The vocab drives the fuzzy corrector's "has the corpus ever seen this
    word" test and is module-global state that survives across test files,
    so it is pinned to the FAQ text — the same tokens load_dataset_internal
    would derive from it. load_dataset_internal itself cannot run on this
    box: it also builds the model2vec embedding indexes, which stalls on the
    model download offline (one of the env-only failures CI owns)."""
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    for table in ("dataset", "companies", "questions", "synonyms"):
        conn.execute(f"DELETE FROM {table}")
    cid, title, text = FAQ
    conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                 " VALUES (?, ?, ?, '')", (cid, title, text))
    for cid, title, text, field, hall, booth in companies:
        conn.execute(
            "INSERT INTO companies (id, title, text, video_url,"
            " activity_field, hall, booth_number)"
            " VALUES (?, ?, ?, '', ?, ?, ?)",
            (cid, title, text, field, hall, booth))
    conn.commit()
    conn.close()

    from app.services import search
    from app.utils.normalizer import normalize_persian
    monkeypatch.setattr(search, "_corpus_vocab",
                        set(normalize_persian(f"{FAQ[1]} {FAQ[2]}").split()))


def _hall(query):
    from app.services.company_search import answer_hall_list
    return answer_hall_list(query, lang="fa")


def _booth(query):
    from app.services.company_search import answer_booth_lookup
    return answer_booth_lookup(query, lang="fa")


_LIST_KEYS = {"text", "count", "matched_ids", "displayed_ids", "options",
              "offer_state", "keywords", "filter_label"}
_BOOTH_KEYS = {"text", "field", "label", "value", "confidence",
               "company_id", "title", "video_url"}


# ── The hall list ─────────────────────────────────────────────────────────


def test_hall_question_lists_only_that_halls_companies(db, monkeypatch):
    """The measured failure: «شرکت های سالن 6» (Latin digit) was REFUSED.
    Now the hall's companies are listed, the head names the hall in the
    organizer's own spelling, and no other hall's company appears."""
    _seed(monkeypatch)
    r = _hall("شرکت های سالن 6")
    assert r is not None
    assert set(r) == _LIST_KEYS
    assert r["count"] == 3
    assert set(r["matched_ids"]) == {"co-ava", "co-rayan", "co-rgb"}
    assert r["text"].splitlines()[0] == "شرکت‌های سالن ۶:"
    for gone in ("شرکت نگار", "شرکت کوشا", "شرکت نیلوفر"):
        assert gone not in r["text"], r["text"]
    # The pager payload: «بیشتر» and a tapped «۳» resolve against these ids,
    # produced by the same renderer that printed the names.
    state = json.loads(r["offer_state"])
    assert state["filter"] == "سالن ۶"
    assert set(state["ids"]) >= {"co-ava", "co-rayan", "co-rgb"}


def test_hall_question_with_persian_digits_is_the_same_list(db, monkeypatch):
    """«سالن ۶» typed with the Persian digit must reach the hall stored as
    «سالن ۶» exactly as the Latin-digit query does — digits fold on both
    sides before they are compared."""
    _seed(monkeypatch)
    r = _hall("شرکت های سالن ۶")
    assert r is not None
    assert set(r["matched_ids"]) == {"co-ava", "co-rayan", "co-rgb"}


def test_milad_word_matches_the_milad_hall(db, monkeypatch):
    """«میلاد (31B)» carries a site code the visitor never types; they say
    «میلاد پایین». The label's own word anchors the match — and پایین only
    prefers, never rejects, when the install has the one میلاد hall."""
    _seed(monkeypatch)
    r = _hall("شرکت‌های میلاد پایین")
    assert r is not None
    assert r["matched_ids"] == ["co-milad"]
    assert r["text"].splitlines()[0] == "شرکت‌های میلاد (31B):"


def test_unknown_hall_returns_none(db, monkeypatch):
    """«سالن ۹» names a hall no recorded company sits in — defer to the
    pipeline rather than invent an empty list."""
    _seed(monkeypatch)
    assert _hall("سالن ۹") is None
    assert _hall("شرکت های سالن 99") is None


def test_open_area_words_match_the_open_air_hall():
    """«محوطه» is how visitors say «فضای باز». Unit-level: the matcher maps
    the visitor's word onto the recorded label without any DB."""
    from app.services.company_search import _match_hall
    halls = {"فضای باز": None}
    assert _match_hall(["شرکت", "های", "محوطه"], halls) == "فضای باز"
    assert _match_hall(["شرکت", "های", "فضای", "باز"], halls) == "فضای باز"
    assert _match_hall(["سالن", "6"], halls) is None


# ── The booth lookup ──────────────────────────────────────────────────────


def test_booth_word_finds_the_company_at_that_booth(db, monkeypatch):
    """The measured failure: «غرفه 377» went out of scope. Now the company
    at that booth answers, with the contract the company-field tier set
    (text/field/label/value) plus the deterministic-tier confidence."""
    _seed(monkeypatch)
    r = _booth("غرفه 377")
    assert r is not None
    assert set(r) == _BOOTH_KEYS
    assert r["field"] == "booth_number"
    assert r["value"] == "377"
    assert r["confidence"] == 0.95
    assert r["company_id"] == "co-ava"
    assert r["text"].splitlines()[0] == "غرفه 377: شرکت آوا"
    # The public company text rides along: the router serves this as that
    # company's answer, not as a bare number-to-name mapping.
    assert "هوش مصنوعی" in r["text"]


def test_bare_number_finds_the_same_company(db, monkeypatch):
    """A bare all-digit query is booth-shaped the moment the router sends it
    here — the function answers it identically to «غرفه 377»."""
    _seed(monkeypatch)
    r = _booth("377")
    assert r is not None and r["company_id"] == "co-ava"
    r_fa = _booth("۳۷۷")
    assert r_fa is not None and r_fa["company_id"] == "co-ava"


def test_hyphenated_booth_is_found_after_normalization_splits_it(db, monkeypatch):
    """normalize_persian turns «6-10» into the two tokens «6» «10»; the
    recorded value keeps the hyphen. Digit-only folding on both sides joins
    them back without ever making «37» equal «377»."""
    _seed(monkeypatch)
    r = _booth("غرفه 6-10")
    assert r is not None and r["company_id"] == "co-rayan" and r["value"] == "6-10"


def test_booth_prefix_never_matches_a_longer_number(db, monkeypatch):
    """«37» must not match «377» — whole-string equality, not a prefix."""
    _seed(monkeypatch)
    assert _booth("37") is None
    assert _booth("غرفه 37") is None


def test_unknown_booth_returns_none(db, monkeypatch):
    _seed(monkeypatch)
    assert _booth("غرفه 9999") is None
    # A query with no booth token at all is not this tier's to answer.
    assert _booth("شرکت دکیو چیست") is None


# ── The بانک stem, and the regression around it ──────────────────────────


def test_bank_query_now_matches_the_banking_facet(db, monkeypatch):
    """The measured failure: «چه بانک هایی هستن تو نمایشگاه» — the word
    «بانک» never matched the facet token «بانکداری». One suffix rule and
    the list contains the banking company, nobody else."""
    _seed(monkeypatch)
    from app.services.company_search import answer_company_list
    r = answer_company_list("چه بانک هایی هستن تو نمایشگاه", lang="fa")
    assert r is not None
    assert r["matched_ids"] == ["co-negar"]


def test_single_distinct_facet_word_behavior_is_unchanged(db, monkeypatch):
    """Regression guard for the stem: «رباتیک» — one word, one facet, exact
    match — must still select exactly that field, exactly as before."""
    _seed(monkeypatch)
    from app.services.company_search import answer_company_list
    r = answer_company_list("شرکت های رباتیک", lang="fa")
    assert r is not None
    assert r["matched_ids"] == ["co-rgb"]


def test_stem_rule_shapes():
    """The rule itself, unit-level: facet = query + one closed suffix, bases
    under three letters never stem, and the direction never reverses."""
    from app.services.company_search import _stem_hit
    assert _stem_hit("بانکداری", {"بانک"})
    assert _stem_hit("بانکها", {"بانک"})
    assert _stem_hit("تیغسازی", {"تیغ"})
    assert not _stem_hit("بانکک", {"بانک"})
    assert not _stem_hit("آبها", {"آب"})          # two-letter base never stems
    assert not _stem_hit("بانک", {"بانکداری"})    # facet grows, query does not


# ── Tier inertness ────────────────────────────────────────────────────────


def test_without_hall_or_booth_values_both_tiers_return_none(db, monkeypatch):
    """An install whose import never carried hall/booth columns' values: both
    tiers switch off — no exception, normal pipeline."""
    _seed(monkeypatch, COMPANIES_BARE)
    assert _hall("شرکت های سالن 6") is None
    assert _booth("غرفه 377") is None


# ── Router wiring: the three live failures, end to end ───────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Same client shape as tests/test_conversational.py."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "hallbooth.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        # The corpus vocabulary builds at app boot, BEFORE _seed() inserts
        # the companies — without this the unknown-entity gate flags the
        # very words the fixture is about (بانک), and the tier never runs.
        from app.services.search import reindex_and_publish
        reindex_and_publish()
        yield c
    security._chat_rate_limits.clear()


def _ask(client, message):
    return client.post("/chat", json={"message": message, "lang": "fa"})


def _seed_and_index(monkeypatch):
    """Seed, THEN rebuild the corpus vocabulary — the unknown-entity gate
    reads the vocab, and it was last built at app boot, before the rows
    existed. Every e2e test below needs both, in this order."""
    _seed(monkeypatch)
    from app.services.search import reindex_and_publish
    reindex_and_publish()


def test_hall_question_served_through_chat(db, client, monkeypatch):
    _seed_and_index(monkeypatch)
    r = _ask(client, "شرکت های سالن 6")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body
    # COMPANIES rows are tuples: (id, title, text, activity, hall, booth)
    hall6 = [c for c in COMPANIES if c[4] == "سالن ۶"]
    for c in hall6:
        assert c[1] in body["text"]


def test_booth_question_served_through_chat(db, client, monkeypatch):
    _seed_and_index(monkeypatch)
    r = _ask(client, "غرفه 377")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_booth", body
    at_377 = [c for c in COMPANIES if c[5] == "377"]
    assert at_377 and at_377[0][1] in body["text"]


def test_bare_number_with_no_offer_finds_the_booth(db, client, monkeypatch):
    _seed_and_index(monkeypatch)
    r = _ask(client, "377")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "local_booth", r.text


def test_bank_word_lists_the_bankdari_facet(db, client, monkeypatch):
    _seed_and_index(monkeypatch)
    r = _ask(client, "چه بانک هایی هستن تو نمایشگاه")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_company_search", body
    banks = [c for c in COMPANIES if "بانکداری" in (c[3] or "")]
    for c in banks:
        assert c[1] in body["text"]
