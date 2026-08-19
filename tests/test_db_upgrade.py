"""Upgrading an existing installation must never lose data.

This project has no migration framework — schema changes are made by
`CREATE TABLE IF NOT EXISTS` plus idempotent `ALTER TABLE ... ADD COLUMN`.
That is simple and right for a single-tenant kiosk install, but it only stays
safe if it is actually exercised against an OLD database. These tests are that
exercise: they build a database in its pre-upgrade shape, run the upgrade path,
and assert the existing rows are still there.
"""
import sqlite3

import pytest

from app.services import otp as otp_service


# The otp_challenges table as it shipped BEFORE the profile step (no name, job,
# position or interests columns).
LEGACY_SCHEMA = """
CREATE TABLE otp_challenges (
    id TEXT PRIMARY KEY,
    destination TEXT NOT NULL,
    code_hmac TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    resends INTEGER DEFAULT 0,
    used INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    last_sent_at TEXT NOT NULL
)
"""

NEW_COLUMNS = ("first_name", "last_name", "job", "position", "interests")


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A database in its pre-upgrade shape, with one row of real data."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO otp_challenges VALUES"
        " ('legacy-1','+989120000001','deadbeef','2026-01-01T00:00:00',"
        "  2,1,1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    # `get_db_connection` imports DB_PATH from app.config on every call, so the
    # patch has to land there — patching app.db.connection would silently do
    # nothing and run these tests against the real database.
    from app import config
    monkeypatch.setattr(config, "DB_PATH", str(path))
    return path


def columns(path):
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(otp_challenges)")]
    finally:
        conn.close()


def test_upgrade_adds_the_new_columns(legacy_db):
    for col in NEW_COLUMNS:
        assert col not in columns(legacy_db), "fixture is not actually a legacy DB"

    otp_service.ensure_table()

    for col in NEW_COLUMNS:
        assert col in columns(legacy_db)


def test_upgrade_preserves_existing_rows(legacy_db):
    """The whole point: an operator upgrading must not lose their data."""
    otp_service.ensure_table()

    conn = sqlite3.connect(legacy_db)
    try:
        row = conn.execute(
            "SELECT id, destination, attempts, resends, used FROM otp_challenges"
        ).fetchall()
    finally:
        conn.close()

    assert row == [("legacy-1", "+989120000001", 2, 1, 1)]


def test_new_columns_default_to_empty_not_null(legacy_db):
    """Downstream code reads these with `or ""`; NULL would still be fine, but
    an empty default keeps old and new rows shaped identically."""
    otp_service.ensure_table()

    conn = sqlite3.connect(legacy_db)
    try:
        job, position, interests = conn.execute(
            "SELECT job, position, interests FROM otp_challenges WHERE id = 'legacy-1'"
        ).fetchone()
    finally:
        conn.close()

    assert (job, position, interests) == ("", "", "")


def test_upgrade_is_idempotent(legacy_db):
    """Every boot runs this. Running it twice must not raise or duplicate."""
    otp_service.ensure_table()
    before = columns(legacy_db)
    otp_service.ensure_table()
    otp_service.ensure_table()
    assert columns(legacy_db) == before


def test_upgraded_row_is_readable_through_the_service(legacy_db):
    """A pre-upgrade verified challenge must still return a usable profile
    rather than raising on the columns that did not exist when it was written."""
    otp_service.ensure_table()
    profile = otp_service.profile_for("legacy-1")
    assert profile["first_name"] == ""
    assert profile["job"] == ""
    assert "*" in profile["destination_masked"]
