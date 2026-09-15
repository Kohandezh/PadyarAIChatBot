"""The default knowledge seed: a fresh install starts empty.

These tests protect two contracts:
1. The platform ships with no bundled facts — every piece of content a
   customer's assistant knows arrives via the admin panel or its import
   scripts, so one customer's event can never leak into another's install.
2. The seeding hooks stay wired and idempotent: they are the mechanism a
   customer's own seed module plugs into, and they must never touch rows
   that are already in the database.
"""
import sqlite3

import pytest

from app.default_content import (
    DEFAULT_DATASET,
    DEFAULT_QUESTIONS,
    DEFAULT_SYNONYMS,
    seed_default_content,
    seed_default_synonyms,
)


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


def test_the_default_seed_is_empty():
    assert DEFAULT_DATASET == []
    assert DEFAULT_QUESTIONS == []
    assert DEFAULT_SYNONYMS == []


def test_fresh_install_seeds_nothing(cursor):
    seed_default_content(cursor)
    seed_default_synonyms(cursor)
    assert cursor.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 0
    assert cursor.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
    assert cursor.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == 0


def test_seeding_is_idempotent_on_empty_data(cursor):
    seed_default_content(cursor)
    seed_default_synonyms(cursor)
    seed_default_content(cursor)
    seed_default_synonyms(cursor)
    assert cursor.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 0
    assert cursor.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
    assert cursor.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == 0


def test_seed_never_touches_existing_content(cursor):
    cursor.execute(
        "INSERT INTO dataset (id, title, text) VALUES ('customer-1', 'عنوان مشتری', 'متن مشتری')"
    )
    cursor.execute("INSERT INTO synonyms (source, target) VALUES ('a', 'b')")
    seed_default_content(cursor)
    seed_default_synonyms(cursor)
    assert cursor.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 1
    assert cursor.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == 1
