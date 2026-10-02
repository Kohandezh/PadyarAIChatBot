"""What the operator reads: one name per check, plain Persian, honest failures.

No server is used. A restore failure is made by replacing `pg_backup._run` or
`subprocess.run`, and the check names are compared with the page script.

Two English things stay on purpose, because SPEC-B2 asks for them: the token
`drill database missing` and the word `DDL` (with its Persian gloss). Nothing
else in a sentence the operator reads may be English.
"""
import re
import subprocess
from pathlib import Path

import pytest

from app.services import pg_backup, restore_drill

REPO = Path(__file__).resolve().parent.parent
JS = (REPO / "static/admin/js/infra_backups.js").read_text(encoding="utf-8")

ALLOWED_ENGLISH = ("drill database missing", "DDL", "_drill")


def _english_left(text: str) -> list:
    for token in ALLOWED_ENGLISH:
        text = text.replace(token, "")
    return re.findall(r"[A-Za-z][A-Za-z0-9_./-]*", text)


# ── One name per check, the same on the server and the page ─────────────

def _js_check_labels() -> dict:
    body = re.search(r"const CHECK_LABELS = \[(.*?)\];", JS, re.S).group(1)
    return dict(re.findall(r"\[\s*'([a-z_]+)'\s*,\s*'([^']+)'\s*\]", body))


def test_the_server_check_names_equal_the_page_check_names():
    assert _js_check_labels() == restore_drill._CHECK_LABELS


def test_the_three_names_are_the_agreed_ones():
    assert restore_drill._CHECK_LABELS == {
        "row_counts": "تعداد ردیف‌ها",
        "schema_migrations": "نسخه‌های پایگاه داده",
        "validation": "سلامت پایگاه داده",
    }


# ── Plain Persian in every sentence the operator can read ───────────────

def _block(**checks):
    block = restore_drill._new_block("pytest")
    block["checks"].update(checks)
    return block


def test_a_failed_row_count_reason_is_plain_persian_with_the_ddl_sentence():
    block = _block(row_counts="failed", schema_migrations="passed",
                   validation="passed")
    block["mismatches"] = [{"table": "app.admins", "expected": 2, "actual": 1}]

    restore_drill._conclude(block)

    assert block["status"] == "failed"
    assert "DDL" in block["reason"] and "تغییر ساختار جدول" in block["reason"]
    assert "تعداد ردیف‌ها" in block["reason"]
    assert _english_left(block["reason"]) == []


def test_every_failed_check_reason_uses_the_page_names_and_no_jargon():
    block = _block(row_counts="failed", schema_migrations="failed",
                   validation="failed")
    block["mismatches"] = [{"table": "t", "expected": 1, "actual": 0}]

    restore_drill._conclude(block)

    for name in restore_drill._CHECK_LABELS.values():
        assert name in block["reason"]
    assert _english_left(block["reason"]) == []


def test_a_passed_reason_with_skipped_checks_is_plain_persian():
    block = _block(row_counts="skipped", schema_migrations="skipped",
                   validation="passed")

    restore_drill._conclude(block)

    assert block["status"] == "passed"
    assert restore_drill._CHECK_LABELS["row_counts"] in block["reason"]
    assert _english_left(block["reason"]) == []


def test_the_other_fixed_reasons_are_plain_persian():
    texts = [restore_drill.LOCK_FAILED_REASON_FA, restore_drill._LOGS_HINT,
             restore_drill._RESTORE_WHAT]
    for live in ("", "padyar_inotex_drill", "a" * 70):
        with pytest.raises(restore_drill.DrillRefused) as caught:
            restore_drill.drill_db_name(live)
        texts.append(caught.value.message_fa)
    for text in texts:
        assert _english_left(text) == [], text


def test_the_reasons_never_name_a_tool_or_a_script():
    block = _block(row_counts="failed")
    block["mismatches"] = [{"table": "t", "expected": 1, "actual": 0}]
    restore_drill._conclude(block)
    source = Path(restore_drill.__file__).read_text(encoding="utf-8")
    sentences = re.findall(r'"([^"\n]*[؀-ۿ][^"\n]*)"', source)
    for text in sentences + [block["reason"]]:
        assert "pg_dump" not in text and "pg_restore" not in text, text
        assert ".sh" not in text, text


# ── A restore failure keeps its real error ──────────────────────────────

@pytest.fixture
def restore_args(monkeypatch):
    monkeypatch.setattr(restore_drill.applog, "error", lambda *a, **kw: None)
    block = restore_drill._new_block("pytest")
    parts = {"host": "h", "port": "5432", "user": "u", "password": "",
             "dbname": "padyar_x"}
    drill_parts = {**parts, "dbname": "padyar_x_drill"}
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: name)
    monkeypatch.setattr(pg_backup, "_dump_path", lambda backup_id: "/x/padyar.dump")
    return block, parts, drill_parts


def _restore(restore_args):
    block, parts, drill_parts = restore_args
    restore_drill._restore_and_check("pg_x", {}, block, parts, drill_parts)
    return block


def test_run_raises_a_timeout_error_that_is_still_a_backup_error(monkeypatch):
    def expired(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", expired)

    with pytest.raises(pg_backup.BackupTimeout) as caught:
        pg_backup._run(["x"], {}, "کاری", timeout=1)
    assert isinstance(caught.value, pg_backup.BackupError)
    assert "از حد زمانی گذشت" in caught.value.message_fa


def test_run_raises_a_plain_backup_error_for_a_non_zero_exit(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: Failed())

    with pytest.raises(pg_backup.BackupError) as caught:
        pg_backup._run(["x"], {}, "کاری")
    assert not isinstance(caught.value, pg_backup.BackupTimeout)


def test_a_timeout_says_it_took_longer_than_the_allowed_time(
        restore_args, monkeypatch):
    def expired(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", expired)

    block = _restore(restore_args)

    minutes = str(pg_backup._RESTORE_TIMEOUT // 60)
    assert block["status"] == "failed"
    assert minutes in block["reason"] and "دقیقه" in block["reason"]
    assert "از حد زمانی گذشت" in block["reason"]
    assert "خراب" not in block["reason"], "a timeout is not a damaged file"
    assert block["reason"].endswith(restore_drill._LOGS_HINT)
    assert _english_left(block["reason"]) == []


def test_the_timeout_minutes_follow_the_module_constant(restore_args, monkeypatch):
    monkeypatch.setattr(pg_backup, "_RESTORE_TIMEOUT", 600)

    def expired(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", expired)

    assert "10 دقیقه" in _restore(restore_args)["reason"]


def test_a_non_zero_exit_keeps_the_failure_text_and_the_logs_hint(
        restore_args, monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "pg_restore: error: could not create schema"

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: Failed())

    block = _restore(restore_args)

    assert block["status"] == "failed"
    assert "ناموفق بود" in block["reason"]
    assert restore_drill._RESTORE_WHAT in block["reason"]
    assert block["reason"].endswith(restore_drill._LOGS_HINT)
    assert "خراب" not in block["reason"], "nobody checked the file is damaged"
    assert "دقیقه" not in block["reason"]
    assert _english_left(block["reason"]) == []
    assert block["restore_duration_ms"] is not None


# ── The drill database is set up like the live one ──────────────────────

def test_the_deploy_script_gives_the_drill_database_the_live_search_path():
    script = (REPO / "deploy/05-create-databases.sh").read_text(encoding="utf-8")
    live = 'ALTER DATABASE ${db} SET search_path = app, observability, public;'
    drill = 'ALTER DATABASE ${drill_db} SET search_path = app, observability, public;'
    assert live in script
    assert drill in script
    assert script.index(drill) > script.index("CREATE DATABASE ${drill_db}")
