"""The nightly restore drill, against a real server and a real pg_restore.

WHY THIS FILE EXISTS
--------------------
`verify()` proves an archive can be read. It does not prove a database comes
back from it. The drill restores a backup into a separate database and checks
the result against numbers recorded when the dump was taken. These tests run
that whole path with real `pg_dump` and `pg_restore`.

ISOLATION
---------
The drill needs two databases: the "live" one it backs up, and its twin
`<live>_drill`. This module creates a throwaway pair per run
(`padyar_drillsrc_<hex>` and `padyar_drillsrc_<hex>_drill`), migrates the
source with the real `scripts/apply_migrations.py`, and drops both at the end.
`DATABASE_URL` points at the source only inside a test (monkeypatch), so
`_conn_parts()` targets it. The `padyar` database and the real role are never
changed. The test role needs CREATEDB; without it the module skips.
"""
import json
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from tests.postgres.conftest import REPO_ROOT, dsn

PASSWORD_HASH = "$2b$12$" + "a" * 53


def _url_for(name: str) -> str:
    return urlparse(dsn())._replace(path="/" + name).geturl()


def _create_database(name: str) -> None:
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn(), autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0")
                  .format(sql.Identifier(name)))


def _drop_database(name: str) -> None:
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn(), autocommit=True) as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                  " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def _seed(url: str) -> None:
    """Rows validate_restored_database() needs to see after a restore."""
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO app.admins (username, password_hash)"
                  " VALUES ('drilladmin', %s)", (PASSWORD_HASH,))
        c.execute("INSERT INTO app.settings (key, value) VALUES ('k', 'v')")
        c.execute("INSERT INTO app.dataset (id, title, text)"
                  " VALUES ('d1', %s, %s)", ("غرفهٔ نمونه", "متن فارسی"))
        c.execute("INSERT INTO app.questions (question, dataset_id)"
                  " VALUES (%s, 'd1')", ("سؤال نمونه؟",))


@pytest.fixture(scope="module")
def source():
    """A migrated, seeded source database and its empty `_drill` twin."""
    import psycopg
    name = f"padyar_drillsrc_{secrets.token_hex(4)}"
    twin = name + "_drill"
    made = []
    try:
        try:
            _create_database(name)
            made.append(name)
            _create_database(twin)
            made.append(twin)
        except psycopg.errors.InsufficientPrivilege as exc:
            pytest.skip("the restore drill tests need CREATEDB to make a "
                        f"throwaway database pair; this role has none ({exc.sqlstate})")
        url = _url_for(name)
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("CREATE SCHEMA app")
            c.execute("CREATE SCHEMA observability")
        done = subprocess.run(
            [sys.executable, "scripts/apply_migrations.py"], cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        _seed(url)
        yield SimpleNamespace(name=name, twin=twin, url=url, twin_url=_url_for(twin))
    finally:
        for db in reversed(made):
            _drop_database(db)


@pytest.fixture
def drill(source, tmp_path, monkeypatch):
    """Point the backup module at the source database and a temp backup dir."""
    from app.services import pg_backup, restore_drill
    monkeypatch.setenv("DATABASE_URL", source.url)
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    # A dev machine's free disk is not what these tests are about. The disk
    # check has its own test that sets this itself.
    monkeypatch.setattr(restore_drill, "_free_bytes", lambda: 1 << 50)
    return source


def _backup() -> str:
    from app.services import pg_backup
    return pg_backup.create(actor="pytest", reason="drill")["backup_id"]


def _read(backup_id: str) -> dict:
    from app.services import pg_backup
    with open(pg_backup._manifest_path(backup_id), encoding="utf-8") as f:
        return json.load(f)


def _write(backup_id: str, manifest: dict) -> None:
    from app.services import pg_backup
    with open(pg_backup._manifest_path(backup_id), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _twin_schemas(source) -> set:
    import psycopg
    with psycopg.connect(source.twin_url, autocommit=True) as c:
        return {r[0] for r in c.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname IN"
            " ('app', 'observability')").fetchall()}


def _spy_run(monkeypatch):
    from app.services import pg_backup
    calls = []
    real = pg_backup._run

    def spy(argv, env, what, timeout=None):
        calls.append({"argv": list(argv), "what": what, "timeout": timeout})
        return real(argv, env, what, timeout=timeout)

    monkeypatch.setattr(pg_backup, "_run", spy)
    return calls


# ── The good path ───────────────────────────────────────────────────────

def test_a_good_backup_drills_passed_and_the_block_is_complete(drill):
    from app.services import restore_drill
    backup_id = _backup()
    before = _read(backup_id)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "passed", block
    assert block["mismatches"] == []
    assert isinstance(block["duration_ms"], int) and block["duration_ms"] >= 0
    assert isinstance(block["restore_duration_ms"], int)
    assert block["tables_checked"] == len(before["row_counts"]) > 0
    assert datetime.fromisoformat(block["checked_at"]).utcoffset() is not None
    assert block["checks"] == {"row_counts": "passed",
                               "schema_migrations": "passed",
                               "validation": "passed"}
    assert block["cleanup_ok"] is True
    assert block["drill_database"] == drill.twin
    assert block["actor"] == "pytest"
    assert isinstance(block["reason"], str) and block["reason"]
    assert set(block["tables"]) == set(before["row_counts"])
    assert all(t["expected"] == t["actual"] for t in block["tables"].values())

    after = _read(backup_id)
    assert after["drill"] == block
    assert {k: v for k, v in after.items() if k != "drill"} == before, \
        "the drill adds its block and changes nothing else"


def test_schema_migrations_are_recorded_in_the_manifest_at_create_time(drill):
    import psycopg
    backup_id = _backup()
    recorded = _read(backup_id)["schema_migrations"]

    with psycopg.connect(drill.url) as c:
        live = c.execute("SELECT version, checksum FROM app.schema_migrations"
                         " ORDER BY version").fetchall()
    assert recorded == [{"version": v, "checksum": s} for v, s in live]
    assert [r["version"] for r in recorded] == sorted(r["version"] for r in recorded)
    assert len(recorded) > 5


def test_a_database_without_schema_migrations_records_null_but_still_counts_rows(
        tmp_path, monkeypatch):
    import psycopg
    from app.services import pg_backup
    name = f"padyar_drillsrc_{secrets.token_hex(4)}"
    try:
        try:
            _create_database(name)
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("needs CREATEDB")
        with psycopg.connect(_url_for(name), autocommit=True) as c:
            c.execute("CREATE SCHEMA app")
            c.execute("CREATE TABLE app.only_table (id int)")
            c.execute("INSERT INTO app.only_table VALUES (1), (2)")
        monkeypatch.setenv("DATABASE_URL", _url_for(name))
        monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))

        manifest = pg_backup.create(actor="pytest", reason="no-migrations")
    finally:
        # DATABASE_URL must point back at the admin database first: a session
        # on the database being dropped would block the DROP.
        monkeypatch.undo()
        _drop_database(name)

    assert manifest["schema_migrations"] is None
    assert manifest["row_counts"] == {"app.only_table": 2}


# ── Cleanup ─────────────────────────────────────────────────────────────

def test_after_a_passed_drill_the_drill_database_has_no_app_schemas(drill):
    from app.services import restore_drill
    restore_drill.run(_backup(), actor="pytest")
    assert _twin_schemas(drill) == set()


def test_after_a_failed_drill_the_drill_database_is_empty_too(drill):
    from app.services import restore_drill
    backup_id = _backup()
    manifest = _read(backup_id)
    manifest["row_counts"]["app.admins"] += 1
    _write(backup_id, manifest)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "failed"
    assert block["cleanup_ok"] is True
    assert _twin_schemas(drill) == set()


def test_leftovers_from_a_crashed_drill_are_cleared_before_the_restore(drill):
    import psycopg
    from app.services import restore_drill
    with psycopg.connect(drill.twin_url, autocommit=True) as c:
        c.execute("CREATE SCHEMA app")
        c.execute("CREATE TABLE app.crashed_leftover (id int)")
    backup_id = _backup()

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "passed", block
    assert _twin_schemas(drill) == set()


# ── What a failed check looks like ──────────────────────────────────────

def test_a_row_count_mismatch_fails_the_drill_and_names_the_table(drill):
    from app.services import restore_drill
    backup_id = _backup()
    manifest = _read(backup_id)
    real = manifest["row_counts"]["app.admins"]
    manifest["row_counts"]["app.admins"] = real + 5
    manifest["row_counts"]["app.table_that_is_gone"] = 3
    _write(backup_id, manifest)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "failed"
    assert block["checks"]["row_counts"] == "failed"
    by_table = {m["table"]: m for m in block["mismatches"]}
    assert by_table["app.admins"] == {"table": "app.admins",
                                      "expected": real + 5, "actual": real}
    assert by_table["app.table_that_is_gone"]["actual"] is None
    assert len(by_table) == 2
    assert "DDL" in block["reason"], "the reason must mention DDL during the backup"
    assert block["tables_checked"] == len(manifest["row_counts"])
    assert _read(backup_id)["drill"]["status"] == "failed"


def test_row_counts_null_skips_that_check_and_the_drill_can_still_pass(drill):
    from app.services import restore_drill
    backup_id = _backup()
    manifest = _read(backup_id)
    manifest["row_counts"] = None
    manifest["row_counts_source"] = "unavailable"
    _write(backup_id, manifest)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "passed", block
    assert block["checks"]["row_counts"] == "skipped"
    assert block["checks"]["schema_migrations"] == "passed"
    assert block["checks"]["validation"] == "passed"
    assert block["tables_checked"] == 0


def test_schema_migrations_null_skips_that_check_an_old_manifest_has_none(drill):
    from app.services import restore_drill
    backup_id = _backup()
    manifest = _read(backup_id)
    del manifest["schema_migrations"]
    _write(backup_id, manifest)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "passed", block
    assert block["checks"]["schema_migrations"] == "skipped"


def test_a_schema_migrations_checksum_mismatch_fails_the_drill(drill):
    from app.services import restore_drill
    backup_id = _backup()
    manifest = _read(backup_id)
    manifest["schema_migrations"][0]["checksum"] = "0" * 64
    _write(backup_id, manifest)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "failed"
    assert block["checks"]["schema_migrations"] == "failed"
    assert block["checks"]["row_counts"] == "passed"
    assert block["reason"]


def test_a_restored_database_that_fails_validation_fails_the_drill(drill):
    import psycopg
    from app.services import restore_drill
    with psycopg.connect(drill.url, autocommit=True) as c:
        c.execute("UPDATE app.admins SET password_hash = 'plain'"
                  " WHERE username = 'drilladmin'")
    try:
        backup_id = _backup()
        block = restore_drill.run(backup_id, actor="pytest")
    finally:
        with psycopg.connect(drill.url, autocommit=True) as c:
            c.execute("UPDATE app.admins SET password_hash = %s"
                      " WHERE username = 'drilladmin'", (PASSWORD_HASH,))

    assert block["status"] == "failed"
    assert block["checks"]["validation"] == "failed"
    assert block["checks"]["row_counts"] == "passed"
    assert "password_hashes_intact" in json.dumps(block, ensure_ascii=False)


# ── Skipped, not crashed ────────────────────────────────────────────────

def test_a_missing_drill_database_is_skipped_not_failed_and_nothing_raises(
        drill, monkeypatch):
    import psycopg
    from psycopg import sql
    from app.services import restore_drill
    backup_id = _backup()
    calls = _spy_run(monkeypatch)
    renamed = drill.twin + "_away"
    with psycopg.connect(dsn(), autocommit=True) as c:
        c.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
            sql.Identifier(drill.twin), sql.Identifier(renamed)))
    try:
        block = restore_drill.run(backup_id, actor="pytest")
    finally:
        with psycopg.connect(dsn(), autocommit=True) as c:
            c.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                sql.Identifier(renamed), sql.Identifier(drill.twin)))

    assert block["status"] == "skipped"
    # SPEC-B2 asks for this exact English token. The rest is plain Persian.
    assert "drill database missing" in block["reason"]
    assert "05-create-databases.sh" not in block["reason"]
    assert not calls, "no restore may start without the drill database"
    assert _read(backup_id)["drill"]["status"] == "skipped"


def test_not_enough_disk_is_skipped_before_any_restore(drill, monkeypatch):
    from app.services import restore_drill
    backup_id = _backup()
    calls = _spy_run(monkeypatch)
    monkeypatch.setattr(restore_drill, "_free_bytes", lambda: 100 * 1024 * 1024)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "skipped"
    assert "100" in block["reason"] and "مگابایت" in block["reason"]
    assert not calls
    assert block["restore_duration_ms"] is None
    assert _twin_schemas(drill) == set()


def test_enough_disk_is_the_allow_control(drill, monkeypatch):
    from app.services import restore_drill
    import psycopg
    backup_id = _backup()
    with psycopg.connect(drill.url, autocommit=True) as c:
        live = c.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    need = int(live * restore_drill._DISK_FACTOR) + restore_drill._DISK_MARGIN
    monkeypatch.setattr(restore_drill, "_free_bytes", lambda: need)

    assert restore_drill.run(backup_id, actor="pytest")["status"] == "passed"


def test_the_real_free_space_probe_returns_a_positive_number(tmp_path, monkeypatch):
    from app.services import pg_backup, restore_drill
    # The folder does not exist yet on a fresh install. The probe must not crash.
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "not" / "yet"))
    free = restore_drill._free_bytes()
    assert isinstance(free, int) and free > 0


def test_a_live_database_that_already_ends_in_drill_is_refused(drill, monkeypatch):
    from app.services import restore_drill
    backup_id = _backup()
    calls = _spy_run(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", drill.twin_url)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "skipped"
    assert not calls
    assert _twin_schemas(drill) == set()


# ── A broken dump ───────────────────────────────────────────────────────

def test_a_pg_restore_failure_is_failed_cleanup_still_ran_and_the_dump_is_kept(
        drill):
    import hashlib
    import psycopg
    from app.services import pg_backup, restore_drill
    backup_id = _backup()
    dump = pg_backup._dump_path(backup_id)
    with open(dump, "wb") as f:
        f.write(b"this is not a pg_dump archive")
    before = hashlib.sha256(open(dump, "rb").read()).hexdigest()
    with psycopg.connect(drill.twin_url, autocommit=True) as c:
        c.execute("CREATE SCHEMA app")

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "failed"
    assert block["checks"] == {"row_counts": "skipped",
                               "schema_migrations": "skipped",
                               "validation": "skipped"}
    assert block["cleanup_ok"] is True
    assert _twin_schemas(drill) == set()
    assert os.path.exists(dump)
    assert hashlib.sha256(open(dump, "rb").read()).hexdigest() == before


# ── The live database is never the target ───────────────────────────────

def test_pg_restore_targets_the_drill_database_and_no_password_is_on_argv(
        drill, monkeypatch):
    from app.services import restore_drill
    backup_id = _backup()
    password = urlparse(dsn()).password
    calls = _spy_run(monkeypatch)

    restore_drill.run(backup_id, actor="pytest")

    restores = [c for c in calls if c["argv"][0].endswith("pg_restore")]
    assert len(restores) == 1
    argv = restores[0]["argv"]
    assert argv[argv.index("--dbname") + 1] == drill.twin
    assert drill.name not in argv
    for flag in ("--clean", "--if-exists", "--no-owner", "--no-privileges",
                 "--single-transaction"):
        assert flag in argv
    from app.services import pg_backup
    assert restores[0]["timeout"] == pg_backup._RESTORE_TIMEOUT
    assert password and not any(password in a for c in calls for a in c["argv"])


def test_a_destructive_step_refuses_to_run_on_the_wrong_database(drill):
    import psycopg
    from app.services import restore_drill
    with psycopg.connect(drill.url, autocommit=True) as live:
        with pytest.raises(restore_drill.DrillRefused):
            restore_drill._drop_app_schemas(live, drill.twin)
        count = live.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = 'app'").fetchone()[0]
    assert count == 1, "the live app schema must survive"


# ── One drill at a time ─────────────────────────────────────────────────

def test_a_second_drill_is_refused_while_the_first_holds_the_lock_then_runs(drill):
    import psycopg
    from app.services import restore_drill
    backup_id = _backup()
    holder = psycopg.connect(drill.url, autocommit=True)
    try:
        holder.execute("SELECT pg_advisory_lock(%s)", (restore_drill._LOCK_KEY,))
        with pytest.raises(restore_drill.DrillAlreadyRunning):
            restore_drill.run(backup_id, actor="pytest")
        with pytest.raises(restore_drill.DrillAlreadyRunning):
            restore_drill.start(backup_id, "pytest")
        assert "drill" not in _read(backup_id), "a refused drill writes nothing"
        holder.execute("SELECT pg_advisory_unlock(%s)", (restore_drill._LOCK_KEY,))
    finally:
        holder.close()

    assert restore_drill.run(backup_id, actor="pytest")["status"] == "passed"


def test_a_broken_lock_connection_runs_no_drill_and_cleans_nothing(
        drill, monkeypatch):
    """Fail closed: when the lock can not be taken, nothing touches the twin."""
    import psycopg
    from app.services import pg_backup, restore_drill
    backup_id = _backup()
    calls = _spy_run(monkeypatch)
    real_connect = pg_backup._connect

    def connect(parts, application_name, options):
        if application_name == "padyar-restore-drill-lock":
            raise psycopg.OperationalError("lock connection down")
        return real_connect(parts, application_name, options)

    monkeypatch.setattr(pg_backup, "_connect", connect)
    # A leftover schema in the twin proves nothing was cleaned either.
    with psycopg.connect(drill.twin_url, autocommit=True) as c:
        c.execute("CREATE SCHEMA app")
    try:
        block = restore_drill.run(backup_id, actor="pytest")
        assert block["status"] == "skipped"
        assert block["reason"] == restore_drill.LOCK_FAILED_REASON_FA
        assert not calls, "no restore may start without the lock"
        assert _read(backup_id)["drill"]["status"] == "skipped"
        assert _twin_schemas(drill) == {"app"}, "the drill database was left alone"
        with pytest.raises(restore_drill.DrillLockUnavailable):
            restore_drill.start(backup_id, "pytest")
        assert not calls
    finally:
        with psycopg.connect(drill.twin_url, autocommit=True) as c:
            c.execute("DROP SCHEMA IF EXISTS app CASCADE")

    # Allow-control: with the lock connection healthy again, the drill runs.
    monkeypatch.setattr(pg_backup, "_connect", real_connect)
    assert restore_drill.run(backup_id, actor="pytest")["status"] == "passed"
    assert calls, "the restore ran once the lock worked"


def test_the_lock_is_released_after_a_drill_and_after_a_failed_one(drill):
    import psycopg
    from app.services import restore_drill
    backup_id = _backup()
    manifest = _read(backup_id)
    manifest["row_counts"]["app.admins"] += 1
    _write(backup_id, manifest)

    restore_drill.run(backup_id, actor="pytest")

    with psycopg.connect(drill.url, autocommit=True) as c:
        got = c.execute("SELECT pg_try_advisory_lock(%s)",
                        (restore_drill._LOCK_KEY,)).fetchone()[0]
        c.execute("SELECT pg_advisory_unlock(%s)", (restore_drill._LOCK_KEY,))
    assert got is True


def test_start_runs_the_drill_in_a_thread_and_the_block_appears_after_join(drill):
    from app.services import restore_drill
    backup_id = _backup()

    thread = restore_drill.start(backup_id, "pytest")

    assert isinstance(thread, threading.Thread) and thread.daemon
    thread.join(180)
    assert not thread.is_alive()
    block = _read(backup_id)["drill"]
    assert block["status"] == "passed", block
    assert block["actor"] == "pytest"
    assert restore_drill.run(backup_id, actor="pytest")["status"] == "passed", \
        "the thread released the lock"


def test_start_and_run_refuse_a_bad_backup_id_and_take_no_lock(drill):
    from app.services import pg_backup, restore_drill
    for bad in ("../../etc/passwd", "", "pg_bad", "pg_20260101_120000_zzzzzz"):
        with pytest.raises(pg_backup.BackupError):
            restore_drill.start(bad, "pytest")
        with pytest.raises(pg_backup.BackupError):
            restore_drill.run(bad, "pytest")
    # A well-formed id with no backup behind it.
    with pytest.raises(pg_backup.BackupError):
        restore_drill.start("pg_20260101_120000_abcdef", "pytest")
    assert restore_drill.run(_backup(), actor="pytest")["status"] == "passed"


# ── validate_restored_database(conn=...) ────────────────────────────────

def test_validate_runs_on_a_given_connection_and_leaves_it_open(drill):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from app.services import pg_backup
    backup_id = _backup()
    parts = pg_backup._conn_parts()
    pg_backup._run([pg_backup._pg_bin("pg_restore"),
                    "--host", parts["host"], "--port", parts["port"],
                    "--username", parts["user"], "--dbname", drill.twin,
                    "--no-owner", "--no-privileges", "--single-transaction",
                    pg_backup._dump_path(backup_id)],
                   pg_backup._env(parts), "pg_restore (test)")
    conn = psycopg.connect(drill.twin_url, autocommit=True, row_factory=dict_row)
    try:
        result = pg_backup.validate_restored_database(conn=conn)
        assert result["ok"] is True, result
        assert result["checks"]["reachable"]["ok"] is True
        assert not conn.closed, "the caller owns the connection"
        conn.execute("SELECT 1")
    finally:
        for schema in ("app", "observability"):
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(schema)))
        conn.close()


def test_validate_reports_a_dead_given_connection_as_not_reachable(drill):
    import psycopg
    from psycopg.rows import dict_row
    from app.services import pg_backup
    conn = psycopg.connect(drill.twin_url, autocommit=True, row_factory=dict_row)
    conn.close()

    result = pg_backup.validate_restored_database(conn=conn)

    assert result["ok"] is False
    assert result["problems"] == ["reachable"]


# ── The block is the last visible step (final review, item 1) ───────────

def test_when_the_block_is_visible_the_lock_is_free_and_the_gauges_are_set(
        drill, monkeypatch):
    """block_in_manifest=True implies lock_free=True and the gauges set.

    A client that sees the block takes the drill as done. It may start the next
    drill at once (needs the lock) or scrape /metrics (needs the final values).
    The probe runs from a SEPARATE connection at the moment os.replace makes
    the block visible.
    """
    import psycopg
    from app.services import metrics, pg_backup, restore_drill
    backup_id = _backup()
    manifest_file = pg_backup._manifest_path(backup_id)
    metrics.backup_drill_last_ok.set(0)  # the value a stale scrape would show
    seen = []
    real_replace = os.replace

    def probe(src, dst, *a, **kw):
        real_replace(src, dst, *a, **kw)
        if os.path.abspath(dst) != os.path.abspath(manifest_file):
            return
        with open(manifest_file, encoding="utf-8") as f:
            visible = "drill" in json.load(f)
        with psycopg.connect(drill.url, autocommit=True) as c:
            free = c.execute("SELECT pg_try_advisory_lock(%s)",
                             (restore_drill._LOCK_KEY,)).fetchone()[0]
            if free:
                c.execute("SELECT pg_advisory_unlock(%s)", (restore_drill._LOCK_KEY,))
        seen.append({"block_in_manifest": visible, "lock_free": free,
                     "ok_gauge": metrics.backup_drill_last_ok._value.get()})

    monkeypatch.setattr(os, "replace", probe)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "passed", block
    assert seen == [{"block_in_manifest": True, "lock_free": True,
                     "ok_gauge": 1.0}]


# ── A leftover copy must not keep the drill skipped (item 2) ────────────

def test_a_leftover_copy_is_dropped_even_when_the_disk_check_skips_the_drill(
        drill, monkeypatch):
    """The leftover uses the same disk the check measures.

    If the check ran first, a leftover could push free space under the limit
    and nothing would ever drop it. The clean-up runs before the check.
    """
    import psycopg
    from app.services import restore_drill
    backup_id = _backup()
    calls = _spy_run(monkeypatch)
    with psycopg.connect(drill.twin_url, autocommit=True) as c:
        c.execute("CREATE SCHEMA app")
        c.execute("CREATE TABLE app.crashed_leftover (id int)")
    assert _twin_schemas(drill) == {"app"}
    monkeypatch.setattr(restore_drill, "_free_bytes", lambda: 100 * 1024 * 1024)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "skipped"
    assert "مگابایت" in block["reason"]
    assert _twin_schemas(drill) == set(), "the leftover copy must be gone"
    assert not calls, "no restore may start when the disk is too small"


def test_a_missing_drill_database_has_nothing_to_clean_and_nothing_is_touched(
        drill, monkeypatch):
    """The clean-up needs the drill database. Without it the drill just skips."""
    import psycopg
    from psycopg import sql
    from app.services import restore_drill
    backup_id = _backup()
    renamed = drill.twin + "_away"
    with psycopg.connect(dsn(), autocommit=True) as c:
        c.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
            sql.Identifier(drill.twin), sql.Identifier(renamed)))
    try:
        block = restore_drill.run(backup_id, actor="pytest")
    finally:
        with psycopg.connect(dsn(), autocommit=True) as c:
            c.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                sql.Identifier(renamed), sql.Identifier(drill.twin)))
    assert block["status"] == "skipped"
    assert "drill database missing" in block["reason"]


# ── The real restore error stays in the reason (item 3) ─────────────────

def test_a_pg_restore_that_exits_non_zero_names_the_failure_not_a_damaged_file(
        drill):
    from app.services import pg_backup, restore_drill
    backup_id = _backup()
    with open(pg_backup._dump_path(backup_id), "wb") as f:
        f.write(b"this is not a pg_dump archive")

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "failed"
    assert "ناموفق بود" in block["reason"]
    assert "جزئیات در بخش گزارش‌ها ثبت شده است." in block["reason"]
    assert "خراب" not in block["reason"], "nobody checked the file is damaged"
    assert "timeout" not in block["reason"].lower()


def test_a_pg_restore_that_times_out_says_it_took_too_long(drill, monkeypatch):
    import subprocess
    from app.services import pg_backup, restore_drill
    backup_id = _backup()
    real_run = subprocess.run

    def run(argv, *a, **kw):
        if os.path.basename(argv[0]) == "pg_restore" and "--dbname" in argv:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(pg_backup.subprocess, "run", run)

    block = restore_drill.run(backup_id, actor="pytest")

    assert block["status"] == "failed"
    minutes = str(pg_backup._RESTORE_TIMEOUT // 60)
    assert minutes in block["reason"] and "دقیقه" in block["reason"]
    assert "خراب" not in block["reason"]
    assert block["cleanup_ok"] is True
    assert _twin_schemas(drill) == set()
