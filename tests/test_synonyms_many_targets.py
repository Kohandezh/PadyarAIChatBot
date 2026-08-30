"""A word has many synonyms, on both backends.

THE DEFECT
----------
`app/db/connection.py` declared the SQLite table as `source TEXT PRIMARY KEY`
while `migrations/0001_initial.sql` (written from the LIVE table) declared
`PRIMARY KEY (source, target)`. `app/db/pg.py` builds its ON CONFLICT clause
from the table's real key, so one admin action did two things:

  * saving a second synonym for a word REPLACED the first on SQLite and ADDED a
    row on PostgreSQL, and
  * `DELETE FROM synonyms WHERE source = ?` removed one mapping on SQLite and
    every mapping for that word on PostgreSQL.

Synonyms feed query expansion, which feeds retrieval, so the two backends
answered visitors differently. The pair key won: it is what production already
held, and a word genuinely has several synonyms.

Every test here runs on SQLite and fails against the single-column key.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_synonyms.db"))
    # The bundled INOTEX synonyms would drown the rows these tests count.
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    from app.auth.security import verify_admin

    app.dependency_overrides[verify_admin] = lambda: True
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        # `active_synonyms` is process-wide and these tests fill it. Later
        # modules must not inherit words from a database that no longer exists.
        import app.utils.normalizer as normalizer
        normalizer.active_synonyms = []


def _rows(client, source):
    return [s["target"] for s in client.get("/api/synonyms").json()["synonyms"]
            if s["source"] == source]


# --- The schema itself -----------------------------------------------------

def test_the_sqlite_key_is_the_pair(client):
    """The divergence, asserted directly."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    key = [c["name"] for c in conn.execute("PRAGMA table_info(synonyms)").fetchall()
           if c["pk"]]
    conn.close()
    assert key == ["source", "target"]


def test_an_older_database_is_rebuilt_onto_the_pair_key(tmp_path):
    """A file created before the split keeps its rows and loses its old key.

    SQLite cannot alter a primary key in place, so CREATE TABLE IF NOT EXISTS
    would have left the single-column key (and the bug) in place forever.
    """
    from app.db.connection import _create_sqlite_schema

    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE synonyms (source TEXT PRIMARY KEY, target TEXT)")
    old.execute("INSERT INTO synonyms VALUES ('لیزیک', 'لیزر')")
    old.commit()
    old.close()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _create_sqlite_schema(conn.cursor())
    conn.commit()

    key = [c["name"] for c in conn.execute("PRAGMA table_info(synonyms)").fetchall()
           if c["pk"]]
    assert key == ["source", "target"]
    assert conn.execute("SELECT target FROM synonyms").fetchall()[0]["target"] == "لیزر"

    # And the rebuilt table now accepts the second synonym the old one refused.
    conn.execute("INSERT INTO synonyms VALUES ('لیزیک', 'پی آر کی')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM synonyms").fetchone()["n"] == 2
    conn.close()


# --- The admin API ---------------------------------------------------------

def test_one_word_holds_several_synonyms(client):
    for target in ("لیزر", "پی آر کی", "فمتو"):
        assert client.post("/api/synonyms",
                           json={"source": "لیزیک", "target": target}).status_code == 200

    assert sorted(_rows(client, "لیزیک")) == sorted(["لیزر", "پی آر کی", "فمتو"])


def test_saving_the_same_pair_twice_does_not_duplicate(client):
    for _ in range(3):
        assert client.post("/api/synonyms",
                           json={"source": "بلیط", "target": "بلیت"}).status_code == 200

    assert _rows(client, "بلیط") == ["بلیت"]


def test_deleting_one_mapping_leaves_the_others(client):
    """The data-loss regression.

    The old route deleted by source alone. On the pair key that wipes every
    synonym of the word, which is what production did on each delete.
    """
    for target in ("لیزر", "پی آر کی", "فمتو"):
        client.post("/api/synonyms", json={"source": "لیزیک", "target": target})

    res = client.request("DELETE", "/api/synonyms/لیزیک", params={"target": "پی آر کی"})
    assert res.status_code == 200
    assert res.json()["deleted"] == 1

    assert sorted(_rows(client, "لیزیک")) == sorted(["لیزر", "فمتو"])


def test_delete_without_a_target_is_refused(client):
    """The API cannot be asked to remove "the synonym of this word" any more."""
    client.post("/api/synonyms", json={"source": "بلیط", "target": "بلیت"})

    assert client.delete("/api/synonyms/بلیط").status_code == 422
    assert _rows(client, "بلیط") == ["بلیت"]


def test_deleting_a_pair_that_is_not_there_reports_zero(client):
    client.post("/api/synonyms", json={"source": "بلیط", "target": "بلیت"})

    res = client.request("DELETE", "/api/synonyms/بلیط", params={"target": "ورودیه"})
    assert res.status_code == 200
    assert res.json()["deleted"] == 0
    assert _rows(client, "بلیط") == ["بلیت"]


def test_an_empty_word_is_refused(client):
    """An empty source would splice the target between every character of every
    query, and an empty target could never be named on the delete route."""
    assert client.post("/api/synonyms", json={"source": "", "target": "x"}).status_code == 400
    assert client.post("/api/synonyms", json={"source": "x", "target": " "}).status_code == 400


# --- Query expansion -------------------------------------------------------

def test_expansion_picks_up_every_synonym_of_a_word(client):
    """Row-by-row replacement lost all but one: the first target consumed the
    source, so the later rows never fired."""
    from app.utils.normalizer import load_synonyms_from_db, normalize_persian

    for target in ("لیزر", "پی آر کی", "فمتو"):
        client.post("/api/synonyms", json={"source": "لیزیک", "target": target})
    load_synonyms_from_db()

    expanded = normalize_persian("هزینه لیزیک چقدر است")
    for target in ("لیزر", "پی آر کی", "فمتو"):
        assert target in expanded, expanded


def test_expansion_does_not_repeat_a_word_across_targets(client):
    """Repeating a token raises its term frequency for TF-IDF and BM25 without
    adding meaning, which skews the scores expansion is meant to help."""
    from app.utils.normalizer import load_synonyms_from_db, normalize_persian

    client.post("/api/synonyms", json={"source": "بلیط", "target": "بلیت ورودی"})
    client.post("/api/synonyms", json={"source": "بلیط", "target": "ورودی هزینه"})
    load_synonyms_from_db()

    assert normalize_persian("بلیط").split().count("ورودی") == 1


def test_a_single_target_expands_deduped(client):
    """The merge keeps every DISTINCT word of the tuned one-row strings.

    The old contract kept the first target verbatim, repeats and all. That is
    exactly the defect fixed on 2026-08-26: «پارک فناوری پردیس پردیس» doubled
    the source word, and doubled words pushed expanded queries out of the
    embedding model's region (dense=0.000 on the diagnostic run). The new
    contract is: source once, each synonym word once — the union, no repeats.
    """
    from app.utils.normalizer import load_synonyms_from_db, normalize_persian

    client.post("/api/synonyms", json={"source": "پردیس", "target": "پارک فناوری پردیس پردیس"})
    load_synonyms_from_db()

    assert normalize_persian("پردیس") == "پردیس پارک فناوری"


# --- Bulk delete -------------------------------------------------------

@pytest.fixture
def anon_client(tmp_path, monkeypatch):
    """A client that never overrides verify_admin — for the auth check.

    A separate fixture rather than reusing `client`: the module-level
    `client` fixture always bypasses admin auth, which is exactly what a
    401/403 test must NOT do.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_synonyms_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app

    with TestClient(app) as c:
        yield c

    import app.utils.normalizer as normalizer
    normalizer.active_synonyms = []


def test_bulk_delete_removes_every_pair_and_bumps_index_once(client, monkeypatch):
    for source, target in (("لیزیک", "لیزر"), ("لیزیک", "فمتو"), ("بلیط", "بلیت")):
        client.post("/api/synonyms", json={"source": source, "target": target})

    calls = []
    import app.services.search as search
    monkeypatch.setattr(search, "bump_index_version", lambda: calls.append(1))

    res = client.post("/api/synonyms/bulk-delete", json={"pairs": [
        {"source": "لیزیک", "target": "لیزر"},
        {"source": "بلیط", "target": "بلیت"},
    ]})
    assert res.status_code == 200
    assert res.json() == {"status": "success", "deleted": 2}
    assert len(calls) == 1

    assert _rows(client, "لیزیک") == ["فمتو"]
    assert _rows(client, "بلیط") == []


def test_bulk_delete_skips_pairs_that_are_not_there(client):
    client.post("/api/synonyms", json={"source": "لیزیک", "target": "لیزر"})

    res = client.post("/api/synonyms/bulk-delete", json={"pairs": [
        {"source": "لیزیک", "target": "لیزر"},
        {"source": "لیزیک", "target": "این وجود ندارد"},
    ]})
    assert res.status_code == 200
    assert res.json()["deleted"] == 1
    assert _rows(client, "لیزیک") == []


def test_bulk_delete_rejects_an_empty_list(client):
    res = client.post("/api/synonyms/bulk-delete", json={"pairs": []})
    assert res.status_code == 400


def test_bulk_delete_rejects_a_pair_missing_a_word(client):
    client.post("/api/synonyms", json={"source": "لیزیک", "target": "لیزر"})

    res = client.post("/api/synonyms/bulk-delete", json={"pairs": [
        {"source": "لیزیک", "target": ""},
    ]})
    assert res.status_code == 400
    assert _rows(client, "لیزیک") == ["لیزر"]


def test_bulk_delete_rejects_an_unauthenticated_caller(anon_client):
    res = anon_client.post("/api/synonyms/bulk-delete", json={"pairs": [
        {"source": "لیزیک", "target": "لیزر"},
    ]})
    assert res.status_code in (401, 403)
