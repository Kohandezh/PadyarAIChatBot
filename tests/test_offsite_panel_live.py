"""The admin-panel SFTP destination against a REAL SFTP-only server (SPEC-H2).

tests/test_offsite_connection.py pins the rules with a fake sftp. This file
drives the real endpoints (TestClient, admin session, CSRF) against a
throwaway atmoz/sftp container that allows BOTH password and key login, and
proves:

  * password login (SSH_ASKPASS, BatchMode=no before -b) and key login (with
    and without the key's final newline) both pass the connection test, and
    the test file is gone afterwards;
  * the host key is pinned for every key type the server offers: pinning
    its rsa key works as well as its ed25519 key; a pin that matches no key
    is refused before any login, and nothing is written;
  * a wrong password, an unknown key and a path that cannot be written each
    get their own plain reason;
  * the nightly copy goes to the panel destination, encrypted, and the paper
    key decrypts it to the exact original;
  * the password and the key appear in no response and no log row.

The container turns sshd's PerSourcePenalties off with an /etc/sftp.d hook:
OpenSSH 9.8+ refuses an address once more than 15 s of penalty has built up
(5 s per failed login, 1 s per connection that never logs in, and every
ssh-keyscan is one), and the deny cases here would trip it.

Needs Docker, like tests/test_offsite_sftp_live.py, and the same rule: skip
without it, unless OFFSITE_SFTP_LIVE=1 (the offsite-sftp CI job), where a
missing tool fails. Containers are named OFFSITE_TEST_CONTAINER_PREFIX-* and
removed with `docker rm -fv`. Every key and password is a throwaway.
"""
import datetime
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient

from tests import offsite_keys
from tests.test_offsite_sftp_live import PREFIX, SFTP_IMAGE, _docker_ready, _need, _run, _wait

API = "/admin/api/infra/backups/offsite-settings"
PASSWORD = "Live-pw-" + secrets.token_hex(8)
NO_PENALTIES = '#!/bin/sh\necho "PerSourcePenalties no" >> /etc/ssh/sshd_config\n'


@pytest.fixture(scope="module")
def tools():
    _need(_docker_ready(), "a running docker")
    for tool in ("sftp", "ssh-keygen", "ssh-keyscan", "gpg"):
        _need(subprocess.run(["sh", "-c", f"command -v {tool}"],
                             capture_output=True).returncode == 0, tool)


def _fingerprints(scan_output):
    """{key type: SHA256 fingerprint}, computed by ssh-keygen -lf as an
    operator would, not by the code under test."""
    out = {}
    for line in scan_output.splitlines():
        if not line or line.startswith("#"):
            continue
        printed = _run(["ssh-keygen", "-lf", "-"], input=line.encode()).stdout.decode()
        out[line.split()[1]] = printed.split()[1]
    return out


@pytest.fixture(scope="module")
def server(tools, tmp_path_factory):
    keys = tmp_path_factory.mktemp("panel-ssh")
    for name in ("id", "stranger", "otherhost"):
        _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "throwaway",
              "-f", str(keys / name)], check=True)
    os.chmod(keys / "id.pub", 0o644)
    hook = keys / "no-penalties.sh"
    hook.write_text(NO_PENALTIES)
    os.chmod(hook, 0o755)
    name = f"{PREFIX}-panel-{secrets.token_hex(3)}"
    started = _run(["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::22",
                    "-v", f"{keys / 'id.pub'}:/home/backup/.ssh/keys/id.pub:ro",
                    "-v", f"{hook}:/etc/sftp.d/no-penalties.sh:ro",
                    SFTP_IMAGE, f"backup:{PASSWORD}:1001::upload"])
    assert started.returncode == 0, started.stderr
    try:
        port = _run(["docker", "port", name, "22"], text=True).stdout.split(":")[-1].strip()
        scanned = {}

        def scan():
            out = _run(["ssh-keyscan", "-p", port, "127.0.0.1"], text=True).stdout
            scanned["text"] = out
            return "ssh-ed25519" in out and "ssh-rsa" in out

        _wait(scan, "the SFTP server's host keys")
        penalties = _run(["docker", "exec", name, "sh", "-c",
                          "/usr/sbin/sshd -T | grep -i '^persourcepenalties '"], text=True)
        assert penalties.stdout.strip() == "persourcepenalties no", penalties.stdout
        other = (keys / "otherhost.pub").read_text().split()
        yield {
            "name": name, "port": int(port),
            "fingerprints": _fingerprints(scanned["text"]),
            "wrong_fingerprint": _fingerprints(f"x {other[0]} {other[1]}")[other[0]],
            "key": (keys / "id").read_text(),
            "stranger_key": (keys / "stranger").read_text(),
            "identity": str(keys / "id"),
        }
    finally:
        _run(["docker", "rm", "-fv", name])


def _admin_sftp(server, batch):
    """The test's own sftp, by key, to look at the destination."""
    argv = ["sftp", "-b", "-", "-F", "/dev/null", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
            "-i", server["identity"], "-P", str(server["port"]), "backup@127.0.0.1"]
    return subprocess.run(argv, input=batch, capture_output=True, text=True, timeout=120)


def _remote_names(server):
    out = _admin_sftp(server, "ls -1a /upload\n").stdout
    return sorted(n for n in (line.rsplit("/", 1)[-1] for line in out.splitlines()
                              if line and not line.startswith("sftp>"))
                  if n not in (".", ".."))


def _logins(server):
    logs = _run(["docker", "logs", server["name"]], text=True)
    return [line for line in (logs.stdout + logs.stderr).splitlines()
            if line.startswith("Accepted ")]


@pytest.fixture
def client(server, tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "panel-live.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    _admin_sftp(server, "-rm /upload/*\n-rm /upload/.padyar*\n")
    from app.auth.csrf import token_for_session
    from app.db.connection import get_db_connection, init_db
    from app.main import app
    init_db()
    with TestClient(app) as c:
        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "live-admin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _save(client, server, **over):
    body = {"host": "127.0.0.1", "port": server["port"], "user": "backup",
            "path": "/upload", "auth": "password", "password": PASSWORD,
            "fingerprint": server["fingerprints"]["ssh-ed25519"]}
    body.update(over)
    res = client.post(API, json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _test(client):
    res = client.post(f"{API}/test")
    assert res.status_code == 200, res.text
    return res.json()


def _messages():
    from app.services import offsite_destination
    return offsite_destination.MESSAGES


# ── It works ────────────────────────────────────────────────────────────

def test_password_login_passes_and_leaves_no_test_file(client, server):
    before = len(_logins(server))
    _save(client, server)

    result = _test(client)

    assert result == {"ok": True, "message": _messages()["ok"]}
    assert _remote_names(server) == []
    assert any(line.startswith("Accepted password for backup")
               for line in _logins(server)[before:])


@pytest.mark.parametrize("final_newline", [True, False])
def test_key_login_passes_with_and_without_the_final_newline(client, server, final_newline):
    key = server["key"] if final_newline else server["key"].rstrip("\n")
    assert key.endswith("\n") is final_newline
    before = len(_logins(server))
    _save(client, server, auth="key", password=None, private_key=key)

    result = _test(client)

    assert result["ok"] is True, result
    assert _remote_names(server) == []
    assert any(line.startswith("Accepted publickey for backup")
               for line in _logins(server)[before:])


def test_pinning_the_rsa_key_works_too(client, server):
    """The server prefers ed25519. Pinning its rsa key must still connect:
    every key type it offers is considered, and only the pinned type is
    asked for on the real connection."""
    _save(client, server, fingerprint=server["fingerprints"]["ssh-rsa"])

    assert _test(client)["ok"] is True


# ── It refuses ──────────────────────────────────────────────────────────

def test_a_pin_that_matches_no_host_key_is_refused_before_any_login(client, server):
    before = len(_logins(server))
    _save(client, server, fingerprint=server["wrong_fingerprint"])

    result = _test(client)
    logins = _logins(server)[before:]

    assert result == {"ok": False, "message": _messages()["host_key"]}
    assert logins == [], "a login was attempted against an unpinned key"
    assert _remote_names(server) == []


def test_a_wrong_password_is_its_own_reason(client, server):
    _save(client, server, password="Wrong-password-" + secrets.token_hex(4))

    assert _test(client) == {"ok": False, "message": _messages()["auth_password"]}


def test_a_key_the_server_does_not_know_is_its_own_reason(client, server):
    _save(client, server, auth="key", password=None, private_key=server["stranger_key"])

    assert _test(client) == {"ok": False, "message": _messages()["auth_key"]}


@pytest.mark.parametrize("path", ["/", "/no-such-folder"])
def test_a_path_that_cannot_be_written_is_its_own_reason(client, server, path):
    _save(client, server, path=path)

    assert _test(client) == {"ok": False, "message": _messages()["not_writable"]}
    assert _remote_names(server) == []


# ── The nightly copy ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def vault(tools, tmp_path_factory):
    key = offsite_keys.make_vault_key(tmp_path_factory.mktemp("panel-vault"),
                                      "padyar-backup-panel")
    yield key
    offsite_keys.destroy(key)


def test_the_nightly_copy_goes_to_the_panel_destination_encrypted(client, server, vault,
                                                                 tmp_path, monkeypatch):
    import app.config as config
    from app.services import applog, backup, backup_offsite, pg_backup
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "sftp:nobody@unused.example:/x")
    monkeypatch.setattr(config, "OFFSITE_SFTP_IDENTITY_FILE", "")
    monkeypatch.setattr(config, "OFFSITE_SFTP_KNOWN_HOSTS", "")
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", vault.public_file)
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", vault.fingerprint)
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", None)
    monkeypatch.setattr(backup, "configured_keep", lambda: 14)
    monkeypatch.setattr(applog, "service", lambda *a, **k: 0)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/padyar_panel")
    _save(client, server)
    backup_id = "pg_20261008_030000_a1b2c3"
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
    assert result["target"] == f"sftp:backup@127.0.0.1:{server['port']}:/upload"
    name = f"padyar_panel.{backup_id}.dump.gpg"
    assert _remote_names(server) == [name]
    fetched = tmp_path / name
    assert _admin_sftp(server, f'get /upload/{name} "{fetched}"\n').returncode == 0
    assert b"09121234567" not in fetched.read_bytes()
    restored = tmp_path / "restored.dump"
    assert vault.decrypt(str(fetched), str(restored)).returncode == 0
    assert restored.read_bytes() == dump


# ── Secrets ─────────────────────────────────────────────────────────────

def test_no_response_or_log_row_holds_the_password_or_the_key(client, server, tmp_path):
    import app.config as config
    texts = [client.post(API, json={
        "host": "127.0.0.1", "port": server["port"], "user": "backup", "path": "/upload",
        "auth": "key", "password": PASSWORD, "private_key": server["key"],
        "fingerprint": server["fingerprints"]["ssh-ed25519"]}).text]
    texts.append(client.post(f"{API}/test").text)
    texts.append(client.get(API).text)
    _save(client, server, password="Wrong-password-" + secrets.token_hex(4))
    texts.append(client.post(f"{API}/test").text)
    conn = sqlite3.connect(config.LOGS_DB_PATH)
    try:
        for table in ("app_logs", "audit_logs", "security_events", "service_events"):
            texts += [json.dumps(list(r), default=str)
                      for r in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()

    body = "".join(server["key"].splitlines()[1:-1])
    for text in texts:
        assert PASSWORD not in text
        assert body[:40] not in text.replace("\\n", "")
