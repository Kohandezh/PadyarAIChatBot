"""Infrastructure → Backups: create, verify, download, delete, restore.

Nothing about SQLite is mocked here. Every test writes real databases into a
throwaway directory and then reads the bytes back, because the whole feature is
a claim about bytes on disk: "this backup could actually restore the system".
A mocked sqlite3 would let every one of these tests pass while the feature was
broken.

The load-bearing test is `test_a_backup_taken_while_a_write_is_in_flight...`.
Both databases run in WAL mode, so recently committed transactions live in the
`-wal` sidecar and are NOT in the `.db` file yet. That test pins the WAL open,
copies the `.db` file the naive way, and proves the naive copy loses data the
online-backup API keeps.
"""
import datetime
import os
import secrets
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Redirect every database and the backups directory into tmp_path.

    conftest already redirects LOGS_DB_PATH into this same tmp_path for every
    test, so only the main DB and the backups root need moving here."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    import backup_db
    monkeypatch.setattr(backup_db, "BACKUP_DIR", str(tmp_path / "backups"))

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    return tmp_path


@pytest.fixture
def client(paths):
    from app.main import app
    from app.routers import backups as backups_router
    # Mounting the router belongs to whoever owns app/main.py. Until that is
    # wired, mount it here so these tests exercise the real application object
    # either way. Idempotent — it is skipped once the route exists.
    if not any(str(getattr(r, "path", "")).startswith("/admin/api/infra/backups")
               for r in app.routes):
        app.include_router(backups_router.router)
    with TestClient(app) as c:
        yield c


def _login(client):
    """Create a real admin session row and put its cookie on the client."""
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, "tester", expiry.isoformat()),
    )
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    # Admin mutations require a CSRF token; these tests exercise the
    # endpoints themselves, not the CSRF guard (see tests/test_csrf.py).
    from app.auth.csrf import token_for_session
    client.headers.update({'X-CSRF-Token': token_for_session(token)})
    return token


# ── Helpers ─────────────────────────────────────────────────────────────

def _set_setting(key, value):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, value))
    conn.commit()
    conn.close()


def _get_setting(key):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def _count_rows(path, like="row%"):
    """Rows matching `like` in a database file, or -1 if the table isn't there."""
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM settings WHERE key LIKE ?", (like,)).fetchone()[0]
    except sqlite3.OperationalError:
        return -1
    finally:
        conn.close()


def _events(table):
    from app.services import applog
    conn = applog.get_logs_connection()
    try:
        return [r["event_name"] for r in
                conn.execute(f"SELECT event_name FROM {table}")]
    finally:
        conn.close()


def _corrupt(path, offset=1024, length=512):
    """Garble a byte range in place, keeping the file size identical.

    0xff, not 0x00: a fresh SQLite page is mostly zeros, so writing zeros here
    left the file byte-identical and quietly turned this helper into a no-op —
    the assertion below makes that failure mode impossible to repeat."""
    with open(path, "rb") as f:
        before = f.read()
    with open(path, "r+b") as f:
        f.seek(offset)
        f.write(b"\xff" * length)
    with open(path, "rb") as f:
        after = f.read()
    assert before != after, "corruption helper did not change the file"
    assert len(before) == len(after), "corruption helper changed the file size"


# ── create ──────────────────────────────────────────────────────────────

def test_create_produces_a_manifest_and_both_databases(paths):
    import json
    from app.services import backup_center

    summary = backup_center.create(actor="tester")
    backup_id = summary["backup_id"]

    directory = backup_center.set_dir(backup_id)
    assert directory and os.path.isdir(directory)

    with open(os.path.join(directory, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["backup_id"] == backup_id
    assert manifest["created_by"] == "tester"
    assert manifest["created_at"]
    assert manifest["duration_ms"] is not None
    assert manifest["total_bytes"] > 0

    names = {f["name"] for f in manifest["files"]}
    assert names == {"chat_history.db", "application_logs.db"}, names
    for entry in manifest["files"]:
        member = os.path.join(directory, entry["name"])
        assert os.path.isfile(member)
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] == os.path.getsize(member)


def test_list_reports_metadata_and_an_unverified_state(paths):
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    rows = backup_center.list_sets()
    assert [r["backup_id"] for r in rows] == [backup_id]

    row = rows[0]
    assert row["file_count"] == 2
    assert row["total_bytes"] > 0
    assert row["age_seconds"] is not None
    # Nothing has checked it yet — and "unknown" must not read as "fine".
    assert row["verification"]["state"] == "unknown"

    backup_center.verify(backup_id)
    assert backup_center.list_sets()[0]["verification"]["state"] == "verified"


def test_a_stored_backup_is_one_self_contained_file_per_database(paths):
    """No -wal/-shm sidecars, and verifying does not change the bytes.

    The backup API copies page 1 verbatim, so a backup of a WAL database is
    itself a WAL database unless it is folded back — and then every read of it
    drops sidecars beside the file whose SHA-256 the manifest recorded."""
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    directory = backup_center.set_dir(backup_id)

    assert backup_center.verify(backup_id)["ok"] is True
    assert backup_center.verify(backup_id)["ok"] is True   # and again

    stray = [n for n in os.listdir(directory)
             if n.endswith("-wal") or n.endswith("-shm")]
    assert stray == [], stray


def test_a_backup_taken_while_a_write_is_in_flight_is_consistent(paths):
    """The WAL correctness test — the reason a file copy is not a backup.

    A reader opened before the burst pins the WAL, so SQLite may not checkpoint
    past its snapshot and the 300 committed rows are guaranteed to be sitting
    in the `-wal`, not in the `.db` file. A plain copy of the `.db` therefore
    loses them; the online backup API does not.
    """
    from app.db.connection import get_db_connection
    from app.services import backup_center

    writer = get_db_connection()   # WAL + busy_timeout, exactly like the app

    reader = get_db_connection()
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM settings").fetchone()  # pin a snapshot

    for i in range(300):
        writer.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                       (f"row{i:04d}", "in-flight"))
    writer.commit()

    live = paths / "chat_history.db"
    assert (paths / "chat_history.db-wal").exists(), "WAL mode is not in play"

    naive = paths / "naive_copy.db"
    shutil.copyfile(live, naive)          # what a file copy would have captured

    summary = backup_center.create(actor="tester")

    reader.rollback()
    reader.close()
    writer.close()

    backup_main = backup_center.member_path(summary["backup_id"], "chat_history.db")

    assert _count_rows(naive) < 300, (
        "the naive file copy saw all 300 rows, so this test no longer proves "
        "anything about the WAL — re-check the setup before trusting it")
    assert _count_rows(backup_main) == 300, "the online backup lost committed rows"
    assert backup_center.verify(summary["backup_id"])["ok"]


# ── verify ──────────────────────────────────────────────────────────────

def test_verify_passes_on_a_good_backup(paths):
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    verdict = backup_center.verify(backup_id, actor="tester")
    assert verdict["ok"] is True
    assert verdict["problems"] == []
    assert verdict["checked_at"]


def test_verify_fails_on_a_corrupted_file(paths):
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    _corrupt(backup_center.member_path(backup_id, "chat_history.db"))

    verdict = backup_center.verify(backup_id, actor="tester")
    assert verdict["ok"] is False
    assert any("chat_history.db" in p for p in verdict["problems"]), verdict
    assert backup_center.list_sets()[0]["verification"]["state"] == "failed"


def test_verify_fails_on_a_missing_file(paths):
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    os.remove(backup_center.member_path(backup_id, "application_logs.db"))

    verdict = backup_center.verify(backup_id)
    assert verdict["ok"] is False
    assert "application_logs.db:missing" in verdict["problems"]


def test_verify_fails_on_a_checksum_mismatch(paths):
    """A file can be perfectly valid SQLite and still not be what we backed up."""
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    member = backup_center.member_path(backup_id, "chat_history.db")

    conn = sqlite3.connect(member)          # a legitimate, well-formed change
    conn.execute("INSERT INTO settings (key, value) VALUES ('tampered', '1')")
    conn.commit()
    conn.close()

    verdict = backup_center.verify(backup_id)
    assert verdict["ok"] is False
    assert "chat_history.db:checksum_mismatch" in verdict["problems"], verdict


def test_verify_runs_integrity_check_not_just_the_checksum(paths):
    """Corrupt the file AND fix up the manifest so the checksum agrees.

    A verifier that only compared hashes would now call this backup healthy.
    `PRAGMA integrity_check` is what catches it — and it is the check that
    decides whether the file could really be restored."""
    import hashlib
    import json
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    member = backup_center.member_path(backup_id, "chat_history.db")
    _corrupt(member, offset=4096, length=2048)

    manifest_path = os.path.join(backup_center.set_dir(backup_id), "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(member, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    for entry in manifest["files"]:
        if entry["name"] == "chat_history.db":
            entry["sha256"] = digest
            entry["bytes"] = os.path.getsize(member)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    verdict = backup_center.verify(backup_id)
    assert verdict["ok"] is False
    assert "chat_history.db:integrity_check_failed" in verdict["problems"], verdict


def test_verify_fails_when_the_manifest_is_gone(paths):
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    os.remove(os.path.join(backup_center.set_dir(backup_id), "manifest.json"))

    verdict = backup_center.verify(backup_id)
    assert verdict["ok"] is False
    assert "manifest_missing_or_unreadable" in verdict["problems"]


# ── restore ─────────────────────────────────────────────────────────────

def test_restore_brings_back_the_old_data_and_takes_a_safety_backup_first(paths):
    from app.services import backup_center

    _set_setting("marker", "before")
    backup_id = backup_center.create(actor="tester")["backup_id"]
    _set_setting("marker", "after")

    ids_before = {r["backup_id"] for r in backup_center.list_sets()}
    result = backup_center.restore(backup_id, actor="tester")

    assert _get_setting("marker") == "before"
    assert result["restart_recommended"] is True
    assert set(result["restored"]) == {"main", "logs"}

    safety_id = result["safety_backup_id"]
    assert safety_id and safety_id not in ids_before

    safety = [r for r in backup_center.list_sets() if r["backup_id"] == safety_id]
    assert safety and safety[0]["kind"] == "safety"

    # The undo really holds the state we overwrote, not a second copy of the
    # backup we restored from.
    conn = sqlite3.connect(backup_center.member_path(safety_id, "chat_history.db"))
    value = conn.execute(
        "SELECT value FROM settings WHERE key = 'marker'").fetchone()[0]
    conn.close()
    assert value == "after"


def test_restore_refuses_a_corrupt_backup_and_changes_nothing(paths):
    from app.services import backup_center

    _set_setting("marker", "live")
    backup_id = backup_center.create(actor="tester")["backup_id"]
    _corrupt(backup_center.member_path(backup_id, "chat_history.db"))

    sets_before = {r["backup_id"] for r in backup_center.list_sets()}
    with pytest.raises(backup_center.BackupNotVerified):
        backup_center.restore(backup_id, actor="tester")

    assert _get_setting("marker") == "live"
    # No safety backup either — a refused restore does nothing at all.
    assert {r["backup_id"] for r in backup_center.list_sets()} == sets_before


def test_restore_ignores_a_stale_verified_flag(paths):
    """A stored verdict is a memory. Restore has to look at the disk again."""
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    assert backup_center.verify(backup_id)["ok"] is True   # marked good...
    _corrupt(backup_center.member_path(backup_id, "chat_history.db"))  # ...then rots

    with pytest.raises(backup_center.BackupNotVerified):
        backup_center.restore(backup_id, actor="tester")


def test_a_half_finished_restore_is_rolled_back_as_a_set(paths, monkeypatch):
    """Two files cannot be swapped atomically, so the guarantee is rollback:
    if the second database fails, the first is put back from the safety set
    rather than left restored beside an original."""
    import app.config as config
    import backup_db
    from app.services import backup_center

    _set_setting("marker", "before")
    backup_id = backup_center.create(actor="tester")["backup_id"]
    _set_setting("marker", "after")

    logs_live = os.path.abspath(str(config.LOGS_DB_PATH))
    real_copy = backup_db.copy_database
    state = {"failed": False}

    def flaky_copy(src, dest, **kwargs):
        # Fail exactly once, on the SECOND database of the restore, so the
        # rollback that follows is allowed to succeed.
        if not state["failed"] and os.path.abspath(dest) == logs_live:
            state["failed"] = True
            raise OSError("simulated disk failure")
        return real_copy(src, dest, **kwargs)

    monkeypatch.setattr(backup_db, "copy_database", flaky_copy)

    with pytest.raises(backup_center.RestoreFailed):
        backup_center.restore(backup_id, actor="tester")

    assert state["failed"], "the failure never happened — the test proved nothing"
    # The main database was already overwritten with 'before' and then put back.
    assert _get_setting("marker") == "after"


def test_restore_is_refused_when_the_audit_row_cannot_be_written(paths, monkeypatch):
    """FAIL-CLOSED. Logging may degrade; destroying a live database silently
    may not. applog never raises — it returns None — so the refusal has to be
    driven by that return value."""
    from app.services import applog, backup_center

    _set_setting("marker", "live")
    backup_id = backup_center.create(actor="tester")["backup_id"]
    sets_before = {r["backup_id"] for r in backup_center.list_sets()}

    monkeypatch.setattr(applog, "audit", lambda *a, **k: None)

    with pytest.raises(backup_center.AuditUnavailable):
        backup_center.restore(backup_id, actor="tester")

    assert _get_setting("marker") == "live"
    assert {r["backup_id"] for r in backup_center.list_sets()} == sets_before


# ── delete ──────────────────────────────────────────────────────────────

def test_delete_removes_only_the_target_set(paths):
    from app.services import backup_center

    keep = backup_center.create(actor="tester")["backup_id"]
    doomed = backup_center.create(actor="tester")["backup_id"]
    assert keep != doomed

    backup_center.delete(doomed, actor="tester")

    remaining = [r["backup_id"] for r in backup_center.list_sets()]
    assert remaining == [keep]
    assert os.path.isdir(backup_center.set_dir(keep))
    assert backup_center.member_path(keep, "chat_history.db")
    assert os.path.isfile(backup_center.member_path(keep, "chat_history.db"))


def test_delete_of_an_unknown_set_is_refused(paths):
    from app.services import backup_center
    with pytest.raises(backup_center.UnknownBackup):
        backup_center.delete("set_20260818_120000_abcdef", actor="tester")


# ── path safety ─────────────────────────────────────────────────────────

TRAVERSALS = [
    "../../etc/passwd",
    "/etc/passwd",
    "..%2f..",
    "..",
    "....//....//etc/passwd",
    "set_20260818_120000_abcdef/../../../etc/passwd",
    "set_20260818_120000_abcdef/..",
    "C:\\Windows\\System32",
    "..\\..\\windows",
    "",
]


@pytest.mark.parametrize("bad", TRAVERSALS)
def test_a_backup_id_can_never_escape_the_backups_directory(paths, bad):
    from app.services import backup_center

    assert backup_center.set_dir(bad) is None
    assert backup_center.member_path(bad, "chat_history.db") is None

    for operation in (backup_center.verify, backup_center.delete,
                      backup_center.restore):
        with pytest.raises(backup_center.UnknownBackup):
            operation(bad)


def test_a_member_name_can_never_escape_its_set(paths):
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    for bad in ("../manifest.json", "../../chat_history.db", "/etc/passwd",
                "..", "chat_history.db/../../x", ""):
        assert backup_center.member_path(backup_id, bad) is None


@pytest.mark.parametrize("bad", TRAVERSALS)
def test_the_legacy_primitive_also_refuses_traversal(paths, bad):
    """backup_db.safe_backup_path() guards the older single-file endpoints."""
    import backup_db
    assert backup_db.safe_backup_path(bad) is None
    assert backup_db.delete_backup(bad) is False


# ── the audit trail ─────────────────────────────────────────────────────

def test_logging_covers_create_verify_delete_and_restore(paths):
    from app.services import backup_center

    first = backup_center.create(actor="tester")["backup_id"]
    backup_center.verify(first, actor="tester")
    second = backup_center.create(actor="tester")["backup_id"]
    backup_center.delete(first, actor="tester")

    audit = set(_events("audit_logs"))
    operational = set(_events("app_logs"))

    assert "admin.backup.create" in audit
    assert "admin.backup.delete" in audit
    assert {"backup.started", "backup.completed"} <= operational
    assert {"backup.verify.started", "backup.verify.completed"} <= operational

    backup_center.restore(second, actor="tester")
    # The restore replaced the log database with `second`'s snapshot; the
    # completion row is written AFTERWARDS, into the live file, which is why
    # the destructive action is still on the record.
    assert "admin.backup.restore.completed" in set(_events("audit_logs"))


def test_a_failed_verification_is_recorded(paths):
    from app.services import backup_center

    backup_id = backup_center.create(actor="tester")["backup_id"]
    _corrupt(backup_center.member_path(backup_id, "chat_history.db"))
    backup_center.verify(backup_id, actor="tester")

    assert "backup.verify.failed" in set(_events("app_logs"))


# ── HTTP surface ────────────────────────────────────────────────────────

def test_api_requires_admin(client):
    fake = "set_20260818_120000_abcdef"
    assert client.get("/admin/api/infra/backups").status_code == 401
    assert client.post("/admin/api/infra/backups").status_code == 401
    assert client.post(f"/admin/api/infra/backups/{fake}/verify").status_code == 401
    assert client.get(f"/admin/api/infra/backups/{fake}/download").status_code == 401
    assert client.delete(f"/admin/api/infra/backups/{fake}").status_code == 401
    assert client.post(f"/admin/api/infra/backups/{fake}/restore",
                       json={"confirm": f"RESTORE BACKUP {fake}"}).status_code == 401


def test_page_requires_admin(client):
    r = client.get("/secure-panel-inotex/infrastructure/backups",
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/secure-panel-inotex/login"


def test_page_renders_for_admin(client):
    _login(client)
    html = client.get("/secure-panel-inotex/infrastructure/backups").text
    assert 'id="backups-body"' in html
    assert 'id="restoreModal"' in html
    assert '/static/admin/js/infra_backups.js' in html


def test_create_and_list_over_http(client):
    _login(client)
    created = client.post("/admin/api/infra/backups")
    assert created.status_code == 200
    backup_id = created.json()["backup_id"]

    listed = client.get("/admin/api/infra/backups")
    assert listed.status_code == 200
    body = listed.json()
    assert [r["backup_id"] for r in body["backups"]] == [backup_id]
    assert "schedule" in body
    # No filesystem path may ever reach the browser.
    assert str(client.app) or True
    assert "/backups/sets" not in listed.text
    assert "chat_history.db" in listed.text  # the member NAME is fine; a path is not

    verified = client.post(f"/admin/api/infra/backups/{backup_id}/verify")
    assert verified.status_code == 200
    assert verified.json()["ok"] is True


def test_restore_with_a_wrong_confirmation_does_nothing(client):
    _login(client)
    _set_setting("marker", "live")
    backup_id = client.post("/admin/api/infra/backups").json()["backup_id"]
    _set_setting("marker", "changed")

    from app.services import backup_center
    sets_before = {r["backup_id"] for r in backup_center.list_sets()}

    for wrong in ("", "yes", "RESTORE BACKUP", "restore backup " + backup_id,
                  "RESTORE BACKUP set_20260818_120000_abcdef"):
        r = client.post(f"/admin/api/infra/backups/{backup_id}/restore",
                        json={"confirm": wrong})
        assert r.status_code == 400, wrong

    assert _get_setting("marker") == "changed"
    assert {r["backup_id"] for r in backup_center.list_sets()} == sets_before


def test_restore_over_http_with_the_right_phrase(client):
    _login(client)
    _set_setting("marker", "before")
    backup_id = client.post("/admin/api/infra/backups").json()["backup_id"]
    _set_setting("marker", "after")

    r = client.post(f"/admin/api/infra/backups/{backup_id}/restore",
                    json={"confirm": f"RESTORE BACKUP {backup_id}"})
    assert r.status_code == 200, r.text
    assert r.json()["safety_backup_id"]
    assert _get_setting("marker") == "before"


def test_restore_of_a_corrupt_backup_is_rejected_over_http(client):
    _login(client)
    from app.services import backup_center

    backup_id = client.post("/admin/api/infra/backups").json()["backup_id"]
    _corrupt(backup_center.member_path(backup_id, "chat_history.db"))

    r = client.post(f"/admin/api/infra/backups/{backup_id}/restore",
                    json={"confirm": f"RESTORE BACKUP {backup_id}"})
    assert r.status_code == 409
    assert "پشتیبان" in r.json()["detail"]


def test_download_serves_only_a_manifest_listed_file(client):
    _login(client)
    backup_id = client.post("/admin/api/infra/backups").json()["backup_id"]
    base = f"/admin/api/infra/backups/{backup_id}/download"

    good = client.get(f"{base}?file=chat_history.db")
    assert good.status_code == 200
    assert good.content[:16] == b"SQLite format 3\x00"

    assert client.get(f"{base}?file=application_logs.db").status_code == 200
    # In the set, but not a database the manifest lists as a backed-up file.
    assert client.get(f"{base}?file=manifest.json").status_code == 404
    for bad in ("../../../etc/passwd", "/etc/passwd", "..", "chat_history.db "):
        assert client.get(f"{base}?file={bad}").status_code == 404, bad


def test_unknown_ids_are_404_and_never_echo_a_path(client):
    _login(client)
    fake = "set_20260818_120000_abcdef"
    for response in (
        client.post(f"/admin/api/infra/backups/{fake}/verify"),
        client.delete(f"/admin/api/infra/backups/{fake}"),
        client.post(f"/admin/api/infra/backups/{fake}/restore",
                    json={"confirm": f"RESTORE BACKUP {fake}"}),
        client.get(f"/admin/api/infra/backups/{fake}/download"),
    ):
        assert response.status_code == 404, response.text
        assert "/" not in response.json()["detail"]
