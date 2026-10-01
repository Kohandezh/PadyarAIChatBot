"""A backup records its own row counts, read from the snapshot pg_dump used.

WHY THIS FILE EXISTS
--------------------
The nightly restore drill (next PR) restores a dump and compares each table's
row count against the source. Counting the live database at drill time gives
different numbers: rows arrive between the dump and the drill, so a perfect
restore would look broken. The only honest reference is a count taken from
the SAME snapshot pg_dump read. `create()` now exports that snapshot, counts
inside it, hands it to `pg_dump --snapshot=<id>`, and stores the counts in
`manifest.json`.

What is asserted against a real server and a real pg_dump:

  * every app/observability table in the dump has a count, the keys are
    sorted, and each count equals the rows actually inside the dump file;
  * rows another session commits after the snapshot export (and before
    pg_dump runs) are in neither the counts nor the dump, while the live
    table does have them. Without `--snapshot` the dump would hold them and
    the counts would not;
  * deny-case: a table in a schema the app does not own is not counted;
    allow-control: an `app.*` table in the same database is;
  * a pg_dump failure still fails the backup, and the snapshot session is
    closed on every path (a leaked open transaction would hold back vacuum).

The probe tables live in the real `app` schema (and one extra schema), under
a random name, and are dropped afterwards. None of them is in the session
guard's LIVE_TABLES, so the guard still proves live content did not move.
"""
import json
import os
import re
import secrets
import subprocess

import pytest

from tests.postgres.conftest import dsn

SNAPSHOT_APP_NAME = "padyar-backup-snapshot"


def _dump_row_counts(dump_path: str) -> dict:
    """Rows per table INSIDE the archive, read back with pg_restore.

    COPY text format writes one line per row (newlines inside values are
    escaped), so the lines between a COPY header and `\\.` are the rows.
    """
    from app.services import pg_backup

    out = subprocess.run(
        [pg_backup._pg_bin("pg_restore"), "--data-only", "--file", "-", dump_path],
        capture_output=True, text=True, check=True).stdout
    counts, current = {}, None
    for line in out.splitlines():
        if current is None:
            m = re.match(r'^COPY ("?[^".]+"?)\.("?[^"(]+?"?)\s*(\(.*\))? FROM stdin;$',
                         line)
            if m:
                current = f"{m.group(1).strip(chr(34))}.{m.group(2).strip(chr(34))}"
                counts[current] = 0
        elif line == "\\.":
            current = None
        else:
            counts[current] += 1
    return counts


def _owned(counts: dict) -> dict:
    return {k: v for k, v in counts.items()
            if k.split(".", 1)[0] in ("app", "observability")}


def _snapshot_sessions() -> int:
    import psycopg
    with psycopg.connect(dsn(), autocommit=True) as c:
        return c.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s",
            (SNAPSHOT_APP_NAME,)).fetchone()[0]


@pytest.fixture
def backups(tmp_path, monkeypatch):
    from app.services import pg_backup
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(tmp_path / "pg"))
    return pg_backup


@pytest.fixture
def probe():
    """`app.<probe>` with 3 rows, and `<other>.<probe>` with 2 rows."""
    import psycopg
    name = f"counts_probe_{secrets.token_hex(3)}"
    other = f"counts_other_{secrets.token_hex(3)}"
    with psycopg.connect(dsn(), autocommit=True) as c:
        c.execute(f'CREATE TABLE app."{name}" (id int PRIMARY KEY, note text)')
        c.execute(f'INSERT INTO app."{name}" VALUES (1, %s), (2, %s), (3, %s)',
                  ("یک", "two\nlines", "سه"))
        c.execute(f'CREATE SCHEMA "{other}"')
        c.execute(f'CREATE TABLE "{other}"."{name}" (id int)')
        c.execute(f'INSERT INTO "{other}"."{name}" VALUES (1), (2)')
    try:
        yield {"table": name, "schema": other}
    finally:
        with psycopg.connect(dsn(), autocommit=True) as c:
            c.execute(f'DROP TABLE IF EXISTS app."{name}"')
            c.execute(f'DROP SCHEMA IF EXISTS "{other}" CASCADE')


def _read_manifest(pg_backup, backup_id: str) -> dict:
    with open(pg_backup._manifest_path(backup_id), encoding="utf-8") as f:
        return json.load(f)


def test_every_owned_table_in_the_dump_has_a_count_equal_to_its_dumped_rows(
        backups, probe):
    manifest = backups.create(actor="pytest", reason="counts")

    on_disk = _read_manifest(backups, manifest["backup_id"])
    counts = on_disk["row_counts"]
    assert on_disk["row_counts_source"] == "dump_snapshot"
    assert list(counts) == sorted(counts), "keys must be stored sorted"
    assert all(isinstance(v, int) for v in counts.values())

    dumped = _owned(_dump_row_counts(backups._dump_path(manifest["backup_id"])))
    assert counts == dumped, "every dumped table, and only those, with the dumped row count"
    assert counts[f"app.{probe['table']}"] == 3
    assert "app.admins" in counts and "observability.app_logs" in counts
    assert manifest["row_counts"] == counts


def test_rows_committed_after_the_snapshot_are_in_neither_the_counts_nor_the_dump(
        backups, probe, monkeypatch):
    import psycopg
    key = f"app.{probe['table']}"
    seen = {}
    real_run = backups._run

    def insert_then_dump(argv, env, what):
        if what == "pg_dump":
            seen["argv"] = list(argv)
            with psycopg.connect(dsn(), autocommit=True) as c:
                c.execute(f'INSERT INTO app."{probe["table"]}" '
                          "SELECT g, 'late' FROM generate_series(100, 149) g")
        return real_run(argv, env, what)

    monkeypatch.setattr(backups, "_run", insert_then_dump)
    manifest = backups.create(actor="pytest", reason="race")

    with psycopg.connect(dsn(), autocommit=True) as c:
        live = c.execute(f'SELECT count(*) FROM app."{probe["table"]}"').fetchone()[0]
    assert live == 53, "the concurrent insert really happened"
    dumped = _dump_row_counts(backups._dump_path(manifest["backup_id"]))
    assert manifest["row_counts"][key] == 3
    assert dumped[key] == 3, "pg_dump read the exported snapshot, not a fresh one"
    assert manifest["row_counts"] == _owned(dumped)
    assert any(a.startswith("--snapshot=") for a in seen["argv"]), seen["argv"]


def test_a_table_outside_the_app_schemas_is_not_counted_but_an_app_table_is(
        backups, probe):
    manifest = backups.create(actor="pytest", reason="scope")

    dumped = _dump_row_counts(backups._dump_path(manifest["backup_id"]))
    foreign = f"{probe['schema']}.{probe['table']}"
    assert dumped.get(foreign) == 2, "the foreign table IS in the dump"
    assert foreign not in manifest["row_counts"]
    assert not any(k.startswith(probe["schema"] + ".") for k in manifest["row_counts"])
    assert manifest["row_counts"][f"app.{probe['table']}"] == 3


def test_no_password_reaches_the_pg_dump_command_line(backups, probe, monkeypatch):
    from urllib.parse import urlparse
    password = urlparse(dsn()).password
    seen = []
    real_run = backups._run

    def spy(argv, env, what):
        seen.append(list(argv))
        return real_run(argv, env, what)

    monkeypatch.setattr(backups, "_run", spy)
    backups.create(actor="pytest", reason="argv")

    assert seen
    assert password and not any(password in a for argv in seen for a in argv)


def test_the_snapshot_session_is_closed_after_a_backup(backups, probe):
    backups.create(actor="pytest", reason="close")
    assert _snapshot_sessions() == 0


def test_a_pg_dump_failure_still_fails_the_backup_and_closes_the_snapshot(
        backups, probe, monkeypatch):
    def failing_dump(argv, env, what):
        assert _snapshot_sessions() == 1, "the snapshot is held open during pg_dump"
        raise backups.BackupError("pg_dump ناموفق بود.")

    monkeypatch.setattr(backups, "_run", failing_dump)
    with pytest.raises(backups.BackupError):
        backups.create(actor="pytest", reason="fail")

    assert _snapshot_sessions() == 0
    assert not os.listdir(backups.BACKUP_DIR), "a failed backup leaves no directory"
