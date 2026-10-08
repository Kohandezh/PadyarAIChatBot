"""Backup retention keeps two pools, so a deploy cannot push out nightly backups.

The bug this file pins: pg_backup.prune() kept the newest 14 backups of ANY
reason. Pre-deploy dumps, rollback dumps, manual ones, reset-content dumps and
the panel restore's safety dump counted in the same 14. A day with several
deploys pushed out the nightly backups, and the daily recovery window shrank
without anyone deciding it.

What is asserted here (SPEC-R and its Amendment 1), with no server, no pg_dump
and no database. The backups are fake directories with a manifest.json:

  * scheduled backups (and old ones with no reason) are one pool, kept to `keep`
  * every other reason is a second pool, kept to `keep_other`
  * a deploy dump never evicts a scheduled one, and the 6th "other" evicts the
    oldest "other"
  * the newest verified scheduled backup is never pruned, even beyond `keep`
  * BACKUP_KEEP_OTHER reads from the environment, falls back and is clamped
  * the scheduler passes both numbers, and the Backups page API returns the
    second one (PostgreSQL only) and the page text states both
"""
import datetime
import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent


# ── Fake backup directory ───────────────────────────────────────────────

def _bid(i):
    """A valid backup id. A bigger `i` is a newer backup (names sort by time)."""
    return "pg_20260801_%06d_aabbcc" % i


def _write(root, i, reason, status="verified", with_reason=True):
    d = root / _bid(i)
    d.mkdir(parents=True)
    manifest = {"backup_id": _bid(i),
                "verification": {"status": status, "checked_at": None}}
    if with_reason:
        manifest["reason"] = reason
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "padyar.dump").write_bytes(b"x")


@pytest.fixture
def backups(tmp_path, monkeypatch):
    """Returns a function that builds a BACKUP_DIR from (reason, status) rows,
    oldest first. The first row gets id 1, the next id 2, and so on."""
    from app.services import pg_backup
    monkeypatch.setattr(pg_backup.applog, "audit", lambda *a, **k: None)
    root = tmp_path / "postgres"
    root.mkdir()
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(root))

    def build(rows):
        for i, row in enumerate(rows, start=1):
            if isinstance(row, str):
                row = (row, "verified")
            _write(root, i, row[0], row[1])
        return root

    return build


def _left(root):
    """The numbers of the backups still on disk."""
    return sorted(int(p.name.split("_")[2]) for p in root.iterdir())


def _prune(**kw):
    from app.services import pg_backup
    return pg_backup.prune(**kw)


# ── The two pools ───────────────────────────────────────────────────────

def test_many_deploy_dumps_never_evict_a_scheduled_backup(backups):
    # 14 nightly backups, then 10 deploy dumps made after them.
    root = backups(["scheduled"] * 14 + ["deploy"] * 10)
    removed = _prune(keep=14, keep_other=5)
    left = _left(root)
    assert [n for n in left if n <= 14] == list(range(1, 15))   # all nightly stay
    assert [n for n in left if n > 14] == [20, 21, 22, 23, 24]  # 5 newest deploy
    assert len(removed) == 5


def test_a_deploy_dump_between_nightly_backups_does_not_use_up_their_slots(backups):
    # The old code kept the newest 14 of everything: 3 deploys would have
    # pushed out the 3 oldest nightly backups.
    rows = []
    for _ in range(14):
        rows += ["scheduled"]
    rows += ["deploy", "deploy", "deploy"]
    root = backups(rows)
    assert _prune(keep=14, keep_other=5) == []
    assert len(_left(root)) == 17


def test_the_sixth_other_backup_evicts_the_oldest_other_one(backups):
    root = backups(["manual", "deploy", "rollback", "reset-content",
                    "safety-before-restore-of-pg_20260701_000000_aabbcc",
                    "deploy"])
    removed = _prune(keep=14, keep_other=5)
    assert removed == [_bid(1)]
    assert _left(root) == [2, 3, 4, 5, 6]


def test_other_reasons_are_one_pool_not_one_pool_each(backups):
    # 3 manual + 3 deploy is 6 "other": one must go, even though each reason
    # alone is under 5.
    root = backups(["manual", "manual", "manual", "deploy", "deploy", "deploy"])
    assert len(_prune(keep=14, keep_other=5)) == 1
    assert len(_left(root)) == 5


def test_other_backups_never_fill_the_scheduled_pool(backups):
    # Allow-control for the deny test above: with only 5 other backups
    # nothing is removed, so the test cannot pass by always deleting.
    root = backups(["scheduled", "scheduled", "deploy", "manual", "rollback",
                    "reset-content", "deploy"])
    assert _prune(keep=14, keep_other=5) == []
    assert len(_left(root)) == 7


def test_scheduled_backups_beyond_keep_are_pruned_oldest_first(backups):
    root = backups(["scheduled"] * 6)
    removed = _prune(keep=4, keep_other=5)
    assert sorted(removed) == [_bid(1), _bid(2)]
    assert _left(root) == [3, 4, 5, 6]


def test_scheduled_pruning_does_not_touch_the_other_pool(backups):
    root = backups(["deploy", "scheduled", "scheduled", "scheduled"])
    _prune(keep=1, keep_other=5)
    assert _left(root) == [1, 4]


def test_an_old_manifest_without_reason_counts_as_scheduled(backups, tmp_path):
    from app.services import pg_backup
    root = Path(pg_backup.BACKUP_DIR)
    _write(root, 1, None, with_reason=False)
    _write(root, 2, "scheduled")
    # keep_other=1 would delete it if it were an "other" backup.
    for i in range(3, 6):
        _write(root, i, "deploy")
    removed = _prune(keep=14, keep_other=1)
    assert _bid(1) not in removed
    assert 1 in _left(root)
    # And it is pruned by the scheduled limit, not the other one.
    removed = _prune(keep=1, keep_other=1)
    assert _bid(1) in removed


def test_an_empty_reason_counts_as_scheduled(backups):
    from app.services import pg_backup
    root = Path(pg_backup.BACKUP_DIR)
    _write(root, 1, "")
    _write(root, 2, "deploy")
    _write(root, 3, "deploy")
    _prune(keep=14, keep_other=1)
    assert _left(root) == [1, 3]


def test_an_unreadable_manifest_counts_as_scheduled(backups):
    from app.services import pg_backup
    root = Path(pg_backup.BACKUP_DIR)
    broken = root / _bid(1)
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    _write(root, 2, "deploy")
    _write(root, 3, "deploy")
    # keep_other=1 would delete the broken one if it were in the other pool.
    _prune(keep=14, keep_other=1)
    assert _left(root) == [1, 3]


# ── Never zero verified scheduled backups (Amendment 1) ─────────────────

def test_the_newest_verified_scheduled_backup_survives_beyond_keep(backups):
    # The 3 newest scheduled backups failed verify. keep=3 would delete the
    # one good backup, and the install would have nothing it can restore.
    root = backups([("scheduled", "verified"),
                    ("scheduled", "failed"),
                    ("scheduled", "failed"),
                    ("scheduled", "failed")])
    removed = _prune(keep=3, keep_other=5)
    assert removed == []
    assert _left(root) == [1, 2, 3, 4]


def test_only_one_extra_verified_backup_is_kept_not_more(backups):
    root = backups([("scheduled", "verified"),
                    ("scheduled", "verified"),
                    ("scheduled", "failed"),
                    ("scheduled", "failed")])
    removed = _prune(keep=2, keep_other=5)
    # Newest verified is #2, kept as the extra. #1 is beyond keep and goes.
    assert removed == [_bid(1)]
    assert _left(root) == [2, 3, 4]


def test_a_verified_backup_inside_keep_needs_no_extra(backups):
    root = backups(["scheduled", "scheduled", "scheduled", "scheduled"])
    assert len(_prune(keep=2, keep_other=5)) == 2
    assert _left(root) == [3, 4]


def test_without_any_verified_scheduled_backup_normal_pruning_applies(backups):
    root = backups([("scheduled", "failed"), ("scheduled", "unknown"),
                    ("scheduled", "failed"), ("scheduled", "failed")])
    assert len(_prune(keep=2, keep_other=5)) == 2
    assert _left(root) == [3, 4]


def test_a_verified_other_backup_does_not_count_as_the_scheduled_safety(backups):
    # A verified deploy dump is not a nightly backup. The rule is about the
    # scheduled pool only, so the old scheduled ones are still pruned.
    root = backups([("scheduled", "failed"), ("scheduled", "failed"),
                    ("scheduled", "failed"), ("deploy", "verified")])
    assert len(_prune(keep=2, keep_other=5)) == 1
    assert _left(root) == [2, 3, 4]


# ── Arguments and failure ───────────────────────────────────────────────

def test_keep_other_none_reads_the_config_value_at_call_time(backups, monkeypatch):
    import app.config as config
    root = backups(["deploy"] * 4)
    monkeypatch.setattr(config, "BACKUP_KEEP_OTHER", 2)
    assert len(_prune(keep=14)) == 2
    assert _left(root) == [3, 4]
    monkeypatch.setattr(config, "BACKUP_KEEP_OTHER", 1)
    assert len(_prune(keep=14)) == 1
    assert _left(root) == [4]


def test_an_explicit_keep_other_wins_over_the_config_value(backups, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "BACKUP_KEEP_OTHER", 1)
    root = backups(["deploy"] * 4)
    assert _prune(keep=14, keep_other=3) == [_bid(1)]
    assert _left(root) == [2, 3, 4]


def test_prune_never_raises_when_listing_fails(monkeypatch):
    from app.services import pg_backup
    monkeypatch.setattr(pg_backup, "list_backups",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pg_backup.prune() == []


def test_a_delete_that_fails_is_skipped_and_the_rest_still_go(backups, monkeypatch):
    from app.services import pg_backup
    root = backups(["deploy"] * 4)
    real = pg_backup.delete

    def flaky(backup_id, actor=""):
        if backup_id == _bid(1):
            raise pg_backup.BackupError("gone")
        return real(backup_id, actor=actor)

    monkeypatch.setattr(pg_backup, "delete", flaky)
    assert _prune(keep=14, keep_other=2) == [_bid(2)]
    assert _left(root) == [1, 3, 4]


def test_the_default_keep_is_unchanged():
    from app.services import pg_backup
    assert pg_backup.KEEP == 14


# ── BACKUP_KEEP_OTHER in the config ─────────────────────────────────────

def _config_value(env_value):
    """Import app.config in a fresh process with the env var set (or unset)."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "BACKUP_KEEP_OTHER"}
    if env_value is not None:
        env["BACKUP_KEEP_OTHER"] = env_value
    out = subprocess.run(
        [sys.executable, "-c",
         "import app.config as c; print(c.BACKUP_KEEP_OTHER)"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    return int(out.stdout.strip().splitlines()[-1])


def test_backup_keep_other_defaults_to_5():
    assert _config_value(None) == 5


def test_backup_keep_other_reads_the_environment():
    assert _config_value("8") == 8


@pytest.mark.parametrize("bad", ["abc", "", "2.5"])
def test_a_bad_backup_keep_other_falls_back_to_5(bad):
    assert _config_value(bad) == 5


@pytest.mark.parametrize("low", ["0", "-3"])
def test_backup_keep_other_below_one_is_clamped_to_1(low):
    assert _config_value(low) == 1


def test_backup_keep_other_above_100_is_clamped_to_100():
    assert _config_value("5000") == 100


def test_env_example_documents_backup_keep_other():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^# BACKUP_KEEP_OTHER=5$", text, re.M)


# ── The scheduler passes both numbers ───────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()


@pytest.mark.parametrize("kind", ["scheduled", "manual"])
def test_the_run_passes_both_pool_sizes_to_prune(monkeypatch, db, kind):
    from app.services import backup
    monkeypatch.setattr(backup, "_backup_engine", lambda: "postgres")
    monkeypatch.setattr(backup, "configured_keep", lambda: 9)
    monkeypatch.setattr(backup, "BACKUP_KEEP_OTHER", 3)
    monkeypatch.setattr("app.services.pg_backup.create",
                        lambda actor="", reason="":
                            {"backup_id": "pg_20260914_030000_ab12cd"})
    monkeypatch.setattr("app.services.pg_backup.verify",
                        lambda backup_id, actor="", offsite=True: None)
    monkeypatch.setattr("app.services.pg_backup.backup_dir",
                        lambda backup_id: "/somewhere/" + backup_id)
    seen = {}
    monkeypatch.setattr("app.services.pg_backup.prune",
                        lambda **kw: seen.update(kw) or [])

    backup._run_backup_now(actor="scheduler", kind=kind)

    assert seen == {"keep": 9, "keep_other": 3}


# ── The Backups page API ────────────────────────────────────────────────

def _manifest(backup_id, reason="scheduled"):
    return {
        "backup_id": backup_id,
        "created_at": "2026-08-30T12:00:00+00:00",
        "created_by": "tester",
        "engine": "postgresql",
        "database": "padyar",
        "format": "custom",
        "file": "padyar.dump",
        "bytes": 42,
        "sha256": "deadbeef",
        "duration_ms": 500,
        "reason": reason,
        "verification": {"status": "verified",
                         "checked_at": "2026-08-30T12:05:00+00:00",
                         "problems": []},
    }


class FakePgEngine:
    def list_backups(self):
        return [_manifest("pg_20260830_120000_ab12cd")]


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    from app.main import app
    from app.routers import backups as backups_router
    if not any(str(getattr(r, "path", "")).startswith("/admin/api/infra/backups")
               for r in app.routes):
        app.include_router(backups_router.router)
    with TestClient(app) as c:
        yield c


def _login(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
        (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)


def test_anonymous_cannot_read_the_backup_list(client, monkeypatch):
    from app.routers import backups as backups_router
    monkeypatch.setattr(backups_router, "_engine", lambda: (FakePgEngine(), True))
    assert client.get("/admin/api/infra/backups").status_code in (401, 403)


def test_the_list_returns_the_keep_other_number_on_postgres(client, monkeypatch):
    import app.config as config
    from app.routers import backups as backups_router
    monkeypatch.setattr(backups_router, "_engine", lambda: (FakePgEngine(), True))
    monkeypatch.setattr(backups_router, "BACKUP_KEEP_OTHER", 7)
    _login(client)
    res = client.get("/admin/api/infra/backups")
    assert res.status_code == 200
    body = res.json()
    assert body["engine"] == "postgresql"
    assert body["keep_other"] == 7
    assert "keep" in body["schedule"]   # the nightly number is still there
    assert config.BACKUP_KEEP_OTHER >= 1


def test_the_list_has_no_keep_other_on_sqlite(client, monkeypatch):
    from app.routers import backups as backups_router
    from app.services import backup_center
    monkeypatch.setattr(backups_router, "_engine", lambda: (backup_center, False))
    monkeypatch.setattr(backup_center, "list_sets", lambda: [])
    _login(client)
    body = client.get("/admin/api/infra/backups").json()
    assert body["engine"] == "sqlite"
    assert body["keep_other"] is None


# ── The page text ───────────────────────────────────────────────────────

def _page_js():
    return (REPO / "static/admin/js/infra_backups.js").read_text(encoding="utf-8")


def test_the_page_script_states_both_numbers_and_uses_textcontent():
    source = _page_js()
    render = source[source.index("function renderSchedule"):]
    render = render[:render.index("\n}\n")]
    assert "keep_other" in render or "keepOther" in render
    assert "sched-keep" in render
    assert "textContent" in render
    assert not re.search(r"innerHTML\s*\+?=", source)


def test_the_page_script_passes_the_response_keep_other_to_the_schedule_line():
    source = _page_js()
    assert re.search(r"renderSchedule\(\s*data\.schedule\s*,\s*data\.keep_other",
                     source)
