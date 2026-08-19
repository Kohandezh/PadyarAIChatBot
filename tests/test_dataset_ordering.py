"""The public knowledge base has an explicit display order.

`/api/dataset` is what the visitor-facing chat UI loads its knowledge base
from, and it used to order by `rowid` — SQLite's implicit insertion counter.
PostgreSQL has no such column, so once PostgreSQL became the production
backend the endpoint returned a hard 500 in production while every test here
stayed green, because the suite pins `DB_BACKEND=sqlite`.

Two things therefore need holding down, and the second is the one that would
go unnoticed:

  1. the endpoint must not 500, and
  2. the ORDER must stay the curated one. `id` is TEXT, so the obvious
     "just order by id" fix sorts alphabetically and would quietly float
     `inotex-app` above `inotex-overview` — a silent content regression
     that no error log would ever report.
"""
import pytest
from fastapi.testclient import TestClient

# The curated reading order, as served before the PostgreSQL migration.
# Taken from the rowid sequence of the pre-migration SQLite database, which
# was the only surviving record of it.
CURATED = [
    "inotex-overview", "inotex-date", "inotex-venue", "inotex-hours",
    "inotex-booth", "inotex-programs", "inotex-pitch", "inotex-contact",
    "inotex-exhibitors", "inotex-visitors", "inotex-stats", "inotex-app",
    "inotex-volunteer", "inotex-organizers", "inotex-targeted-visit",
    "inotex-news",
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh install, seeded with the default INOTEX content."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ordering.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", True)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _ids(client):
    res = client.get("/api/dataset")
    assert res.status_code == 200, res.text
    return [row["id"] for row in res.json()]


def test_the_dataset_endpoint_does_not_error(client):
    """The regression itself: this returned 500 on PostgreSQL."""
    assert client.get("/api/dataset").status_code == 200


def test_a_seeded_install_serves_the_curated_order(client):
    assert _ids(client) == CURATED


def test_the_order_is_not_alphabetical(client):
    """Guards the tempting wrong fix. `ORDER BY id` passes the 'no 500' test
    and silently reshuffles what every visitor reads."""
    ids = _ids(client)
    assert ids != sorted(ids)
    assert ids[0] == "inotex-overview"
    # Alphabetically `inotex-app` would come first of all; it must not.
    assert ids.index("inotex-overview") < ids.index("inotex-app")


def test_every_seeded_row_has_an_explicit_position(client):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        missing = conn.execute(
            "SELECT COUNT(*) FROM dataset WHERE position IS NULL").fetchone()[0]
    finally:
        conn.close()
    assert missing == 0


def test_a_newly_added_entry_goes_to_the_end_not_the_front(client):
    """A new entry must not jump the queue. With NULL positions and the
    COALESCE fallback it would land last too, but only by accident — this
    asserts the explicit position the insert now assigns."""
    # The admin API needs an authenticated session; this exercises the same
    # MAX(position)+10 the endpoint computes, without the auth scaffolding.
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        nxt = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 10 FROM dataset").fetchone()[0]
        conn.execute(
            "INSERT INTO dataset (id, title, text, video_url, title_en,"
            " text_en, position) VALUES (?,?,?,?,?,?,?)",
            ("zz-new-entry", "T", "X", "", "", "", nxt))
        conn.commit()
    finally:
        conn.close()

    ids = _ids(client)
    assert ids[-1] == "zz-new-entry"
    assert ids[:len(CURATED)] == CURATED      # nothing else moved


def test_an_unpositioned_row_still_sorts_last_and_does_not_break_the_page(client):
    """Defence in depth: a row inserted by some path that forgets `position`
    must not vanish, crash the endpoint, or land at the top."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO dataset (id, title, text, video_url, title_en, text_en)"
            " VALUES (?,?,?,?,?,?)", ("zz-no-position", "T", "X", "", "", ""))
        conn.commit()
    finally:
        conn.close()

    ids = _ids(client)
    assert "zz-no-position" in ids
    assert ids[-1] == "zz-no-position"
    assert ids[0] == "inotex-overview"
