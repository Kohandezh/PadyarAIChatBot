"""Targeted-visit planner: matching, honesty, and the two entry points.

The honesty tests matter as much as the ranking ones: the planner's whole
premise is that it recommends only sections the official site actually
documents, so "never names an exhibitor" is a contract, not a detail.
"""
import pytest

from app.services import taxonomy, visit_plan


def ids(plan):
    return [s["id"] for s in plan["sections"]]


def matched_ids(plan):
    """Only the sections the profile actually matched."""
    return [s["id"] for s in plan["sections"] if not s["general"]]


# ── Matching ─────────────────────────────────────────────────────────────

def test_ai_profile_reaches_the_ai_conference():
    plan = visit_plan.recommend({"job": "مهندس نرم‌افزار", "interests": "هوش مصنوعی و یادگیری ماشین"})
    assert plan["matched"] is True
    assert "ai-iot-conference" in ids(plan)


def test_investor_profile_reaches_the_investment_sections():
    plan = visit_plan.recommend({"job": "سرمایه‌گذار", "interests": "جذب سرمایه و شتابدهنده"})
    assert plan["matched"] is True
    assert {"capital-cafe", "investors-pavilion"} & set(ids(plan))


def test_student_looking_for_work_reaches_the_job_station():
    plan = visit_plan.recommend({"job": "دانشجو", "interests": "کارآموزی و استخدام"})
    assert "job-station" in ids(plan)


def test_english_profile_matches_and_answers_in_english():
    plan = visit_plan.recommend({"job": "journalist", "interests": "media, press"}, lang="en")
    assert "media-hub" in ids(plan)
    assert plan["sections"][0]["title"].isascii()
    assert "exhibitor" in plan["note"]


def test_zwnj_spelling_matches_the_same_as_the_spaced_one():
    """«هوش‌مصنوعی» and «هوش مصنوعی» must score identically."""
    a = visit_plan.recommend({"interests": "هوش‌مصنوعی"})
    b = visit_plan.recommend({"interests": "هوش مصنوعی"})
    assert ids(a) == ids(b)
    assert a["sections"][0]["score"] == b["sections"][0]["score"]


def test_one_mention_is_not_counted_twice_through_synonyms():
    """A single mention must not saturate the score via spelling variants.

    The taxonomy lists «هوش مصنوعی» and «هوش‌مصنوعی» separately; both normalise
    to one string, so one mention is one hit.
    """
    one = visit_plan.recommend({"interests": "هوش مصنوعی"})
    two = visit_plan.recommend({"interests": "هوش مصنوعی و اینترنت اشیا"})
    top_one = one["sections"][0]
    top_two = two["sections"][0]
    assert top_one["id"] == top_two["id"] == "ai-iot-conference"
    assert top_two["score"] > top_one["score"]


def test_db_synonyms_do_not_inflate_the_score(monkeypatch):
    """The planner must not run the retriever's synonym table over its keywords.

    Regression: a synonym row expands «هوش مصنوعی» into a multi-word string.
    Because both the keyword and the visitor's text went through that
    expansion, one mention matched twice and saturated the score.
    """
    import app.utils.normalizer as normalizer
    monkeypatch.setattr(normalizer, "active_synonyms",
                        [("هوش مصنوعی", "هوش‌مصنوعی ai هوش")])
    monkeypatch.setattr(visit_plan, "_cache_doc", None)  # force a keyword rebuild

    one = visit_plan.recommend({"interests": "هوش مصنوعی"})
    two = visit_plan.recommend({"interests": "هوش مصنوعی و اینترنت اشیا"})
    assert one["sections"][0]["score"] < two["sections"][0]["score"]


def test_latin_stem_does_not_match_inside_another_word():
    """"email marketing" is not an AI profile just because "email" holds "ai"."""
    plan = visit_plan.recommend({"job": "email marketing specialist"})
    assert "ai-iot-conference" not in matched_ids(plan)


def test_field_without_its_own_track_lands_in_the_product_showcase():
    """Blockchain has no track in the official programme — it must not vanish.

    The honest home is the general technology-product section, not an invented
    blockchain pavilion.
    """
    plan = visit_plan.recommend({"interests": "فین‌تک و بلاکچین"})
    assert plan["matched"] is True
    assert "pioneers-festival" in matched_ids(plan)
    # And the finance side of fintech still reaches the capital sections.
    assert "capital-cafe" in matched_ids(plan)


def test_persian_suffix_still_matches_the_stem():
    """«استارتاپی» must find «استارتاپ»."""
    plan = visit_plan.recommend({"job": "بنیان‌گذار یک کسب‌وکار استارتاپی"})
    assert "pitch-battle" in ids(plan)


def test_ranking_is_deterministic_for_the_same_profile():
    profile = {"job": "مشاور حقوقی", "interests": "قانون و مقررات فناوری"}
    assert ids(visit_plan.recommend(profile)) == ids(visit_plan.recommend(profile))


def test_plan_is_capped():
    plan = visit_plan.recommend({
        "job": "سرمایه‌گذار و مشاور",
        "position": "خبرنگار",
        "interests": "هوش مصنوعی، استارتاپ، صنعت، حکمرانی، استخدام، رسانه",
    })
    assert len(plan["sections"]) <= visit_plan.MAX_RESULTS


# ── Honesty ──────────────────────────────────────────────────────────────

def test_empty_profile_returns_a_generic_plan_not_a_fake_match():
    plan = visit_plan.recommend({})
    assert plan["matched"] is False
    assert ids(plan) == taxonomy.fallback_ids()
    # No fabricated justification for a match that never happened.
    assert all(s["general"] is True for s in plan["sections"])
    assert all(s["why"] == "" for s in plan["sections"])
    assert all(s["score"] == 0.0 for s in plan["sections"])


def test_a_thin_plan_is_topped_up_with_clearly_general_sections():
    """One match is a correct answer but a poor plan — top it up honestly."""
    plan = visit_plan.recommend({"interests": "رسانه"})
    assert len(matched_ids(plan)) == 1
    assert len(plan["sections"]) >= visit_plan.MIN_PLAN
    for s in plan["sections"]:
        # Every topped-up section is marked general and justifies nothing.
        assert s["general"] == (s["score"] == 0.0)
        if s["general"]:
            assert s["why"] == ""


def test_top_up_never_duplicates_a_matched_section():
    plan = visit_plan.recommend({"interests": "نوآوری"})  # matches pioneers-festival
    assert len(ids(plan)) == len(set(ids(plan)))


def test_a_full_plan_is_not_padded():
    plan = visit_plan.recommend({
        "job": "سرمایه‌گذار و مشاور حقوقی",
        "interests": "هوش مصنوعی، استارتاپ",
    })
    assert len(matched_ids(plan)) >= visit_plan.MIN_PLAN
    assert all(s["general"] is False for s in plan["sections"])


def test_unrelated_profile_still_gets_a_usable_plan():
    plan = visit_plan.recommend({"job": "قناد"})
    assert plan["sections"], "the planner must never return an empty list"


def test_every_recommended_section_exists_in_the_taxonomy():
    """The planner may only return sections verified from the official site."""
    known = {s["id"] for s in visit_plan.sections()}
    for profile in ({}, {"job": "سرمایه‌گذار"}, {"interests": "هوش مصنوعی"}, {"job": "قناد"}):
        assert set(ids(visit_plan.recommend(profile))) <= known


def test_note_always_states_the_exhibitor_directory_is_not_published():
    for lang, marker in (("fa", "غرفه‌داران"), ("en", "exhibitor")):
        plan = visit_plan.recommend({"interests": "هوش مصنوعی"}, lang=lang)
        assert marker in plan["note"]


def test_plan_text_is_empty_without_a_real_match():
    """No personalised paragraph for a profile that said nothing."""
    assert visit_plan.plan_text({}) == ""
    assert visit_plan.plan_text({"job": "", "position": "", "interests": ""}) == ""


def test_plan_text_lists_sections_and_the_note():
    text = visit_plan.plan_text({"interests": "هوش مصنوعی"})
    assert "همایش ملی هوش مصنوعی" in text
    assert "غرفه‌داران" in text


def test_plan_text_separates_general_sections_from_matches():
    """A general suggestion must never be printed with a matched section's reason."""
    text = visit_plan.plan_text({"interests": "رسانه"})
    assert "مدیا هاب — چون در حوزهٔ رسانه و محتوا فعالید." in text
    assert "و اگر وقت داشتید" in text
    # The topped-up entry appears as a bare title, with no invented reason.
    assert "• استیج اینوتکس\n" in text or text.endswith("• استیج اینوتکس")
    assert "استیج اینوتکس —" not in text


# ── HTTP surface ─────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_endpoint_returns_a_plan_from_raw_fields(client):
    r = client.post("/api/visit-plan", json={"interests": "هوش مصنوعی", "lang": "fa"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] is True
    assert "ai-iot-conference" in [s["id"] for s in body["sections"]]


def test_endpoint_works_without_registering(client):
    """The planner must not require an identity to be useful."""
    r = client.post("/api/visit-plan", json={})
    assert r.status_code == 200
    assert r.json()["sections"]


def test_endpoint_ignores_an_unverified_challenge_id(client):
    """An unknown id must not error, and must not leak anything."""
    r = client.post("/api/visit-plan", json={"challenge_id": "x" * 32, "interests": "رسانه"})
    assert r.status_code == 200
    body = r.json()
    # Falls back to the body's own fields rather than failing.
    assert "media-hub" in [s["id"] for s in body["sections"]]
    assert "destination" not in r.text and "phone" not in r.text


def test_endpoint_caps_oversized_input(client):
    r = client.post("/api/visit-plan", json={"interests": "x" * 5000})
    assert r.status_code == 422


# ── Chat integration ─────────────────────────────────────────────────────

@pytest.fixture
def answer(monkeypatch):
    """`_answer_from_entry` with logging stubbed, returning the response text."""
    from app.routers import chat as chat_router
    monkeypatch.setattr(chat_router, "log_chat", lambda *a, **k: None)

    def build(entry, visitor=None, lang="fa"):
        return chat_router._answer_from_entry(
            entry, 1.0, "local", "q", lang=lang, visitor=visitor
        ).text
    return build


@pytest.fixture
def targeted_entry():
    return {"id": "inotex-targeted-visit", "text": "پایه", "text_en": "base", "video_url": ""}


def test_targeted_answer_is_personalised_for_a_described_visitor(answer, targeted_entry):
    from app.models import VisitorProfile
    text = answer(targeted_entry, VisitorProfile(interests="هوش مصنوعی"))
    assert text.startswith("پایه")
    assert "همایش ملی هوش مصنوعی" in text


def test_targeted_answer_is_untouched_without_a_profile(answer, targeted_entry):
    from app.models import VisitorProfile
    assert answer(targeted_entry) == "پایه"
    assert answer(targeted_entry, VisitorProfile()) == "پایه"


def test_other_entries_are_never_personalised(answer):
    from app.models import VisitorProfile
    entry = {"id": "inotex-overview", "text": "پایه", "video_url": ""}
    assert answer(entry, VisitorProfile(interests="هوش مصنوعی")) == "پایه"


def test_planner_failure_does_not_lose_the_answer(answer, targeted_entry, monkeypatch):
    from app.models import VisitorProfile
    from app.services import visit_plan as planner
    monkeypatch.setattr(
        planner, "plan_text",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert answer(targeted_entry, VisitorProfile(interests="هوش مصنوعی")) == "پایه"
