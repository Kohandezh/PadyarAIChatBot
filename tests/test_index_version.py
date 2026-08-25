"""The search-index version stamp — cross-worker freshness.

Every worker keeps its retrieval indexes in process memory. Writers rebuild
their own copy immediately; the version key in `settings` is what tells the
OTHER workers to rebuild. These tests pin the stamp mechanics themselves:

  * a published version is readable back from the store,
  * a reader that is behind schedules exactly one background rebuild and
    adopts the new version,
  * a reader that is current does nothing.

Each test gets a fresh throwaway SQLite DB (same idiom as test_dataset_sync).
"""
import threading

import pytest


@pytest.fixture
def store_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()


def test_published_version_is_readable_and_monotonic(store_db):
    from app.services import search
    search.init_index_version()
    first = search._read_index_version()
    assert first >= 1
    search.bump_index_version()
    assert search._read_index_version() > first


def test_behind_reader_rebuilds_once_and_adopts(store_db, monkeypatch):
    from app.services import search
    search.init_index_version()

    rebuilds = []
    done = threading.Event()
    real_rebuild = search._rebuild

    def counting_rebuild(publish, version_floor=0):
        try:
            rebuilds.append((publish, version_floor))
            real_rebuild(publish, version_floor)
        finally:
            done.set()

    monkeypatch.setattr(search, "_rebuild", counting_rebuild)

    # Another "worker" publishes a newer version.
    from app.db.queries import set_setting
    set_setting(search.INDEX_VERSION_KEY, str(search._index_version + 5))

    # Force the freshness check window open, then poll twice: the second poll
    # must NOT schedule another rebuild — the reader already adopted.
    monkeypatch.setattr(search, "_last_version_check", 0.0)
    search._maybe_refresh()
    monkeypatch.setattr(search, "_last_version_check", 0.0)
    search._maybe_refresh()

    assert len(rebuilds) == 1
    assert rebuilds[0][0] is False
    assert search._index_version == search._read_index_version()
    # The rebuild ran in the default executor; let it finish before this
    # test's throwaway DB goes away, or its open connection leaks into the
    # next test's init_db ("database is locked").
    done.wait(timeout=10)


def test_current_reader_does_nothing(store_db, monkeypatch):
    from app.services import search
    search.init_index_version()

    def boom(*a, **kw):
        raise AssertionError("a current reader must not rebuild")

    monkeypatch.setattr(search, "_rebuild", boom)
    monkeypatch.setattr(search, "_last_version_check", 0.0)
    search._maybe_refresh()


def test_failed_rebuild_resets_so_the_next_poll_retries(store_db, monkeypatch):
    from app.services import search
    search.init_index_version()

    def failing_load():
        raise RuntimeError("boom")

    monkeypatch.setattr(search, "load_dataset_internal", failing_load)

    from app.db.queries import set_setting
    set_setting(search.INDEX_VERSION_KEY, str(search._index_version + 3))
    # Drive the rebuild path directly: _maybe_refresh hands it to an executor
    # thread, and a slow thread from an earlier test can still hold the
    # non-blocking rebuild lock.
    search._rebuild(False, search._read_index_version())
    assert search._index_version == 0
