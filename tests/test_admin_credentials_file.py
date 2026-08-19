"""The generated admin login must belong to the database that generated it.

Regression: `_write_admin_credentials` used to resolve its path from BASE_DIR.
Every test run pointed DB_PATH at a throwaway database, seeded an admin into
it, and overwrote the REAL installation's ADMIN_CREDENTIALS.txt with the
password of a temp database that was deleted seconds later — so the operator's
file said one thing and their live database said another, and they could not
log in. These tests pin the fix.
"""
import os
import re

import pytest

from app.db import connection


def read_password(path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"Password:\s*(\S+)", text)
    return m.group(1) if m else None


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A brand-new database in its own folder, with no admin yet."""
    import app.config as config
    db = tmp_path / "install" / "chat_history.db"
    db.parent.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(config, "ADMIN_USERNAME", "someone@admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")        # force generation
    monkeypatch.setattr(config, "ADMIN_SECURITY_ANSWER", "")
    return db


def test_credentials_land_next_to_their_own_database(fresh_db):
    connection.init_db()
    written = fresh_db.parent / "ADMIN_CREDENTIALS.txt"
    assert written.exists(), "the generated login was not written beside its database"


def test_a_throwaway_database_never_touches_another_installs_file(fresh_db, tmp_path):
    """The exact bug: seeding a temp DB must not rewrite an unrelated file."""
    other_install = tmp_path / "real-install"
    other_install.mkdir()
    victim = other_install / "ADMIN_CREDENTIALS.txt"
    victim.write_text("Password:         the-real-operators-password\n", encoding="utf-8")
    before = victim.read_text(encoding="utf-8")

    connection.init_db()

    assert victim.read_text(encoding="utf-8") == before, \
        "seeding one database overwrote another install's credentials file"


def test_the_written_password_actually_works(fresh_db):
    """A file whose password does not open the account is worse than no file."""
    import sqlite3
    from app.auth.security import verify_password

    connection.init_db()
    password = read_password(fresh_db.parent / "ADMIN_CREDENTIALS.txt")
    assert password, "no password line in the generated file"

    conn = sqlite3.connect(fresh_db)
    try:
        row = conn.execute(
            "SELECT password_hash FROM admins WHERE username = ?", ("someone@admin",)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "no admin row was created"
    assert verify_password(password, row[0]), \
        "the file's password does not match the stored hash"


def test_nothing_is_written_when_the_admin_already_exists(fresh_db):
    """Re-running init_db must not regenerate — that would invalidate a
    password the operator has already been given and possibly changed."""
    connection.init_db()
    written = fresh_db.parent / "ADMIN_CREDENTIALS.txt"
    first = written.read_text(encoding="utf-8")

    os.utime(written, (0, 0))
    connection.init_db()

    assert written.read_text(encoding="utf-8") == first, \
        "a second init_db regenerated the credentials file"
