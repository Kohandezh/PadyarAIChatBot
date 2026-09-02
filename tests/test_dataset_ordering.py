"""The public knowledge base has an explicit display order.

The visitor-facing chat UI draws its one-click question menu from the head of
that order, and the endpoint that serves it used to order by `rowid` — SQLite's
implicit insertion counter. PostgreSQL has no such column, so once PostgreSQL
became the production backend the endpoint returned a hard 500 in production
while every test here stayed green, because the suite pins `DB_BACKEND=sqlite`.

Two things therefore need holding down, and the second is the one that would
go unnoticed:

  1. the endpoint must not 500, and
  2. the ORDER must stay the curated one. `id` is TEXT, so the obvious
     "just order by id" fix sorts alphabetically and would quietly float
     `inotex-app` above `inotex-overview` — a silent content regression
     that no error log would ever report.

The public endpoint is now `/api/suggestions`, and it serves only the first
`SUGGESTION_LIMIT` rows, titles only — `/api/dataset`, which returned every
row of the customer's knowledge base to anyone who asked, is gone (see
tests/test_public_data_api.py). So the order is checked in two places that
must agree: the full sequence is read straight from the database, and the
endpoint is checked to serve the HEAD of exactly that sequence. Point 1 and
the alphabetical-fix trap are unchanged — the same `ORDER BY` moved into the
new endpoint.
"""
import pytest
from fastapi.testclient import TestClient

from app.routers.public import SUGGESTION_LIMIT

# The curated reading order, as served before the PostgreSQL migration.
# Taken from the rowid sequence of the pre-migration SQLite database, which
# was the only surviving record of it. Extended 2026-08-27 with the crawled
# 2026 program block, and 2026-09-02 with the crawled history/topics/access/
# new-programs block (app/default_content.py, same seed order both times).
CURATED = [
    "inotex-overview", "inotex-date", "inotex-venue", "inotex-hours",
    "inotex-booth", "inotex-programs", "inotex-pitch", "inotex-contact",
    "inotex-exhibitors", "inotex-visitors", "inotex-stats", "inotex-app",
    "inotex-volunteer", "inotex-organizers", "inotex-targeted-visit",
    "inotex-news",
    "inotex-schedule-2026", "inotex-express-2026", "inotex-pitch-2026-final",
    "inotex-stage-2026", "inotex-capital-cafe-2026",
    "inotex-investors-pavilion-2026", "inotex-reverse-pitch-2026",
    "inotex-fanbazar-2026", "inotex-ai-iot-conf-2026", "inotex-work-station-2026",
    "inotex-mentors-2026", "inotex-inonight-meetups-2026",
    "inotex-governance-forum-2026", "inotex-pardis-summit-2026",
    "inotex-selection-day-2026",
    "inotex-history", "inotex-topics", "inotex-access", "inotex-new-programs-2026",
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
    """The full display order, read from the database.

    Not from the endpoint any more: it serves ten titles, so it cannot show
    where row 31 landed. The `ORDER BY` here is a copy of the endpoint's, and
    `test_the_endpoint_serves_the_head_of_the_curated_order` is what stops the
    copy from drifting away from the original.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM dataset"
            " ORDER BY COALESCE(position, 2147483647), id").fetchall()
    finally:
        conn.close()
    return [row["id"] for row in rows]


def _titles_by_id(client):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT id, title FROM dataset").fetchall()
    finally:
        conn.close()
    return {row["id"]: row["title"] for row in rows}


def test_the_suggestions_endpoint_does_not_error(client):
    """The regression itself: the endpoint this replaced returned 500 on
    PostgreSQL, and it kept the same `ORDER BY`."""
    assert client.get("/api/suggestions").status_code == 200


def test_a_seeded_install_serves_the_curated_order(client):
    assert _ids(client) == CURATED


def test_the_endpoint_serves_the_head_of_the_curated_order(client):
    """Ties the served chips back to the curated ids. Without this, `_ids`
    could go on passing against a database order the endpoint no longer
    follows, and the visitor's menu would silently reshuffle."""
    res = client.get("/api/suggestions")
    assert res.status_code == 200, res.text
    served = [row["title"] for row in res.json()]
    titles = _titles_by_id(client)
    assert served == [titles[i] for i in CURATED[:SUGGESTION_LIMIT]]


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
    # The NULL is what could crash the sort, so the endpoint gets asked too.
    assert client.get("/api/suggestions").status_code == 200
