"""Taxonomy loader: the file is the contract, and a bad file must not ship.

This exists because the taxonomy will be replaced by the client's own file.
Everything here answers one question: what happens to a running exhibition
kiosk when that file arrives malformed?
"""
import json

import pytest

from app.services import taxonomy


@pytest.fixture
def temp_taxonomy(tmp_path, monkeypatch):
    """Point the loader at a scratch file and reset its cache."""
    path = tmp_path / "tax.json"

    def write(doc):
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        # Reset the module cache so each write is a fresh load.
        monkeypatch.setattr(taxonomy, "_mtime", -1.0, raising=False)
        return path

    monkeypatch.setattr(taxonomy, "TAXONOMY_PATH", str(path))
    monkeypatch.setattr(taxonomy, "_doc", taxonomy._MINIMUM, raising=False)
    monkeypatch.setattr(taxonomy, "_mtime", -1.0, raising=False)
    monkeypatch.setattr(taxonomy, "_loaded_once", False, raising=False)
    return write


def minimal_doc(**over):
    doc = {
        "version": "test-1",
        "jobs": [{"id": "student", "fa": "دانشجو", "en": "Student"}],
        "interests": [{"id": "ai", "fa": "هوش مصنوعی", "en": "AI"}],
        "flags": [{"id": "learn", "fa": "علاقه به یادگیری", "en": "Wants to learn"}],
        "fallback_ids": ["stage"],
        "sections": [{
            "id": "stage", "fa": "استیج", "en": "Stage",
            "keywords": ["سخنرانی"], "why_fa": "چون…", "why_en": "Because…",
        }],
    }
    doc.update(over)
    return doc


# ── The shipped file ─────────────────────────────────────────────────────

def test_the_shipped_taxonomy_is_valid():
    """The file in the repo must actually load — not silently fall back."""
    doc = taxonomy.document()
    assert doc["version"] != "builtin-minimum", "shipped taxonomy failed to load"
    assert doc["sections"] and doc["jobs"] and doc["interests"]


def test_shipped_fallback_ids_all_exist():
    doc = taxonomy.document()
    known = {s["id"] for s in doc["sections"]}
    assert set(doc["fallback_ids"]) <= known


# ── Loading and hot reload ───────────────────────────────────────────────

def test_loads_a_valid_file(temp_taxonomy):
    temp_taxonomy(minimal_doc())
    doc = taxonomy.document()
    assert doc["version"] == "test-1"
    assert [s["id"] for s in doc["sections"]] == ["stage"]


def test_edits_apply_without_a_restart(temp_taxonomy):
    temp_taxonomy(minimal_doc())
    assert taxonomy.document()["version"] == "test-1"
    temp_taxonomy(minimal_doc(version="test-2"))
    assert taxonomy.document()["version"] == "test-2"


def test_form_options_are_localised(temp_taxonomy):
    temp_taxonomy(minimal_doc())
    fa = taxonomy.form_options("fa")
    en = taxonomy.form_options("en")
    assert fa["jobs"][0]["label"] == "دانشجو"
    assert en["jobs"][0]["label"] == "Student"
    assert fa["flags"][0]["id"] == "learn"


def test_positions_are_served_when_present(temp_taxonomy):
    temp_taxonomy(minimal_doc(positions=[{"id": "lead", "fa": "سرپرست", "en": "Lead"}]))
    assert taxonomy.form_options("fa")["positions"][0]["label"] == "سرپرست"


def test_positions_are_optional(temp_taxonomy):
    """A taxonomy with no positions must still load — the form falls back to
    a free-text سمت field rather than an empty dropdown."""
    temp_taxonomy(minimal_doc())
    assert taxonomy.form_options("fa")["positions"] == []


def test_the_shipped_taxonomy_offers_positions():
    assert taxonomy.form_options("fa")["positions"], "سمت dropdown would be empty"


# ── Bad files must not reach the product ─────────────────────────────────

def test_broken_json_keeps_the_previous_taxonomy(temp_taxonomy, tmp_path):
    path = temp_taxonomy(minimal_doc())
    assert taxonomy.document()["version"] == "test-1"

    path.write_text("{ this is not json", encoding="utf-8")
    taxonomy._mtime = -1.0
    assert taxonomy.document()["version"] == "test-1", "a broken edit took the taxonomy down"


def test_a_file_with_no_usable_sections_is_refused(temp_taxonomy):
    temp_taxonomy(minimal_doc())
    assert taxonomy.document()["version"] == "test-1"  # load the good one first
    temp_taxonomy(minimal_doc(version="empty", sections=[]))
    assert taxonomy.document()["version"] == "test-1"


def test_a_section_without_keywords_is_skipped_not_fatal(temp_taxonomy):
    temp_taxonomy(minimal_doc(sections=[
        {"id": "stage", "fa": "استیج", "en": "Stage", "keywords": ["سخنرانی"]},
        {"id": "broken", "fa": "خراب", "en": "Broken", "keywords": []},
    ]))
    assert [s["id"] for s in taxonomy.sections()] == ["stage"]


def test_one_malformed_job_does_not_empty_the_dropdown(temp_taxonomy):
    temp_taxonomy(minimal_doc(jobs=[
        {"id": "student", "fa": "دانشجو"},
        {"id": "", "fa": "بی‌شناسه"},
        {"nope": True},
    ]))
    assert [j["id"] for j in taxonomy.document()["jobs"]] == ["student"]


def test_duplicate_ids_are_dropped(temp_taxonomy):
    temp_taxonomy(minimal_doc(interests=[
        {"id": "ai", "fa": "هوش مصنوعی"},
        {"id": "ai", "fa": "تکراری"},
    ]))
    assert len(taxonomy.document()["interests"]) == 1


def test_unknown_fallback_ids_are_replaced_with_a_real_one(temp_taxonomy):
    temp_taxonomy(minimal_doc(fallback_ids=["does-not-exist"]))
    assert taxonomy.fallback_ids() == ["stage"]


def test_missing_file_serves_the_minimum_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(taxonomy, "TAXONOMY_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(taxonomy, "_doc", taxonomy._MINIMUM, raising=False)
    monkeypatch.setattr(taxonomy, "_mtime", -1.0, raising=False)
    monkeypatch.setattr(taxonomy, "_loaded_once", False, raising=False)
    doc = taxonomy.document()
    assert doc["version"] == "builtin-minimum"
    assert doc["sections"] == []


# ── Interest synonym expansion ───────────────────────────────────────────

def test_selected_interest_pulls_in_its_extra_keywords(temp_taxonomy):
    temp_taxonomy(minimal_doc(interests=[
        {"id": "iot", "fa": "اینترنت اشیا (IoT)", "en": "IoT", "keywords": ["حسگر"]},
    ]))
    out = taxonomy.expand_interests("اینترنت اشیا (IoT)")
    assert "حسگر" in out


def test_expansion_leaves_free_text_alone(temp_taxonomy):
    temp_taxonomy(minimal_doc())
    assert taxonomy.expand_interests("چیزی که در فهرست نیست") == "چیزی که در فهرست نیست"
    assert taxonomy.expand_interests("") == ""


# ── Swapping the taxonomy changes the plan, not the code ─────────────────

def test_replacing_the_file_changes_what_the_planner_recommends(temp_taxonomy):
    from app.services import visit_plan

    temp_taxonomy(minimal_doc(sections=[{
        "id": "only-one", "fa": "تنها بخش", "en": "Only section",
        "keywords": ["نقاشی"], "why_fa": "چون…", "why_en": "Because…",
    }], fallback_ids=["only-one"]))

    plan = visit_plan.recommend({"interests": "نقاشی"})
    assert [s["id"] for s in plan["sections"]] == ["only-one"]
    assert plan["matched"] is True
