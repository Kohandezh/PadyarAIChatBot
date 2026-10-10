"""The gpg PUBLIC key set in the admin panel, against a REAL SFTP-only server
(SPEC-H3).

tests/test_offsite_gpg_key.py pins the key rules with no network. This file
drives the real endpoints (TestClient, admin session, CSRF) with BOTH the
destination and the gpg key set in the panel and NO OFFSITE_GPG_* in env, and
proves:

  * "Test connection" says ready only once the panel key is saved, and the
    page's off-site state follows it;
  * a real nightly copy goes to the panel destination encrypted to the panel
    key, and the matching throwaway private key (the "paper" key) decrypts it
    to the exact original bytes. The wrong private key cannot.

Same container, same skip rule as tests/test_offsite_panel_live.py: skipped
without Docker unless OFFSITE_SFTP_LIVE=1 (the offsite-sftp CI job), where a
missing tool fails. Containers are named OFFSITE_TEST_CONTAINER_PREFIX-* and
removed with `docker rm -fv`. Every key and password is a throwaway.
"""
import datetime
import hashlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from tests import offsite_keys
from tests.test_offsite_panel_live import (  # noqa: F401
    API, _admin_sftp, _messages, _remote_names, _save, _test, server, tools)

GPG_API = "/admin/api/infra/backups/offsite-gpg"


@pytest.fixture(scope="module")
def vaults(tools, tmp_path_factory):
    """Two key pairs: the panel one, and a stranger's that must not open a copy."""
    out = tmp_path_factory.mktemp("gpg-panel-vault")
    panel = offsite_keys.make_vault_key(out, "padyar-panel-live")
    stranger = offsite_keys.make_vault_key(out, "padyar-stranger-live")
    yield panel, stranger
    offsite_keys.destroy(panel)
    offsite_keys.destroy(stranger)


@pytest.fixture
def client(server, tmp_path, monkeypatch):
    """An admin client on a fresh database with NO env gpg key: the only key
    there can be is the one the test saves in the panel."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "gpg-panel-live.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "")
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", "")
    _admin_sftp(server, "-rm /upload/*\n-rm /upload/.padyar*\n")
    from app.auth.csrf import token_for_session
    from app.db.connection import get_db_connection, init_db
    from app.main import app
    init_db()
    with TestClient(app) as c:
        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "gpg-live-admin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _public_text(key):
    with open(key.public_file) as f:
        return f.read()


def test_test_connection_follows_the_key_saved_in_the_panel(client, server, vaults):
    panel, _ = vaults
    _save(client, server)

    before = _test(client)
    assert before == {"ok": False, "message": _messages()["encryption_not_ready"]}
    assert client.get("/admin/api/infra/backups").json()["offsite"]["state"] == "not_ready"
    assert client.get(GPG_API).json()["ready"] is False

    saved = client.post(GPG_API, json={"public_key": _public_text(panel)})
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "panel" and saved.json()["ready"] is True

    assert _test(client) == {"ok": True, "message": _messages()["ok"]}
    offsite = client.get("/admin/api/infra/backups").json()["offsite"]
    assert offsite["configured"] is True and offsite["state"] != "not_ready"
    assert _remote_names(server) == [], "the test file is gone"

    cleared = client.post(GPG_API, json={"clear": True})
    assert cleared.json()["source"] == "none"
    assert _test(client) == {"ok": False, "message": _messages()["encryption_not_ready"]}


@pytest.mark.parametrize("how", ["paste", "upload"])
def test_the_nightly_copy_is_encrypted_to_the_panel_key_and_the_paper_key_opens_it(
        client, server, vaults, tmp_path, monkeypatch, how):
    import app.config as config
    from app.services import applog, backup, backup_offsite, pg_backup
    panel, stranger = vaults
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "sftp:nobody@unused.example:/x")
    monkeypatch.setattr(config, "OFFSITE_SFTP_IDENTITY_FILE", "")
    monkeypatch.setattr(config, "OFFSITE_SFTP_KNOWN_HOSTS", "")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", None)
    monkeypatch.setattr(backup, "configured_keep", lambda: 14)
    monkeypatch.setattr(applog, "service", lambda *a, **k: 0)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/padyar_gpgpanel")
    _save(client, server)
    if how == "paste":
        res = client.post(GPG_API, json={"public_key": _public_text(panel)})
    else:
        res = client.post(GPG_API, files={"file": ("backup-key.asc", _public_text(panel).encode())})
    assert res.status_code == 200, res.text

    backup_id = "pg_20261010_030000_c1d2e3"
    root = tmp_path / "postgres"
    (root / backup_id).mkdir(parents=True)
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(root))
    dump = b"PGDMP" + secrets.token_bytes(4096) + b"09121234567"
    (root / backup_id / "padyar.dump").write_bytes(dump)
    manifest = {"backup_id": backup_id, "file": "padyar.dump",
                "sha256": hashlib.sha256(dump).hexdigest(),
                "verification": {"status": "verified"}}
    (root / backup_id / "manifest.json").write_text(json.dumps(manifest))

    result = backup_offsite.copy_verified_dump(backup_id, manifest)

    assert result["status"] == "copied", result["error"]
    name = f"padyar_gpgpanel.{backup_id}.dump.gpg"
    assert _remote_names(server) == [name]
    fetched = tmp_path / name
    assert _admin_sftp(server, f'get /upload/{name} "{fetched}"\n').returncode == 0
    assert b"09121234567" not in fetched.read_bytes()
    restored = tmp_path / "restored.dump"
    assert panel.decrypt(str(fetched), str(restored)).returncode == 0
    assert restored.read_bytes() == dump
    assert stranger.decrypt(str(fetched), str(tmp_path / "nope")).returncode != 0
