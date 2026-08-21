"""The generated admin login must not outlive its purpose.

Two regressions this pins, both found on a live install:
  * the file was written world-readable (0644), so a generated admin password
    sat on disk readable by every account on the host;
  * nothing ever removed it, so it stayed there for the life of the install
    even though the file itself says to delete it after logging in.
"""
import os
import stat

import pytest

from app.db.connection import _write_admin_credentials
from app.routers.admin import _retire_bootstrap_credentials


@pytest.fixture
def creds_file(tmp_path, monkeypatch):
    db = tmp_path / "chat_history.db"
    db.write_text("")
    monkeypatch.setattr("app.config.DB_PATH", str(db))
    return tmp_path / "ADMIN_CREDENTIALS.txt"


def test_written_readable_only_by_owner(creds_file):
    _write_admin_credentials("padyar", "s3cret", "blue")

    assert creds_file.exists()
    mode = stat.S_IMODE(os.stat(creds_file).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    assert not mode & (stat.S_IRGRP | stat.S_IROTH), "group/other can read it"


def test_contains_what_the_operator_needs(creds_file):
    _write_admin_credentials("padyar", "s3cret", "blue")
    body = creds_file.read_text()

    # The security answer matters as much as the password: the login form
    # requires it on EVERY login, not only for recovery.
    assert "padyar" in body
    assert "s3cret" in body
    assert "blue" in body


def test_removed_after_a_successful_login(creds_file):
    _write_admin_credentials("padyar", "s3cret", "blue")
    assert creds_file.exists()

    _retire_bootstrap_credentials("padyar", "127.0.0.1")

    assert not creds_file.exists()


def test_retiring_a_missing_file_is_not_an_error(creds_file):
    assert not creds_file.exists()
    _retire_bootstrap_credentials("padyar", "127.0.0.1")   # must not raise


def test_failure_never_breaks_the_login(creds_file, monkeypatch):
    _write_admin_credentials("padyar", "s3cret", "blue")

    def boom(_path):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "remove", boom)
    # A login that succeeded must not be turned into a failure by housekeeping.
    _retire_bootstrap_credentials("padyar", "127.0.0.1")
