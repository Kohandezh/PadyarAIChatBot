"""Regression tests for the multi-worker dataset/questions/synonyms sync fix.

Production runs gunicorn with several worker processes, each holding its own
in-memory copy of the dataset/questions/synonyms. Admin reads and writes must go
through the SQLite DB (the single source of truth), not per-worker memory —
otherwise the two reported production bugs appear:

  * a newly-added dataset entry / synonym doesn't show in the admin list until a
    logout/login or a manual "reload dataset" (the list read returned this
    worker's stale memory, or — for synonyms — a stale imported reference), and
  * a write on one worker rewrote the whole table from a stale snapshot,
    silently dropping rows another worker had added.

Each test gets a fresh temp SQLite DB (never the real chat_history.db) and
bypasses admin auth via a dependency override.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the whole app at a throwaway DB. get_db_connection() / init_db()
    # both re-read app.config.DB_PATH at call time, so patching the attribute
    # before the lifespan runs is enough.
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_chat.db"))
    # These tests assert on an empty knowledge base, so the bundled INOTEX
    # defaults must not be seeded into the throwaway DB. Read at call time in
    # connection.py, same as DB_PATH.
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    from app.auth.security import verify_admin

    app.dependency_overrides[verify_admin] = lambda: True
    try:
        # Context manager so the lifespan (init_db + load) actually runs.
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _db():
    from app.db.connection import get_db_connection
    return get_db_connection()


# --- Bug 1 & 2: writes must be visible to the next read --------------------

def test_added_dataset_item_appears_in_list_immediately(client):
    payload = {"id": "vid1", "title": "تست", "text": "متن پاسخ", "video_url": ""}
    assert client.post("/admin/api/dataset", json=payload).status_code == 200

    items = client.get("/admin/api/dataset").json()
    assert any(d["id"] == "vid1" for d in items)

    # The public chat-facing endpoint must agree (it already read from the DB).
    assert any(d["id"] == "vid1" for d in client.get("/api/dataset").json())


def test_added_synonym_appears_in_list_immediately(client):
    assert client.post("/api/synonyms", json={"source": "لازک", "target": "لیزر"}).status_code == 200
    syns = client.get("/api/synonyms").json()["synonyms"]
    assert {"source": "لازک", "target": "لیزر"} in syns


def test_added_question_appears_in_list(client):
    client.post("/admin/api/dataset", json={"id": "q-ds", "title": "t", "text": "x", "video_url": ""})
    res = client.post("/admin/api/questions", json={"question": "سوال؟", "dataset_id": "q-ds", "video_url": ""})
    assert res.status_code == 200
    qid = res.json()["id"]

    qs = client.get("/admin/api/questions").json()
    assert any(q["id"] == qid and q["question"] == "سوال؟" for q in qs)

    filtered = client.get("/admin/api/questions", params={"dataset_id": "q-ds"}).json()
    assert [q["id"] for q in filtered] == [qid]


# --- Bug 3: a write must not clobber rows added by another worker ----------

def test_create_dataset_does_not_drop_rows_from_another_worker(client):
    # Simulate a row another worker inserted straight into the DB that this
    # worker's in-memory index never saw. The old whole-table-rewrite save would
    # delete it; a targeted INSERT must leave it untouched.
    conn = _db()
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url) VALUES (?, ?, ?, ?)",
        ("other-worker", "از کارگر دیگر", "متن", ""),
    )
    conn.commit()
    conn.close()

    assert client.post(
        "/admin/api/dataset",
        json={"id": "mine", "title": "مال من", "text": "متن", "video_url": ""},
    ).status_code == 200

    conn = _db()
    ids = {r["id"] for r in conn.execute("SELECT id FROM dataset").fetchall()}
    conn.close()
    assert ids == {"other-worker", "mine"}


def test_create_question_does_not_drop_rows_from_another_worker(client):
    conn = _db()
    conn.execute(
        "INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, ?)",
        ("سوال کارگر دیگر", "ds", ""),
    )
    conn.commit()
    conn.close()

    assert client.post(
        "/admin/api/questions",
        json={"question": "سوال من", "dataset_id": "ds", "video_url": ""},
    ).status_code == 200

    conn = _db()
    questions = {r["question"] for r in conn.execute("SELECT question FROM questions").fetchall()}
    conn.close()
    assert questions == {"سوال کارگر دیگر", "سوال من"}


# --- CRUD edge cases -------------------------------------------------------

def test_duplicate_dataset_id_rejected(client):
    payload = {"id": "dup", "title": "t", "text": "x", "video_url": ""}
    assert client.post("/admin/api/dataset", json=payload).status_code == 200
    assert client.post("/admin/api/dataset", json=payload).status_code == 400


def test_update_and_delete_dataset_round_trip(client):
    client.post("/admin/api/dataset", json={"id": "e1", "title": "old", "text": "x", "video_url": ""})

    assert client.put(
        "/admin/api/dataset/e1",
        json={"title": "new", "text": "x2", "video_url": "http://v"},
    ).status_code == 200
    item = next(d for d in client.get("/admin/api/dataset").json() if d["id"] == "e1")
    assert item["title"] == "new" and item["text"] == "x2" and item["video_url"] == "http://v"

    assert client.delete("/admin/api/dataset/e1").status_code == 200
    assert all(d["id"] != "e1" for d in client.get("/admin/api/dataset").json())


def test_update_delete_missing_dataset_returns_404(client):
    assert client.put("/admin/api/dataset/nope", json={"title": "x", "text": "y", "video_url": ""}).status_code == 404
    assert client.delete("/admin/api/dataset/nope").status_code == 404
