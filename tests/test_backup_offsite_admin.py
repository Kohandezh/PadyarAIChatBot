"""Infrastructure > Backups tells the operator whether any copy exists off
the server.

Until an SFTP destination exists, OFFSITE_BACKUP_TARGET is empty on the
server, and losing the server loses every backup with it. The page must say
so in one plain line, not stay silent (SPEC-H AC6). When a target is set,
it shows the newest backup's off-site result: time, and copied or failed.

The list endpoint carries it as an `offsite` object. It is admin-only like
the rest of the router, and it carries a state and a time only: the target
string and the error text can name a host, a user and a path, and the
router's rule is that no path reaches the browser.
"""
import datetime
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKUP_ID = "pg_20261007_030000_ab12cd"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)


class FakePgEngine:
    def __init__(self, offsite):
        self.manifest = {"backup_id": BACKUP_ID, "created_at": "2026-10-07T03:00:00+00:00",
                         "file": "padyar.dump", "bytes": 42, "reason": "scheduled",
                         "verification": {"status": "verified", "problems": []}}
        if offsite:
            self.manifest["offsite"] = offsite

    def list_backups(self):
        return [self.manifest]


def _use(monkeypatch, offsite):
    from app.routers import backups as backups_router
    monkeypatch.setattr(backups_router, "_engine", lambda: (FakePgEngine(offsite), True))


def test_an_anonymous_caller_gets_no_offsite_state(client, monkeypatch):
    _use(monkeypatch, None)
    assert client.get("/admin/api/infra/backups").status_code == 401


def test_no_target_means_the_page_is_told_off(client, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    _use(monkeypatch, None)
    _login(client)

    body = client.get("/admin/api/infra/backups").json()

    assert body["offsite"]["configured"] is False
    assert body["offsite"]["state"] == "off"


def test_a_failed_copy_is_shown_without_the_target_or_the_error(client, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "sftp:backup@secret-host:/upload")
    monkeypatch.setattr(backup_offsite, "encryption_problem", lambda: "", raising=False)
    _use(monkeypatch, {"status": "failed", "attempted_at": "2026-10-07T03:01:00+00:00",
                       "target": "sftp:backup@secret-host:/upload",
                       "error": "FileNotFoundError: /opt/padyar-x/offsite/id_ed25519"})
    _login(client)

    res = client.get("/admin/api/infra/backups")

    assert res.json()["offsite"] == {"configured": True, "state": "failed",
                                     "attempted_at": "2026-10-07T03:01:00+00:00",
                                     "backup_id": BACKUP_ID}
    assert "secret-host" not in res.text
    assert "/opt/padyar-x" not in res.text


def test_the_page_has_a_place_for_the_line_and_the_plain_sentence():
    html = (ROOT / "templates/admin/infra_backups.html").read_text(encoding="utf-8")
    js = (ROOT / "static/admin/js/infra_backups.js").read_text(encoding="utf-8")
    assert 'id="offsite-status"' in html
    assert "هیچ نسخه‌ای بیرون از سرور نیست" in js
    assert "renderOffsite(data.offsite)" in js


def test_an_sftp_target_without_working_encryption_is_not_shown_as_configured(client,
                                                                            monkeypatch):
    """Review fix 3: with no usable gpg key every nightly copy fails, so the
    page must not call the destination configured."""
    import app.config as config
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "sftp:backup@secret-host:/upload")
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "")
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", "")
    _use(monkeypatch, None)
    _login(client)

    res = client.get("/admin/api/infra/backups")

    assert res.json()["offsite"] == {"configured": False, "state": "not_ready",
                                     "attempted_at": None, "backup_id": None}
    assert "OFFSITE_GPG" not in res.text and "secret-host" not in res.text

