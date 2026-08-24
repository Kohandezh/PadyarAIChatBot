"""The bundled INOTEX knowledge seed: shape, provenance and safety.

These tests protect three contracts:
1. Every seeded fact-record is bilingual and carries its official source URL.
2. The seed only ever runs on an empty database (customer content is sacred).
3. No legacy-event identity can ride along inside the seed.
"""
import sqlite3

import pytest

from app.default_content import (
    INOTEX_DATASET,
    INOTEX_QUESTIONS,
    INOTEX_SYNONYMS,
    seed_default_content,
    seed_default_synonyms,
)

LEGACY_TOKENS = ["الکامپ", "elecomp", "نورا", "noorvision", "چمران", "سئول"]


@pytest.fixture()
def cursor():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        # NOTE: this fixture hand-rolls the schema instead of using
        # app/db/connection.py, so it drifts every time a column is added —
        # `position` was added for the public display order and this CREATE
        # had to follow. Keep it in step with the real `dataset` table.
        "CREATE TABLE dataset (id TEXT PRIMARY KEY, title TEXT, text TEXT,"
        " video_url TEXT DEFAULT '', title_en TEXT DEFAULT '',"
        " text_en TEXT DEFAULT '', position INTEGER)"
    )
    cur.execute(
        "CREATE TABLE questions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " question TEXT, dataset_id TEXT, video_url TEXT DEFAULT '')"
    )
    cur.execute("CREATE TABLE synonyms (source TEXT NOT NULL, target TEXT NOT NULL,"
                "               PRIMARY KEY (source, target))")
    yield cur
    conn.close()


def test_dataset_entries_are_bilingual_with_official_source():
    assert len(INOTEX_DATASET) >= 12
    for item in INOTEX_DATASET:
        assert item["id"].startswith("inotex-")
        assert item["title"].strip() and item["text"].strip()
        assert item["title_en"].strip() and item["text_en"].strip(), item["id"]
        combined = item["text"] + item["text_en"]
        assert "inotex.com" in combined, f"{item['id']} cites no official source"


def test_every_question_maps_to_an_existing_entry():
    ids = {item["id"] for item in INOTEX_DATASET}
    for question, dataset_id in INOTEX_QUESTIONS:
        assert dataset_id in ids, f"orphan mapping: {question!r} → {dataset_id}"


def test_seed_carries_no_legacy_identity():
    blob = " ".join(
        item["title"] + item["text"] + item["title_en"] + item["text_en"]
        for item in INOTEX_DATASET
    )
    blob += " ".join(q for q, _ in INOTEX_QUESTIONS)
    blob += " ".join(s + " " + t for s, t in INOTEX_SYNONYMS)
    for token in LEGACY_TOKENS:
        assert token.lower() not in blob.lower(), f"legacy token {token!r} in seed"


def test_seed_populates_an_empty_db(cursor):
    seed_default_content(cursor)
    seed_default_synonyms(cursor)
    assert cursor.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == len(INOTEX_DATASET)
    assert cursor.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == len(INOTEX_QUESTIONS)
    assert cursor.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == len(INOTEX_SYNONYMS)


def test_seed_never_touches_existing_content(cursor):
    cursor.execute(
        "INSERT INTO dataset (id, title, text) VALUES ('customer-1', 'عنوان مشتری', 'متن مشتری')"
    )
    cursor.execute("INSERT INTO synonyms (source, target) VALUES ('a', 'b')")
    seed_default_content(cursor)
    seed_default_synonyms(cursor)
    assert cursor.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 1
    assert cursor.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == 1


def test_current_edition_facts_are_present():
    by_id = {item["id"]: item for item in INOTEX_DATASET}
    assert "۱۱ تا ۱۴ شهریور ۱۴۰۵" in by_id["inotex-date"]["text"]
    assert "پارک فناوری پردیس" in by_id["inotex-venue"]["text"]
    assert "پانزدهمین" in by_id["inotex-date"]["text"] or "پانزدهمین" in by_id["inotex-overview"]["text"]
