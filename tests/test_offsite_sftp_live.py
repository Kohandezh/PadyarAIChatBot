"""The sftp: off-site copy against a REAL SFTP-only server and a REAL dump.

tests/test_backup_offsite.py pins the rules with a fake sftp. This file
proves the same flow end to end, the way the destination will look in
production: an SFTP-only account (atmoz/sftp, chrooted, no shell), key login,
strict host keys, and a genuine pg_dump custom-format archive from
postgres:16. It proves, per SPEC-H AC8:

  (a) what lands at the destination is encrypted: pg_restore --list fails on
      it and the phone number in the data is not in it (the plain dump, as
      the allow-control, lists fine);
  (b) the paper (private) key decrypts it to the exact original (sha256);
  (c) a wrong fingerprint uploads nothing;
  (d) prune keeps exactly N of our copies and leaves a foreign file alone;
  (e) a wrong host key is refused and uploads nothing.

Needs Docker. Without it the file skips, unless OFFSITE_SFTP_LIVE=1 is set
(the offsite-sftp CI job sets it): then a missing tool is a failure, so CI
cannot report a pass it did not earn. Container names start with
OFFSITE_TEST_CONTAINER_PREFIX (default padyar-offsite-test) and are removed
with `docker rm -fv` at the end. Every key is a throwaway made in a temp dir.
"""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time

import pytest

from tests import offsite_keys

LIVE_REQUIRED = os.environ.get("OFFSITE_SFTP_LIVE") == "1"
PREFIX = os.environ.get("OFFSITE_TEST_CONTAINER_PREFIX", "padyar-offsite-test")
SFTP_IMAGE = "atmoz/sftp:alpine"
PG_IMAGE = "postgres:16-alpine"
PHONE = "09121234567"


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def _need(ok: bool, what: str):
    if ok:
        return
    if LIVE_REQUIRED:
        pytest.fail(f"OFFSITE_SFTP_LIVE=1 but {what} is missing")
    pytest.skip(f"{what} is missing")


def _run(argv, **kw):
    return subprocess.run(argv, capture_output=True, timeout=300, **kw)


def _wait(check, what, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return
        time.sleep(1)
    pytest.fail(f"{what} did not come up within {seconds}s")


@pytest.fixture(scope="module")
def tools():
    _need(_docker_ready(), "a running docker")
    for tool in ("sftp", "ssh-keygen", "ssh-keyscan", "gpg"):
        _need(shutil.which(tool) is not None, tool)


@pytest.fixture(scope="module")
def sftp_server(tools, tmp_path_factory):
    keys = tmp_path_factory.mktemp("ssh")
    for name in ("id", "otherhost"):
        _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "throwaway",
              "-f", str(keys / name)], check=True)
    os.chmod(keys / "id.pub", 0o644)
    name = f"{PREFIX}-sftp-{secrets.token_hex(3)}"
    started = _run(["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::22",
                    "-v", f"{keys / 'id.pub'}:/home/backup/.ssh/keys/id.pub:ro",
                    SFTP_IMAGE, "backup::1001::upload"])
    assert started.returncode == 0, started.stderr
    try:
        port = _run(["docker", "port", name, "22"], text=True).stdout.split(":")[-1].strip()
        known = keys / "known_hosts"

        def scanned():
            out = _run(["ssh-keyscan", "-p", port, "127.0.0.1"], text=True).stdout
            lines = [line for line in out.splitlines() if line and not line.startswith("#")]
            known.write_text("\n".join(lines) + "\n")
            return any("ssh-ed25519" in line for line in lines)

        _wait(scanned, "the SFTP server's host keys")
        host = next(line.split()[0] for line in known.read_text().splitlines())
        pub = (keys / "otherhost.pub").read_text().split()
        (keys / "known_hosts_wrong").write_text(f"{host} {pub[0]} {pub[1]}\n")
        yield {"port": int(port), "identity": str(keys / "id"),
               "known_hosts": str(known), "wrong_known_hosts": str(keys / "known_hosts_wrong")}
    finally:
        _run(["docker", "rm", "-fv", name])


@pytest.fixture(scope="module")
def postgres(tools):
    name = f"{PREFIX}-pg-{secrets.token_hex(3)}"
    started = _run(["docker", "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=x",
                    PG_IMAGE])
    assert started.returncode == 0, started.stderr
    try:
        def exec_(*argv, **kw):
            return _run(["docker", "exec", "-i", name, *argv], **kw)

        _wait(lambda: exec_("pg_isready", "-U", "postgres").returncode == 0
              and exec_("psql", "-U", "postgres", "-c", "select 1").returncode == 0,
              "postgres")
        sql = (f"CREATE TABLE visitors (id int, phone text);"
               f"INSERT INTO visitors SELECT g, '{PHONE}' FROM generate_series(1, 500) g;")
        assert exec_("psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-c", sql).returncode == 0
        dump = exec_("pg_dump", "-U", "postgres", "--format=custom", "--compress=0", "postgres")
        assert dump.returncode == 0, dump.stderr
        yield {"dump": dump.stdout,
               "restore_list": lambda data: exec_("pg_restore", "--list", input=data)}
    finally:
        _run(["docker", "rm", "-fv", name])


@pytest.fixture(scope="module")
def vault(tools, tmp_path_factory):
    out = tmp_path_factory.mktemp("vault")
    key = offsite_keys.make_vault_key(out, "padyar-backup-live")
    other = offsite_keys.make_vault_key(out, "someone-else-live")
    yield key, other
    offsite_keys.destroy(key)
    offsite_keys.destroy(other)


def _sftp(server, batch, known_hosts=None):
    argv = ["sftp", "-b", "-", "-F", "/dev/null", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts or server['known_hosts']}",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
            "-i", server["identity"], "-P", str(server["port"]), "backup@127.0.0.1"]
    return subprocess.run(argv, input=batch, capture_output=True, text=True, timeout=120)


def _remote_names(server):
    out = _sftp(server, "ls -1 /upload\n").stdout
    return sorted(line.rsplit("/", 1)[-1] for line in out.splitlines()
                  if line and not line.startswith("sftp>"))


@pytest.fixture
def install(tmp_path, monkeypatch, sftp_server, postgres, vault):
    """One configured install with an empty destination. Returns a helper
    that makes a verified-looking backup and copies it off-site."""
    import app.config as config
    from app.services import applog, backup, pg_backup
    key, _ = vault
    _sftp(sftp_server, "-rm /upload/*\n")
    root = tmp_path / "postgres"
    root.mkdir()
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(root))
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        f"sftp:backup@127.0.0.1:{sftp_server['port']}:/upload")
    monkeypatch.setattr(config, "OFFSITE_SFTP_IDENTITY_FILE", sftp_server["identity"])
    monkeypatch.setattr(config, "OFFSITE_SFTP_KNOWN_HOSTS", sftp_server["known_hosts"])
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", key.public_file)
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", key.fingerprint)
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", None)
    monkeypatch.setattr(backup, "configured_keep", lambda: 14)
    monkeypatch.setattr(applog, "service", lambda *a, **k: 0)
    dump = postgres["dump"]

    def copy(backup_id):
        from app.services import backup_offsite
        d = root / backup_id
        d.mkdir()
        (d / "padyar.dump").write_bytes(dump)
        manifest = {"backup_id": backup_id, "file": "padyar.dump",
                    "sha256": hashlib.sha256(dump).hexdigest(),
                    "verification": {"status": "verified"}}
        (d / "manifest.json").write_text(json.dumps(manifest))
        return backup_offsite.copy_verified_dump(backup_id, manifest)

    return copy


def _id(day):
    return f"pg_202610{day:02d}_030000_{day:06x}"


def test_the_copy_is_encrypted_and_the_paper_key_restores_the_exact_dump(
        install, sftp_server, postgres, vault, tmp_path):
    key, _ = vault
    dump = postgres["dump"]

    result = install(_id(1))

    assert result["status"] == "copied", result["error"]
    name = f"{_id(1)}.dump.gpg"
    assert _remote_names(sftp_server) == [name]
    fetched = tmp_path / name
    assert _sftp(sftp_server, f'get /upload/{name} "{fetched}"\n').returncode == 0
    data = fetched.read_bytes()
    assert result["encrypted_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["remote_bytes"] == len(data)

    assert postgres["restore_list"](dump).returncode == 0, "allow-control: the plain dump lists"
    assert postgres["restore_list"](data).returncode != 0, "(a) the upload is not a dump"
    assert PHONE.encode() in dump and PHONE.encode() not in data

    restored = tmp_path / "restored.dump"
    assert key.decrypt(str(fetched), str(restored)).returncode == 0
    assert hashlib.sha256(restored.read_bytes()).digest() == hashlib.sha256(dump).digest(), "(b)"


def test_a_wrong_fingerprint_uploads_nothing(install, sftp_server, vault, monkeypatch):
    import app.config as config
    _, other = vault
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", other.fingerprint)

    result = install(_id(2))

    assert result["status"] == "failed"
    assert _remote_names(sftp_server) == []


def test_prune_keeps_exactly_n_of_our_copies(install, sftp_server, monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 2)
    foreign = tmp_path / "operator-notes.txt"
    foreign.write_text("not ours")
    assert _sftp(sftp_server, f'put "{foreign}" /upload/operator-notes.txt\n').returncode == 0

    for day in (3, 4, 5):
        assert install(_id(day))["status"] == "copied"

    assert _remote_names(sftp_server) == sorted(
        [f"{_id(4)}.dump.gpg", f"{_id(5)}.dump.gpg", "operator-notes.txt"])


def test_a_wrong_host_key_is_refused(install, sftp_server, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "OFFSITE_SFTP_KNOWN_HOSTS", sftp_server["wrong_known_hosts"])

    result = install(_id(6))

    assert result["status"] == "failed"
    assert result["exit_code"] == 255
    assert _remote_names(sftp_server) == []
