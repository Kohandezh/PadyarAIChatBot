"""PostgreSQL adapter and backup layer — the parts that must never regress.

These run WITHOUT a PostgreSQL server: they cover the pure logic (dialect
translation, id validation, path containment, confirmation strings) that
caused real failures during the migration. Behaviour that genuinely needs a
live server was proven against the real PostgreSQL 16 instance and is not
faked here — a test that mocks the database would not have caught any of the
four bugs the live runs found.
"""
import pytest


# ── Dialect translation ─────────────────────────────────────────────────

def test_question_mark_inside_a_persian_literal_is_not_a_placeholder():
    """The knowledge base is full of Persian questions. A blind ?->%s replace
    corrupts them and produces a silently wrong query."""
    from app.db.pg import translate
    out = translate("SELECT * FROM dataset WHERE title = 'اینوتکس چیست؟' AND id = ?")
    assert "چیست؟" in out
    assert out.count("%s") == 1


def test_placeholders_outside_literals_are_translated():
    from app.db.pg import translate
    assert translate("SELECT ? , ?").count("%s") == 2


def test_pragma_becomes_a_noop():
    """PRAGMA has no PostgreSQL meaning; it must not reach the server."""
    from app.db.pg import translate
    assert "false" in translate("PRAGMA journal_mode").lower()


def test_insert_or_ignore_gains_on_conflict():
    from app.db.pg import translate, needs_on_conflict
    original = "INSERT OR IGNORE INTO admins (username) VALUES (?)"
    assert "OR IGNORE" not in translate(original)
    assert "ON CONFLICT" in needs_on_conflict(original)


def test_sqlite_datetime_arithmetic_becomes_an_interval():
    from app.db.pg import translate
    assert "interval '-1 day'" in translate("SELECT datetime('now','-1 day')")


def test_a_literal_percent_survives():
    """psycopg treats % specially; a LIKE pattern must not be mangled."""
    from app.db.pg import translate
    assert "%%" in translate("SELECT * FROM t WHERE x LIKE '%foo%'")


# ── Row compatibility ───────────────────────────────────────────────────

def test_row_answers_to_both_name_and_index():
    """sqlite3.Row supported both and the codebase uses both; psycopg's
    dict_row supports only names."""
    from app.db.pg import Row
    r = Row({"a": 1, "b": 2})
    assert r["a"] == 1 and r[0] == 1 and r[1] == 2


# ── Backup id / path safety ─────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "/etc/passwd", "pg_../x", "", "pg_bad",
    "pg_20260101_000000_XYZZZZ", "pg_20260101_000000_abc",
])
def test_a_malformed_backup_id_is_refused(bad):
    from app.services import pg_backup
    with pytest.raises(pg_backup.BackupError):
        pg_backup._safe_dir(bad)


def test_a_wellformed_backup_id_resolves_inside_the_backup_directory():
    import os
    from app.services import pg_backup
    path = pg_backup._safe_dir("pg_20260101_120000_abc123")
    assert path.startswith(os.path.realpath(pg_backup.BACKUP_DIR) + os.sep)


def test_restore_requires_the_exact_typed_confirmation():
    from app.services import pg_backup
    for wrong in ("", "yes", "RESTORE BACKUP", "restore backup pg_20260101_120000_abc123"):
        with pytest.raises(pg_backup.BackupError):
            pg_backup.restore("pg_20260101_120000_abc123", actor="t", confirmation=wrong)


def test_the_password_never_reaches_a_command_line():
    """`ps` is world-readable on most hosts, so the password goes in the
    environment and nowhere else."""
    import inspect
    from app.services import pg_backup
    source = inspect.getsource(pg_backup)
    assert "PGPASSWORD" in source
    assert "--password" not in source


def test_backup_module_never_uses_a_shell():
    import inspect
    from app.services import pg_backup
    source = inspect.getsource(pg_backup)
    assert "shell=True" not in source
    assert "os.system" not in source


# ── Database admin allowlist ────────────────────────────────────────────

def test_pg_admin_refuses_an_unknown_action_and_runs_nothing():
    from app.services import pg_admin
    with pytest.raises(ValueError):
        pg_admin.run_action("DROP TABLE admins", actor="attacker")
    with pytest.raises(ValueError):
        pg_admin.run_action("vacuum_full", actor="attacker")


def test_pg_admin_offers_no_locking_operation():
    """VACUUM FULL and REINDEX take ACCESS EXCLUSIVE locks and would freeze the
    chatbot. Routine vacuuming is autovacuum's job."""
    from app.services import pg_admin
    names = {a["name"] for a in pg_admin.available_actions()}
    assert not (names & {"vacuum_full", "reindex", "vacuum", "cluster"})


def test_pg_admin_builds_no_sql_from_input():
    import inspect
    from app.services import pg_admin
    source = inspect.getsource(pg_admin)
    for danger in ("f\"SELECT", "f'SELECT", "% (action", ".format("):
        assert danger not in source, f"dynamic SQL construct {danger!r} present"


# ── Migration coercion ──────────────────────────────────────────────────

def test_sqlite_naive_timestamps_are_treated_as_utc():
    """The application wrote datetime.utcnow().isoformat() everywhere, so a
    naive value genuinely means UTC. Assuming local time would silently shift
    every historical record."""
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "migtool", pathlib.Path("scripts/migrate_sqlite_to_postgres.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dt = mod._ts("2026-08-14T20:59:11")
    assert dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0
    assert mod._ts("") is None
    assert mod._bool(1) is True and mod._bool("0") is False and mod._bool(None) is False
