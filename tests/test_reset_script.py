"""Tests for the operator reset/migration script (R1).

Verifies the explicit, backed-up reset path operators use to wipe a legacy
(Noor/Padyar/medical) install and restore the verifiable INOTEX defaults —
while preserving admin accounts and (by default) chat logs and settings.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "reset-content-to-defaults.py"


def _legacy_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE dataset (id TEXT PRIMARY KEY, title TEXT, text TEXT, video_url TEXT DEFAULT '');
        CREATE TABLE questions (id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, dataset_id TEXT, video_url TEXT DEFAULT '');
        CREATE TABLE synonyms (source TEXT NOT NULL, target TEXT NOT NULL,
                               PRIMARY KEY (source, target));
        CREATE TABLE chat_logs (id INTEGER PRIMARY KEY, query TEXT);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE admins (username TEXT PRIMARY KEY);
        """
    )
    # Legacy Noor/medical content + a remote video dependency.
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url)"
        " VALUES ('vid_lasik','لیزر لیزیک','پزشکی','http://noor/x.mp4')"
    )
    conn.execute("INSERT INTO questions (question,dataset_id,video_url) VALUES ('هزینه لیزیک','vid_lasik','')")
    conn.execute("INSERT INTO synonyms VALUES ('لیزیک','لیزر لیزیک')")
    conn.execute("INSERT INTO chat_logs (query) VALUES ('old query')")
    conn.execute("INSERT INTO admins VALUES ('the-boss')")
    conn.execute("INSERT INTO settings VALUES ('active_theme','liquid-glass')")
    conn.commit()
    conn.close()


def _run(db: Path, *flags):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--yes", *flags],
        capture_output=True, text=True, cwd=str(ROOT),
    )


def test_reset_replaces_legacy_content_with_inotex_defaults(tmp_path):
    db = tmp_path / "chat_history.db"
    _legacy_db(db)

    r = _run(db)
    assert r.returncode == 0, r.stderr

    conn = sqlite3.connect(str(db))
    # Legacy medical row is gone; INOTEX defaults present.
    assert conn.execute("SELECT COUNT(*) FROM dataset WHERE id='vid_lasik'").fetchone()[0] == 0
    # Every seeded default landed, whatever the knowledge base currently holds.
    from app.default_content import INOTEX_DATASET, INOTEX_QUESTIONS, INOTEX_SYNONYMS
    assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == len(INOTEX_DATASET)
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == len(INOTEX_QUESTIONS)
    assert conn.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == len(INOTEX_SYNONYMS)
    conn.close()


def test_reset_preserves_admin_settings_and_logs_by_default(tmp_path):
    db = tmp_path / "chat_history.db"
    _legacy_db(db)
    assert _run(db).returncode == 0

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM chat_logs").fetchone()[0] == 1  # preserved
    conn.close()


def test_reset_full_clears_logs(tmp_path):
    db = tmp_path / "chat_history.db"
    _legacy_db(db)
    assert _run(db, "--full").returncode == 0
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM chat_logs").fetchone()[0] == 0
    conn.close()


def test_reset_always_creates_backup(tmp_path):
    db = tmp_path / "chat_history.db"
    _legacy_db(db)
    assert _run(db).returncode == 0
    backups = list(tmp_path.glob("*.backup.*"))
    assert len(backups) == 1, backups


def test_reset_aborts_without_confirmation(tmp_path):
    db = tmp_path / "chat_history.db"
    _legacy_db(db)
    # No --yes and no stdin → should abort without changes.
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db)],
        input="", capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode == 0
    conn = sqlite3.connect(str(db))
    # Legacy content untouched.
    assert conn.execute("SELECT COUNT(*) FROM dataset WHERE id='vid_lasik'").fetchone()[0] == 1
    conn.close()


def test_reset_fails_safely_on_missing_db(tmp_path):
    db = tmp_path / "does-not-exist.db"
    r = _run(db)
    assert r.returncode != 0
    assert "not found" in r.stdout.lower() or "not found" in r.stderr.lower()
