"""Shared content writes and the guaranteed index publish (ingest slice S3a).

An approved knowledge proposal must end in a published search-index version.
Before this slice it could not promise that: `_rebuild` returns silently when
another rebuild already holds `_rebuild_lock` (a poll, or another writer), so
a write committed while that rebuild was running was neither indexed by this
worker nor announced to the others. `reindex_and_publish_until_done` retries
until a rebuild that started after the caller's commit has run, and when it
cannot get one inside the bound it still publishes a version so the other
workers rebuild (docs/features/knowledge-ingestion/SPEC.md, REQ-064, SC-015).

The three INSERTs behind dataset, question and synonym creation moved from
the routers into app/db/queries.py, each on the caller's connection and
without a commit, so the ingest approval can put all of them in one
transaction (REQ-063). The router tests at the bottom pin the behaviour the
admin panel had before the move (SC-025).

Every test uses a throwaway SQLite file. A rebuild scheduled by an earlier
test can still hold the lock, so each test waits for it before and after.
Embeddings are switched off: with a row in the table the rebuild would load
the model2vec model, and a checkout without the cached model downloads it
from the network (measured here: a TestClient teardown hung for 368 s).
BM25 and the title match are enough to prove the new row is searchable.
"""
import datetime
import secrets
import threading
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

PUBLISH_TITLE = "پرسش آزمایشی انتشار نمایه"


def _drain_rebuilds():
    from app.services import search
    if search._rebuild_lock.acquire(timeout=30):
        search._rebuild_lock.release()


@pytest.fixture(autouse=True)
def _no_rebuild_in_flight():
    _drain_rebuilds()
    yield
    _drain_rebuilds()


@pytest.fixture(autouse=True)
def _no_embedding_model(monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(embeddings, "available", lambda: False)


@pytest.fixture
def store_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "search_publish.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()


@pytest.fixture
def admin_client(store_db):
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        token = secrets.token_hex(16)
        with closing(get_db_connection()) as conn:
            conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                         " security_question, security_answer_hash)"
                         " VALUES ('panel','x','y','q','z')")
            conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                         " VALUES (?,?,?)",
                         (token, "panel",
                          (datetime.datetime.now()
                           + datetime.timedelta(hours=1)).isoformat()))
            conn.commit()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c
        _drain_rebuilds()


def _db():
    from app.db.connection import get_db_connection
    return closing(get_db_connection())


def _dataset_row(item_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT id, title, text, video_url, title_en, text_en, position"
            " FROM dataset WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def _insert_raw_dataset_row(item_id, title, text, position=10):
    with _db() as conn:
        conn.execute("INSERT INTO dataset (id, title, text, video_url, position)"
                     " VALUES (?, ?, ?, '', ?)", (item_id, title, text, position))
        conn.commit()


# --- REQ-063: the shared write functions -------------------------------------

def test_a_new_dataset_entry_goes_after_the_last_position_and_waits_for_the_callers_commit(store_db):
    from app.db import queries
    _insert_raw_dataset_row("old", "قدیمی", "متن قدیمی", position=40)

    with _db() as conn:
        queries.insert_dataset_entry(conn, "new", "عنوان", "متن")
        row = conn.execute("SELECT video_url, title_en, text_en, position"
                           " FROM dataset WHERE id = 'new'").fetchone()
        assert dict(row) == {"video_url": "", "title_en": "", "text_en": "",
                             "position": 50}
        conn.rollback()

    assert _dataset_row("new") is None, "the function must not commit on its own"


def test_a_duplicate_dataset_id_raises_the_unique_violation_the_router_maps_to_409(store_db):
    from app.db import dberrors, queries
    _insert_raw_dataset_row("dup", "اول", "متن اول")

    with _db() as conn:
        with pytest.raises(Exception) as exc:
            queries.insert_dataset_entry(conn, "dup", "دوم", "متن دوم")
    assert dberrors.is_unique_violation(exc.value)


def test_a_new_question_returns_its_id_and_waits_for_the_callers_commit(store_db):
    from app.db import queries
    _insert_raw_dataset_row("q-ds", "عنوان", "متن")

    with _db() as conn:
        new_id = queries.insert_question(conn, "ساعت کار چیست؟", "q-ds")
        row = conn.execute("SELECT question, dataset_id, video_url FROM questions"
                           " WHERE id = ?", (new_id,)).fetchone()
        assert dict(row) == {"question": "ساعت کار چیست؟", "dataset_id": "q-ds",
                             "video_url": ""}
        conn.rollback()

    with _db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0


def test_synonym_pairs_count_only_new_rows_and_publish_nothing(store_db):
    from app.db import queries
    from app.services import search
    search.init_index_version()
    before = search.published_index_version()

    with _db() as conn:
        added = queries.insert_synonym_pairs(
            conn, [("لیزیک", "لیزر"), ("لیزیک", "لیزر"), ("لیزیک", "فمتو")])
        conn.rollback()
    assert added == 2
    with _db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == 0

    with _db() as conn:
        queries.insert_synonym_pairs(conn, [("بلیط", "بلیت")])
        conn.commit()
    assert search.published_index_version() == before, (
        "the ingest approval publishes once for the whole batch, so the"
        " shared synonym write must not bump the version itself")


# --- REQ-064: _rebuild reports whether a full rebuild ran ---------------------

def test_rebuild_reports_true_after_a_full_run_and_publishes(store_db):
    from app.services import search
    search.init_index_version()
    before = search.published_index_version()

    assert search._rebuild(publish=True) is True
    assert search.published_index_version() > before


def test_rebuild_reports_false_and_publishes_nothing_while_the_lock_is_held(store_db):
    from app.services import search
    search.init_index_version()
    before = search.published_index_version()

    assert search._rebuild_lock.acquire(timeout=5)
    try:
        assert search._rebuild(publish=True) is False
    finally:
        search._rebuild_lock.release()
    assert search.published_index_version() == before


def test_rebuild_reports_false_when_the_load_raises(store_db, monkeypatch):
    from app.services import search
    search.init_index_version()
    before = search.published_index_version()

    def failing_load():
        raise RuntimeError("boom")

    monkeypatch.setattr(search, "load_dataset_internal", failing_load)
    assert search._rebuild(publish=True) is False
    assert search.published_index_version() == before


# --- REQ-081 (S3a part): the published version --------------------------------

def test_published_version_is_the_stored_one_not_this_workers_local_copy(store_db):
    from app.db.queries import get_setting
    from app.services import search
    search.init_index_version()

    search.bump_index_version()
    stored = int(get_setting(search.INDEX_VERSION_KEY, "0", fresh=True))
    assert search.published_index_version() == stored

    assert search._rebuild(publish=False, version_floor=stored + 100) is True
    assert search._index_version == stored + 100
    assert search.published_index_version() == stored, (
        "a rebuild that does not publish must not move the published version")


# --- REQ-064 / SC-015: reindex_and_publish_until_done -------------------------

def test_an_uncontended_call_publishes_exactly_once(store_db, monkeypatch):
    from app.db import queries
    from app.services import search
    search.init_index_version()
    before = search.published_index_version()

    version_writes = []
    real_set_setting = queries.set_setting

    def recording_set_setting(key, value):
        if key == search.INDEX_VERSION_KEY:
            version_writes.append(value)
        real_set_setting(key, value)

    monkeypatch.setattr(queries, "set_setting", recording_set_setting)

    assert search.reindex_and_publish_until_done() is True
    assert len(version_writes) == 1
    assert search.published_index_version() > before


def test_a_held_lock_delays_the_publish_until_it_is_released(store_db, monkeypatch):
    from app.services import search
    search.init_index_version()
    before = search.published_index_version()
    _insert_raw_dataset_row("pub-1", PUBLISH_TITLE, "متن ردیف تازه برای آزمون انتشار")

    load_starts = []
    real_load = search.load_dataset_internal

    def recording_load():
        load_starts.append(time.monotonic())
        real_load()

    monkeypatch.setattr(search, "load_dataset_internal", recording_load)

    result = {}

    def call():
        result["published"] = search.reindex_and_publish_until_done(timeout_s=60.0)

    assert search._rebuild_lock.acquire(timeout=5)
    try:
        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        time.sleep(1.2)
        assert worker.is_alive(), "it must keep waiting while the lock is held"
        assert load_starts == []
        released_at = time.monotonic()
    finally:
        search._rebuild_lock.release()
    worker.join(timeout=60)

    assert not worker.is_alive()
    assert result.get("published") is True
    assert search.published_index_version() > before
    assert load_starts and load_starts[0] >= released_at, (
        "the rebuild that counts must start after the lock was released")
    entry, _score = search.find_best_match(PUBLISH_TITLE)
    assert entry is not None and entry["id"] == "pub-1"


def test_a_lock_that_never_frees_ends_in_a_bump_an_error_and_false(store_db, monkeypatch):
    from app.services import applog, search
    search.init_index_version()
    before = search.published_index_version()

    errors = []
    monkeypatch.setattr(applog, "error",
                        lambda *args, **kwargs: errors.append((args, kwargs)))

    assert search._rebuild_lock.acquire(timeout=5)
    try:
        started = time.monotonic()
        published = search.reindex_and_publish_until_done(timeout_s=1.0)
        elapsed = time.monotonic() - started
    finally:
        search._rebuild_lock.release()

    assert published is False
    assert 0.9 <= elapsed < 5.0, f"the wait is bounded by timeout_s, took {elapsed:.2f}s"
    assert search.published_index_version() > before, (
        "on timeout the other workers must still be told to rebuild")
    assert len(errors) == 1
    assert errors[0][0][0] == "retrieval"


def test_a_bump_that_raises_on_timeout_is_still_logged_and_returns_false(store_db, monkeypatch):
    from app.services import applog, search
    search.init_index_version()

    errors = []
    monkeypatch.setattr(applog, "error",
                        lambda *args, **kwargs: errors.append((args, kwargs)))

    def failing_bump():
        raise RuntimeError("settings store down")

    monkeypatch.setattr(search, "bump_index_version", failing_bump)

    assert search._rebuild_lock.acquire(timeout=5)
    try:
        published = search.reindex_and_publish_until_done(timeout_s=0.5)
    finally:
        search._rebuild_lock.release()

    assert published is False
    assert len(errors) == 1
    assert errors[0][0][:2] == ("retrieval", "retrieval.publish_timeout")


def test_a_load_that_keeps_failing_is_retried_until_the_deadline(store_db, monkeypatch):
    from app.services import applog, search
    search.init_index_version()
    before = search.published_index_version()

    attempts = []

    def failing_load():
        attempts.append(time.monotonic())
        raise RuntimeError("boom")

    monkeypatch.setattr(search, "load_dataset_internal", failing_load)
    monkeypatch.setattr(applog, "error", lambda *args, **kwargs: None)

    assert search.reindex_and_publish_until_done(timeout_s=1.0) is False
    assert len(attempts) >= 2
    assert search.published_index_version() > before


# --- SC-025: the admin write paths behave as before the move ------------------

def test_creating_a_dataset_entry_keeps_english_fields_position_and_the_409(admin_client):
    _insert_raw_dataset_row("first", "اول", "متن اول", position=70)

    res = admin_client.post("/admin/api/dataset", json={
        "id": "second", "title": "دوم", "text": "متن دوم", "video_url": "/v.mp4",
        "title_en": "Second", "text_en": "Second text"})
    assert res.status_code == 200
    assert res.json() == {"status": "created"}
    assert _dataset_row("second") == {
        "id": "second", "title": "دوم", "text": "متن دوم", "video_url": "/v.mp4",
        "title_en": "Second", "text_en": "Second text", "position": 80}

    dup = admin_client.post("/admin/api/dataset", json={
        "id": "first", "title": "جایگزین", "text": "متن جایگزین"})
    assert dup.status_code == 409
    assert dup.json()["detail"] == "ID already exists"
    assert _dataset_row("first")["title"] == "اول"


def test_adding_a_synonym_by_hand_still_reloads_and_bumps_once(admin_client, monkeypatch):
    from app.services import search
    bumps = []
    monkeypatch.setattr(search, "bump_index_version", lambda: bumps.append(1))

    res = admin_client.post("/api/synonyms", json={"source": "لازک", "target": "لیزر"})
    assert res.status_code == 200
    assert res.json() == {"status": "success"}
    assert len(bumps) == 1

    import app.utils.normalizer as normalizer
    assert ("لازک", "لیزر") in normalizer.active_synonyms
