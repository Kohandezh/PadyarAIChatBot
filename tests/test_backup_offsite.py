"""Off-site copy of a VERIFIED backup dump — the second failure domain.

The gap this file pins (2026-09-14 assessment): every pg_dump lands under
backups/postgres on the SAME host as the database it protects. One
host-level event (disk death, ransomware, a bad rm) erases the data and
every copy of it together.

What is asserted here, with subprocess and the filesystem under test control:

  * a configured target receives the dump, and the result is recorded in the
    backup's manifest.json (`offsite` block) plus a service event
  * no OFFSITE_BACKUP_TARGET -> a no-op: nothing copied, nothing written,
    nothing logged
  * every off-site failure (rsync exit, timeout, missing mount, unknown
    scheme) is NON-fatal: the local backup stays valid, the failure is
    recorded and logged, and the caller never sees an exception
  * pg_backup.verify() — the single point where a dump proves restorable —
    wires the copy in, and the restore pre-check opts out of it
"""
import hashlib
import json
import os
import subprocess

import pytest

BACKUP_ID = "pg_20260914_030000_ab12cd"
DUMP_BYTES = b"fake-dump-bytes"
DUMP_SHA = hashlib.sha256(DUMP_BYTES).hexdigest()


@pytest.fixture(autouse=True)
def _own_settings_db(tmp_path, monkeypatch):
    """The copy reads the admin panel's destination from the settings table
    first (SPEC-H2). An empty throwaway database here, so a destination saved
    in a developer's own database cannot change these results."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "settings.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """A pg_backup.BACKUP_DIR holding one already-verified-looking backup."""
    from app.services import pg_backup
    root = tmp_path / "postgres"
    d = root / BACKUP_ID
    d.mkdir(parents=True)
    (d / "padyar.dump").write_bytes(DUMP_BYTES)
    (d / "manifest.json").write_text(json.dumps({
        "backup_id": BACKUP_ID,
        "file": "padyar.dump",
        "sha256": DUMP_SHA,
        "verification": {"status": "verified"},
    }), encoding="utf-8")
    monkeypatch.setattr(pg_backup, "BACKUP_DIR", str(root))
    return d


@pytest.fixture
def events(monkeypatch):
    """Capture applog.service calls so no assertion needs the log database."""
    from app.services import applog
    seen = []

    def _record(event, message="", level="info", **f):
        seen.append({"event": event, "level": level, **f})
        return len(seen)

    monkeypatch.setattr(applog, "service", _record)
    return seen


def _manifest(backup_dir):
    return json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))


# ── rsync target ────────────────────────────────────────────────────────

def test_rsync_success_copies_and_records(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "rsync:user@offsite:/srv/padyar-backups")
    argvs = []

    def fake_run(argv, **kwargs):
        argvs.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(backup_offsite.subprocess, "run", fake_run)

    result = backup_offsite.copy_verified_dump(
        BACKUP_ID, _manifest(backup_dir), actor="tester")

    assert result["status"] == "copied"
    argv, kwargs = argvs[0]
    # Fixed argv: no shell, no string interpolation of anything but the dump
    # path and the operator's own target — the pg_backup discipline.
    assert argv[0] == "rsync"
    assert "-a" in argv
    assert "--chmod=F600" in argv
    assert argv[-2] == str(backup_dir / "padyar.dump")
    assert argv[-1] == f"user@offsite:/srv/padyar-backups/{BACKUP_ID}.dump"
    # The copy runs under the timeout the config exposes.
    assert kwargs["timeout"] == config.OFFSITE_BACKUP_TIMEOUT

    block = _manifest(backup_dir)["offsite"]
    assert block["target"] == "rsync:user@offsite:/srv/padyar-backups"
    assert block["source_sha256"] == DUMP_SHA
    assert block["exit_code"] == 0
    assert isinstance(block["duration_ms"], int)
    assert [e["event"] for e in events] == ["backup.offsite.copied"]
    assert events[0]["level"] == "info"


def test_absent_target_is_a_noop(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    called = []
    monkeypatch.setattr(backup_offsite.subprocess, "run",
                        lambda *a, **k: called.append(a))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result is None
    assert called == []
    assert "offsite" not in _manifest(backup_dir)
    assert events == []


def test_rsync_failure_is_nonfatal_but_visible(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "rsync:user@offsite:/srv/padyar-backups")
    monkeypatch.setattr(backup_offsite.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 23, stdout="",
                                                     stderr="no space left"))

    # The whole contract: no exception reaches the caller.
    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert result["exit_code"] == 23
    block = _manifest(backup_dir)["offsite"]
    assert block["status"] == "failed"
    assert block["exit_code"] == 23
    assert events[0]["event"] == "backup.offsite.failed"
    assert events[0]["level"] == "error"


def test_timeout_is_nonfatal(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "rsync:user@offsite:/srv/padyar-backups")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="rsync", timeout=1)

    monkeypatch.setattr(backup_offsite.subprocess, "run", fake_run)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert result["exit_code"] is None
    assert result["error"] == "timeout"
    assert events[0]["event"] == "backup.offsite.failed"


# ── dir target ──────────────────────────────────────────────────────────

def test_dir_copy_writes_the_dump(backup_dir, events, tmp_path, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    mount = tmp_path / "offsite-mount"
    mount.mkdir()
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", f"dir:{mount}")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied"
    dest = mount / _ours(BACKUP_ID, ".dump")
    assert dest.read_bytes() == DUMP_BYTES
    assert (os.stat(dest).st_mode & 0o777) == 0o600
    block = _manifest(backup_dir)["offsite"]
    assert block["exit_code"] == 0
    assert events[0]["event"] == "backup.offsite.copied"


def test_dir_copy_refuses_an_unmounted_path(backup_dir, events, monkeypatch):
    """If the mount is down the copy must FAIL, not quietly write to the
    local disk under the mountpoint — that is the single-host failure this
    feature exists to remove."""
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "dir:/definitely-not-mounted-padyar")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert not os.path.exists("/definitely-not-mounted-padyar")
    assert events[0]["event"] == "backup.offsite.failed"


def test_unknown_target_scheme_fails_softly(backup_dir, events, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "ftp://who-knows/where")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert events[0]["event"] == "backup.offsite.failed"


# ── The wiring into pg_backup.verify() ──────────────────────────────────

def _listing_ok():
    """A pg_restore --list result with more than the 5 entries verify needs."""
    return subprocess.CompletedProcess(
        [], 0, stdout="\n".join(f"entry {i}" for i in range(8)), stderr="")


def test_verify_success_wires_the_offsite_copy(backup_dir, monkeypatch):
    """Remove the hook in pg_backup.verify() and this fails — the off-site
    copy would then have no production caller (anti-scaffold rule)."""
    from app.services import backup_offsite, pg_backup
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: "/usr/bin/true")
    monkeypatch.setattr(pg_backup, "_run", lambda *a, **k: _listing_ok())
    seen = []
    monkeypatch.setattr(
        backup_offsite, "copy_verified_dump",
        lambda backup_id, manifest, actor="":
            seen.append((backup_id, actor)) or {"status": "spied"})

    manifest = pg_backup.verify(BACKUP_ID, actor="tester")

    assert manifest["verification"]["status"] == "verified"
    assert seen == [(BACKUP_ID, "tester")]


def test_verify_can_opt_out_for_the_restore_precheck(backup_dir, monkeypatch):
    """restore() re-verifies before touching anything; a restore must never
    sit behind a network copy, so it passes offsite=False."""
    from app.services import backup_offsite, pg_backup
    monkeypatch.setattr(pg_backup, "_pg_bin", lambda name: "/usr/bin/true")
    monkeypatch.setattr(pg_backup, "_run", lambda *a, **k: _listing_ok())
    seen = []
    monkeypatch.setattr(
        backup_offsite, "copy_verified_dump",
        lambda backup_id, manifest, actor="": seen.append(backup_id))

    manifest = pg_backup.verify(BACKUP_ID, actor="tester", offsite=False)

    assert manifest["verification"]["status"] == "verified"
    assert seen == []


# ── sftp target: encrypted copy to an SFTP-only account (PR H) ───────────
#
# Real gpg (throwaway keys from tests/offsite_keys.py), fake sftp: FakeSftp
# runs the batch the code sends against a directory under tmp_path, so these
# tests pin both the argv and what actually lands at the destination. The
# same flow against a real SFTP server is tests/test_offsite_sftp_live.py.

import shlex  # noqa: E402

from tests import offsite_keys  # noqa: E402

needs_gpg = pytest.mark.skipif(not offsite_keys.gpg_available(),
                               reason="gpg is not installed")
SFTP_TARGET = "sftp:backup@offsite.example:2222:/upload"


DB = "padyar_alpha"


@pytest.fixture(autouse=True)
def install_db(monkeypatch):
    """Every remote name carries the install's database name, read from
    DATABASE_URL at call time. Pin one install for the whole file."""
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:p@127.0.0.1:5432/{DB}")


def _old_id(day):
    return f"pg_202609{day:02d}_030000_{day:06x}"


def _ours(backup_id, ext, db=DB):
    return f"{db}.{backup_id}{ext}"


class FakeSftp:
    """Stands in for the `sftp` binary. Records every call and executes the
    batch (put, -rm, rm, rename, chmod, ls -ln) on `root`, which plays the
    remote filesystem: remote `/upload/x` is `root/upload/x`."""

    def __init__(self, root, real_run, rc=0, truncate=False):
        self.root, self.real_run, self.rc, self.truncate = root, real_run, rc, truncate
        self.calls = []

    def _local(self, remote):
        return self.root / remote.lstrip("/")

    def __call__(self, argv, **kwargs):
        if argv[0] != "sftp":
            return self.real_run(argv, **kwargs)
        self.calls.append((list(argv), kwargs))
        if self.rc != 0:
            return subprocess.CompletedProcess(argv, self.rc, stdout="",
                                               stderr="Host key verification failed.")
        out = []
        for line in kwargs["input"].splitlines():
            ignore = line.startswith("-")
            words = shlex.split(line.lstrip("-"))
            cmd, args = words[0], words[1:]
            try:
                if cmd == "put":
                    data = open(args[0], "rb").read()
                    self._local(args[1]).write_bytes(data[:-1] if self.truncate else data)
                elif cmd == "rm":
                    self._local(args[0]).unlink()
                elif cmd == "rename":
                    self._local(args[0]).rename(self._local(args[1]))
                elif cmd == "chmod":
                    os.chmod(self._local(args[1]), int(args[0], 8))
                elif cmd == "ls":
                    for p in sorted(self._local(args[-1]).iterdir()):
                        size = p.stat().st_size
                        out.append(f"-rw-------    ? 1001     100  {size:>10} Oct  7 03:00 "
                                   f"{args[-1].rstrip('/')}/{p.name}")
            except OSError:
                if not ignore:
                    return subprocess.CompletedProcess(argv, 1, stdout="\n".join(out),
                                                       stderr=f"{cmd} failed")
        return subprocess.CompletedProcess(argv, 0, stdout="\n".join(out) + "\n", stderr="")


@pytest.fixture(scope="module")
def vault(tmp_path_factory):
    if not offsite_keys.gpg_available():
        pytest.skip("gpg is not installed")
    out = tmp_path_factory.mktemp("vault")
    key = offsite_keys.make_vault_key(out, "padyar-backup-test")
    other = offsite_keys.make_vault_key(out, "someone-else")
    yield key, other
    offsite_keys.destroy(key)
    offsite_keys.destroy(other)


@pytest.fixture
def remote(tmp_path):
    root = tmp_path / "remote"
    (root / "upload").mkdir(parents=True)
    return root


@pytest.fixture
def sftp_setup(tmp_path, monkeypatch, vault, remote):
    """A fully configured sftp: install. Returns the FakeSftp."""
    import app.config as config
    from app.services import backup, backup_offsite
    key, _ = vault
    (tmp_path / "id_ed25519").write_text("not a real key, sftp is faked\n")
    (tmp_path / "known_hosts").write_text("[offsite.example]:2222 ssh-ed25519 AAAA\n")
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", SFTP_TARGET)
    monkeypatch.setattr(config, "OFFSITE_SFTP_IDENTITY_FILE", str(tmp_path / "id_ed25519"))
    monkeypatch.setattr(config, "OFFSITE_SFTP_KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", key.public_file)
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", key.fingerprint)
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", None)
    monkeypatch.setattr(backup, "configured_keep", lambda: 14)
    fake = FakeSftp(remote, subprocess.run)
    monkeypatch.setattr(backup_offsite.subprocess, "run", fake)
    return fake


def _uploaded(remote):
    return sorted(p.name for p in (remote / "upload").iterdir())


@pytest.mark.parametrize("target,user,host,port,path", [
    ("sftp:backup@offsite.example:/upload", "backup", "offsite.example", 22, "/upload"),
    ("sftp:backup@offsite.example:2222:/srv/padyar", "backup", "offsite.example", 2222, "/srv/padyar"),
    ("sftp:pg_repo@10.0.0.7:/upload/", "pg_repo", "10.0.0.7", 22, "/upload"),
])
def test_a_well_formed_sftp_target_parses(target, user, host, port, path):
    from app.services import backup_offsite
    t = backup_offsite.parse_sftp_target(target[len("sftp:"):])
    assert (t.user, t.host, t.port, t.path) == (user, host, port, path)


@pytest.mark.parametrize("bad", [
    "offsite.example:/upload",                  # no user
    "backup@offsite.example",                   # no path
    "backup@offsite.example:upload",            # relative path
    "backup@offsite.example:/upload/../etc",    # climbs out
    "backup@offsite.example:/up load",          # space
    "backup@offsite.example:/upload;touch /tmp/x",
    "backup@offsite.example:/upload$(id)",
    "backup@offsite.example:/upload`id`",
    "backup@offsite.example:/upload/*",
    "backup@offsite.example:/up\"load",
    "backup@offsite.example:/upload\nrm x",
    "backup@-oProxyCommand=x:/upload",          # host read as an option
    "-oProxyCommand=x@offsite.example:/upload",
    "backup@offsite.example:0:/upload",
    "backup@offsite.example:70000:/upload",
    "backup@[::1]:/upload",
    "backup@offsite.example:22:22:/upload",
])
def test_a_malformed_sftp_target_is_rejected(bad):
    from app.services import backup_offsite
    with pytest.raises(ValueError):
        backup_offsite.parse_sftp_target(bad)


@needs_gpg
def test_a_malformed_target_runs_no_command_at_all(backup_dir, events, sftp_setup,
                                                    remote, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET",
                        "sftp:backup@offsite.example:/upload;touch /tmp/pwned")
    ran = []
    monkeypatch.setattr(backup_offsite.subprocess, "run",
                        lambda argv, **k: ran.append(argv))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert "target" in result["error"].lower()
    assert ran == [], "neither gpg nor sftp may run for a target that did not parse"
    assert events[0]["event"] == "backup.offsite.failed"


@needs_gpg
def test_an_sftp_copy_is_encrypted_uploaded_and_recorded(backup_dir, events, sftp_setup,
                                                         remote, vault):
    from app.services import backup_offsite
    key, _ = vault

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir), actor="t")

    assert result["status"] == "copied", result["error"]
    name = _ours(BACKUP_ID, ".dump.gpg")
    assert _uploaded(remote) == [name], "only the .gpg file, no plain dump, no .part"
    uploaded = remote / "upload" / name
    assert DUMP_BYTES not in uploaded.read_bytes()
    plain = remote / "decrypted"
    assert key.decrypt(str(uploaded), str(plain)).returncode == 0
    assert plain.read_bytes() == DUMP_BYTES
    assert (os.stat(uploaded).st_mode & 0o777) == 0o600

    block = _manifest(backup_dir)["offsite"]
    assert block["status"] == "copied"
    assert block["remote_file"] == name
    assert block["encrypted_sha256"] == hashlib.sha256(uploaded.read_bytes()).hexdigest()
    assert block["remote_bytes"] == uploaded.stat().st_size
    assert block["source_sha256"] == DUMP_SHA
    assert [e["event"] for e in events] == ["backup.offsite.copied"]
    leftovers = [p.name for p in backup_dir.iterdir() if p.name.endswith(".gpg")]
    assert leftovers == [], "the encrypted temp file must be removed after upload"


@needs_gpg
def test_sftp_runs_by_name_with_a_fixed_argv_and_strict_host_keys(backup_dir, events,
                                                                  sftp_setup, tmp_path):
    from app.services import backup_offsite

    backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    argv, kwargs = sftp_setup.calls[0]
    assert argv[0] == "sftp", "called by name through PATH, like rsync"
    assert "shell" not in kwargs
    joined = " ".join(argv)
    assert "StrictHostKeyChecking=yes" in joined
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in joined
    assert "BatchMode=yes" in joined
    assert "PasswordAuthentication=no" in joined
    assert argv[argv.index("-i") + 1] == str(tmp_path / "id_ed25519")
    assert argv[argv.index("-P") + 1] == "2222"
    assert argv[-1] == "backup@offsite.example"
    assert kwargs["timeout"] > 0


@needs_gpg
@pytest.mark.parametrize("setting", ["OFFSITE_SFTP_IDENTITY_FILE", "OFFSITE_SFTP_KNOWN_HOSTS",
                                     "OFFSITE_GPG_PUBLIC_KEY", "OFFSITE_GPG_FINGERPRINT"])
def test_a_missing_sftp_setting_uploads_nothing(backup_dir, events, sftp_setup, remote,
                                                monkeypatch, setting):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, setting, "")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert setting in result["error"]
    assert sftp_setup.calls == []
    assert _uploaded(remote) == []


@needs_gpg
def test_a_key_file_that_is_missing_uploads_nothing(backup_dir, events, sftp_setup, remote,
                                                     monkeypatch, tmp_path):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", str(tmp_path / "nope.asc"))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert sftp_setup.calls == []
    assert _uploaded(remote) == []


@needs_gpg
def test_a_fingerprint_that_does_not_match_the_key_uploads_nothing(backup_dir, events,
                                                                    sftp_setup, remote,
                                                                    monkeypatch, vault):
    import app.config as config
    from app.services import backup_offsite
    key, other = vault
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", other.public_file)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert "fingerprint" in result["error"].lower()
    assert sftp_setup.calls == []
    assert _uploaded(remote) == []
    assert [p.name for p in backup_dir.iterdir() if p.name.endswith(".gpg")] == []


@needs_gpg
def test_a_private_key_file_is_refused_so_it_never_sits_on_the_server(backup_dir, events,
                                                                      sftp_setup, remote,
                                                                      monkeypatch, vault):
    import app.config as config
    from app.services import backup_offsite
    key, _ = vault
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", key.secret_file)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert "private" in result["error"].lower()
    assert sftp_setup.calls == []


@needs_gpg
def test_encryption_never_touches_the_users_default_keyring(backup_dir, events, sftp_setup,
                                                           monkeypatch, tmp_path):
    from app.services import backup_offsite
    home = tmp_path / "default-gnupg"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    assert list(home.iterdir()) == []


@needs_gpg
def test_a_refused_connection_is_nonfatal_and_prunes_nothing(backup_dir, events, sftp_setup,
                                                             remote):
    from app.services import backup_offsite
    old = remote / "upload" / _ours(_old_id(1), ".dump.gpg")
    old.write_bytes(b"old")
    sftp_setup.rc = 255

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert result["exit_code"] == 255
    assert old.exists()
    assert events[0]["event"] == "backup.offsite.failed"
    assert len(sftp_setup.calls) == 1, "no listing and no prune after a failed upload"


@needs_gpg
def test_a_remote_size_that_does_not_match_fails_the_copy(backup_dir, events, sftp_setup,
                                                          remote):
    from app.services import backup_offsite
    sftp_setup.truncate = True

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert "size" in result["error"].lower()


@needs_gpg
def test_prune_keeps_the_newest_n_and_never_touches_other_files(backup_dir, events,
                                                                sftp_setup, remote,
                                                                monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    up = remote / "upload"
    for day in range(1, 6):
        (up / _ours(_old_id(day), ".dump.gpg")).write_bytes(b"old")
    foreign = ["notes.txt", _ours(_old_id(1), ".dump"), f"{DB}.pg_manual.dump.gpg",
               f"x{_ours(_old_id(3), '.dump.gpg')}",
               f"{_old_id(1)}.dump.gpg", _ours(_old_id(1), ".dump.gpg", db="padyar_beta"),
               _ours(_old_id(2), ".dump.gpg", db="padyar_alpha2")]
    for name in foreign:
        (up / name).write_bytes(b"not ours")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 3)

    result = backup_offsite.copy_verified_dump(BACKUP_ID,
                                               _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    ours = sorted(n for n in _uploaded(remote) if n not in foreign)
    assert ours == [_ours(_old_id(4), ".dump.gpg"), _ours(_old_id(5), ".dump.gpg"),
                    _ours(BACKUP_ID, ".dump.gpg")]
    assert all((up / n).exists() for n in foreign)
    assert sorted(result["pruned"]) == [_ours(_old_id(d), ".dump.gpg") for d in (1, 2, 3)]


@needs_gpg
def test_remote_keep_defaults_to_the_local_keep_setting(backup_dir, events, sftp_setup,
                                                        remote, monkeypatch):
    from app.services import backup, backup_offsite
    for day in range(1, 4):
        (remote / "upload" / _ours(_old_id(day), ".dump.gpg")).write_bytes(b"old")
    monkeypatch.setattr(backup, "configured_keep", lambda: 2)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["remote_keep"] == 2
    assert _uploaded(remote) == [_ours(_old_id(3), ".dump.gpg"), _ours(BACKUP_ID, ".dump.gpg")]


def test_a_dir_target_is_pruned_with_the_same_rule(backup_dir, events, tmp_path,
                                                   monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    mount = tmp_path / "offsite-mount"
    mount.mkdir()
    for day in range(1, 4):
        (mount / _ours(_old_id(day), ".dump")).write_bytes(b"old")
    (mount / "README.txt").write_text("operator file")
    (mount / _ours(_old_id(1), ".dump.gpg")).write_bytes(b"not this pattern")
    (mount / f"{_old_id(1)}.dump").write_bytes(b"written before names carried the db")
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", f"dir:{mount}")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 2)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied"
    assert sorted(p.name for p in mount.iterdir()) == sorted([
        _ours(_old_id(3), ".dump"), _ours(BACKUP_ID, ".dump"), "README.txt",
        _ours(_old_id(1), ".dump.gpg"), f"{_old_id(1)}.dump"])


def test_an_rsync_target_is_not_pruned(backup_dir, events, monkeypatch):
    """rsync: needs a full shell at the destination and has no safe listing;
    pruning there is left to the operator (documented in .env.example)."""
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "rsync:user@offsite:/srv/b")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 1)
    argvs = []
    monkeypatch.setattr(backup_offsite.subprocess, "run", lambda argv, **k: (
        argvs.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied"
    assert len(argvs) == 1 and argvs[0][0] == "rsync"


# ── What the admin page is told ─────────────────────────────────────────

def test_the_view_says_off_when_no_target_is_set(monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    view = backup_offsite.last_result_view([{"backup_id": BACKUP_ID,
                                             "offsite": {"status": "copied"}}])
    assert view == {"configured": False, "state": "off", "attempted_at": None,
                    "backup_id": None}


@pytest.mark.parametrize("block,state", [
    (None, "none"),
    ({"status": "copied", "attempted_at": "2026-10-07T03:00:00+00:00",
      "target": "sftp:u@h:/upload", "error": ""}, "copied"),
    ({"status": "failed", "attempted_at": "2026-10-07T03:00:00+00:00",
      "target": "sftp:u@h:/upload", "error": "FileNotFoundError: /opt/x"}, "failed"),
])
def test_the_view_reads_the_newest_manifest_and_leaks_no_target_or_error(monkeypatch,
                                                                       block, state):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", SFTP_TARGET)
    monkeypatch.setattr(backup_offsite, "encryption_problem", lambda: "", raising=False)
    newest = {"backup_id": BACKUP_ID}
    if block:
        newest["offsite"] = block
    older = {"backup_id": _old_id(1), "offsite": {"status": "copied",
                                                   "attempted_at": "2026-09-01T03:00:00+00:00"}}

    view = backup_offsite.last_result_view([newest, older])

    assert view["configured"] is True
    assert view["state"] == state
    assert view["backup_id"] == BACKUP_ID
    assert set(view) == {"configured", "state", "attempted_at", "backup_id"}


@needs_gpg
@pytest.mark.parametrize("setting", ["OFFSITE_SFTP_IDENTITY_FILE", "OFFSITE_SFTP_KNOWN_HOSTS"])
def test_an_ssh_path_with_a_space_is_refused(backup_dir, events, sftp_setup, remote,
                                             monkeypatch, tmp_path, setting):
    """ssh splits UserKnownHostsFile on whitespace, so a path with a space
    would quietly become two other files. Refuse it instead."""
    import app.config as config
    from app.services import backup_offsite
    spaced = tmp_path / "my keys"
    spaced.mkdir()
    (spaced / "file").write_text("x\n")
    monkeypatch.setattr(config, setting, str(spaced / "file"))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert setting in result["error"]
    assert sftp_setup.calls == []


# ── Two installs, one destination path ──────────────────────────────────
#
# This server runs two installs. If both point at the same destination path
# and prune matched any pg_<id> name, each install would delete the other's
# copies. Names carry the install's database name, and prune only matches
# its own. (The runbook also asks for one path per install.)

def _as_install(monkeypatch, db):
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:p@127.0.0.1:5432/{db}")


def _seed(directory, ext):
    for db in ("padyar_alpha", "padyar_beta"):
        for day in (1, 2, 3):
            (directory / _ours(_old_id(day), ext, db=db)).write_bytes(b"old")


def _names(directory, db, ext):
    return sorted(p.name for p in directory.iterdir()
                  if p.name.startswith(f"{db}.") and p.name.endswith(ext))


def test_two_installs_sharing_one_dir_path_prune_only_their_own(backup_dir, events,
                                                                tmp_path, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    mount = tmp_path / "shared"
    mount.mkdir()
    _seed(mount, ".dump")
    legacy = mount / f"{_old_id(1)}.dump"
    legacy.write_bytes(b"written before names carried the db")
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", f"dir:{mount}")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 2)

    _as_install(monkeypatch, "padyar_alpha")
    assert backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))["status"] == "copied"

    assert _names(mount, "padyar_alpha", ".dump") == [
        _ours(_old_id(3), ".dump", db="padyar_alpha"), _ours(BACKUP_ID, ".dump", db="padyar_alpha")]
    assert len(_names(mount, "padyar_beta", ".dump")) == 3, "the other install's copies survive"

    _as_install(monkeypatch, "padyar_beta")
    assert backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))["status"] == "copied"

    assert _names(mount, "padyar_beta", ".dump") == [
        _ours(_old_id(3), ".dump", db="padyar_beta"), _ours(BACKUP_ID, ".dump", db="padyar_beta")]
    assert len(_names(mount, "padyar_alpha", ".dump")) == 2, "beta's prune left alpha alone"
    assert legacy.exists(), "a name without a db is never deleted by this code"


@needs_gpg
def test_two_installs_sharing_one_sftp_path_prune_only_their_own(backup_dir, events,
                                                                 sftp_setup, remote,
                                                                 monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    up = remote / "upload"
    _seed(up, ".dump.gpg")
    legacy = up / f"{_old_id(1)}.dump.gpg"
    legacy.write_bytes(b"written before names carried the db")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 2)

    _as_install(monkeypatch, "padyar_alpha")
    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    assert result["remote_file"] == _ours(BACKUP_ID, ".dump.gpg", db="padyar_alpha")
    assert _names(up, "padyar_alpha", ".dump.gpg") == [
        _ours(_old_id(3), ".dump.gpg", db="padyar_alpha"),
        _ours(BACKUP_ID, ".dump.gpg", db="padyar_alpha")]
    assert len(_names(up, "padyar_beta", ".dump.gpg")) == 3, "the other install's copies survive"

    _as_install(monkeypatch, "padyar_beta")
    assert backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))["status"] == "copied"

    assert len(_names(up, "padyar_beta", ".dump.gpg")) == 2
    assert len(_names(up, "padyar_alpha", ".dump.gpg")) == 2, "beta's prune left alpha alone"
    assert legacy.exists(), "a name without a db is never deleted by this code"


@pytest.mark.parametrize("url", [
    "postgresql://u:p@127.0.0.1:5432/bad name",
    "postgresql://u:p@127.0.0.1:5432/a.b",
    "postgresql://u:p@127.0.0.1:5432/x;y",
])
def test_a_database_name_unsafe_for_a_file_name_fails_the_copy(backup_dir, events, tmp_path,
                                                               monkeypatch, url):
    import app.config as config
    from app.services import backup_offsite
    mount = tmp_path / "offsite-mount"
    mount.mkdir()
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", f"dir:{mount}")
    monkeypatch.setenv("DATABASE_URL", url)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert list(mount.iterdir()) == []



@needs_gpg
def test_a_stale_part_file_is_removed_only_when_it_is_this_installs_own(backup_dir, events,
                                                                        sftp_setup, remote):
    """A cut or timed-out upload leaves <db>.<id>.dump.gpg.part behind, and
    nothing else ever removes it. The next good copy removes this install's
    own leftovers, never another install's."""
    from app.services import backup_offsite
    up = remote / "upload"
    own = up / _ours(_old_id(2), ".dump.gpg.part")
    other = up / _ours(_old_id(2), ".dump.gpg.part", db="padyar_beta")
    legacy = up / f"{_old_id(2)}.dump.gpg.part"
    for f in (own, other, legacy):
        f.write_bytes(b"half an upload")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    assert not own.exists(), "this install's stale .part is removed"
    assert other.exists(), "another install's .part is never touched"
    assert legacy.exists(), "a name without a db is never touched"
    assert result["removed_parts"] == [own.name]


@needs_gpg
def test_no_part_file_is_removed_when_the_upload_failed(backup_dir, events, sftp_setup, remote):
    from app.services import backup_offsite
    own = remote / "upload" / _ours(_old_id(2), ".dump.gpg.part")
    own.write_bytes(b"half an upload")
    sftp_setup.rc = 255

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert own.exists()


# ── A destination saved in the admin panel (SPEC-H2) ────────────────────
#
# The panel destination wins over OFFSITE_BACKUP_TARGET and brings its own
# login: a pasted secret and a pinned host key instead of the two env files.
# The env files point nowhere here, so a copy that still read them would fail.

PANEL_TARGET = "sftp:backup@offsite.example:2222:/upload"
PANEL_PASSWORD = "Panel-pw-" + "7c1e9b2a"


def _host_blob():
    import base64

    def ssh_string(b):
        return len(b).to_bytes(4, "big") + b
    return base64.b64encode(ssh_string(b"ssh-ed25519") + ssh_string(b"\x44" * 32)).decode()


@pytest.fixture
def panel(sftp_setup, monkeypatch):
    """sftp_setup's install with the destination saved in the panel. Returns
    what each sftp call trusted (its known_hosts text) and its argv."""
    import app.config as config
    from app.services import backup_offsite, offsite_destination
    monkeypatch.setattr(config, "OFFSITE_SFTP_IDENTITY_FILE", "/nonexistent/id")
    monkeypatch.setattr(config, "OFFSITE_SFTP_KNOWN_HOSTS", "/nonexistent/known_hosts")
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "sftp:env@env.example:/env")
    blob = _host_blob()
    seen = {"known_hosts": [], "argv": [], "scans": 0}

    def run(argv, **kwargs):
        if argv[0] == "ssh-keyscan":
            seen["scans"] += 1
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"[offsite.example]:2222 ssh-ed25519 {blob}\n", stderr="")
        if argv[0] == "sftp":
            known = next(a.split("=", 1)[1] for a in argv
                         if a.startswith("UserKnownHostsFile="))
            with open(known) as f:
                seen["known_hosts"].append(f.read())
            seen["argv"].append(list(argv))
        return sftp_setup(argv, **kwargs)

    monkeypatch.setattr(backup_offsite.subprocess, "run", run)
    offsite_destination.save({
        "host": "offsite.example", "port": 2222, "user": "backup", "path": "/upload",
        "auth": "password", "password": PANEL_PASSWORD,
        "fingerprint": offsite_destination.fingerprint_of(blob)})
    return seen


@needs_gpg
def test_a_panel_destination_is_copied_to_instead_of_the_env_target(backup_dir, events,
                                                                    panel, remote):
    from app.services import backup_offsite

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    assert result["target"] == PANEL_TARGET
    assert _uploaded(remote) == [_ours(BACKUP_ID, ".dump.gpg")]
    assert panel["scans"] == 1, "one host-key scan per copy, not one per sftp call"
    assert len(panel["argv"]) == 2
    for known, argv in zip(panel["known_hosts"], panel["argv"]):
        assert known.startswith("[offsite.example]:2222 ssh-ed25519 ")
        assert argv.index("BatchMode=no") < argv.index("-b")
        assert not any("/nonexistent" in a or PANEL_PASSWORD in a for a in argv)


@needs_gpg
def test_a_panel_destination_whose_host_key_does_not_match_uploads_nothing(
        backup_dir, events, panel, remote):
    import base64
    import hashlib

    from app.services import backup_offsite, offsite_destination
    other = "SHA256:" + base64.b64encode(hashlib.sha256(b"x").digest()).decode().rstrip("=")
    offsite_destination.save({
        "host": "offsite.example", "port": 2222, "user": "backup", "path": "/upload",
        "auth": "password", "fingerprint": other})

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert "host_key" in result["error"]
    assert panel["argv"] == []
    assert _uploaded(remote) == []


@needs_gpg
def test_a_panel_destination_still_needs_the_gpg_key_and_scans_nothing_without_it(
        backup_dir, events, panel, remote, monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "")

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed"
    assert "OFFSITE_GPG_PUBLIC_KEY" in result["error"]
    assert "OFFSITE_SFTP_IDENTITY_FILE" not in result["error"]
    assert panel["scans"] == 0 and panel["argv"] == []


@needs_gpg
def test_a_panel_destination_without_its_password_uploads_nothing(backup_dir, events,
                                                                  panel, remote):
    from app.services import backup_offsite, offsite_destination
    offsite_destination.save({
        "host": "offsite.example", "port": 2222, "user": "backup", "path": "/upload",
        "auth": "password", "clear_secret": True,
        "fingerprint": offsite_destination.fingerprint_of(_host_blob())})

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed" and "no_password" in result["error"]
    assert panel["scans"] == 0 and _uploaded(remote) == []


@needs_gpg
def test_the_panel_password_reaches_neither_the_manifest_nor_the_event(backup_dir, events,
                                                                       panel):
    from app.services import backup_offsite

    backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert PANEL_PASSWORD not in (backup_dir / "manifest.json").read_text(encoding="utf-8")
    assert PANEL_PASSWORD not in json.dumps(events, default=str)


# ── Review fix 3: is encryption ready? ──────────────────────────────────
#
# A destination without a usable gpg key passes a connection test, and then
# every nightly copy fails. encryption_problem() is the check, and the copy
# itself runs the very same key check before it encrypts.

def test_an_sftp_target_without_working_encryption_is_not_ready(monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", SFTP_TARGET)
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "")
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", "")

    view = backup_offsite.last_result_view([{"backup_id": BACKUP_ID,
                                             "offsite": {"status": "failed"}}])

    assert view == {"configured": False, "state": "not_ready", "attempted_at": None,
                    "backup_id": None}


@needs_gpg
def test_an_sftp_target_with_working_encryption_is_configured(sftp_setup):
    from app.services import backup_offsite

    assert backup_offsite.encryption_problem() == ""
    assert backup_offsite.last_result_view([])["configured"] is True


def test_an_rsync_target_needs_no_gpg_key_to_be_configured(monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "rsync:user@offsite:/srv/x")
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "")

    assert backup_offsite.last_result_view([])["configured"] is True


@pytest.mark.parametrize("setting", ["OFFSITE_GPG_PUBLIC_KEY", "OFFSITE_GPG_FINGERPRINT"])
def test_encryption_is_not_ready_without_either_gpg_setting(monkeypatch, setting):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "/some/key.asc")
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", "AB" * 20)
    monkeypatch.setattr(config, setting, "")

    assert setting in backup_offsite.encryption_problem()


@needs_gpg
def test_encryption_is_not_ready_when_the_key_file_is_gone(sftp_setup, monkeypatch, tmp_path):
    import app.config as config
    from app.services import backup_offsite
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", str(tmp_path / "gone.asc"))

    assert "OFFSITE_GPG_PUBLIC_KEY" in backup_offsite.encryption_problem()


@needs_gpg
def test_encryption_is_not_ready_when_the_fingerprint_names_another_key(sftp_setup, vault,
                                                                        monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    _, other = vault
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", other.fingerprint)

    assert "mismatch" in backup_offsite.encryption_problem()


@needs_gpg
def test_encryption_is_not_ready_when_the_file_holds_a_private_key(sftp_setup, vault,
                                                                   monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    key, _ = vault
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", key.secret_file)

    assert "PRIVATE" in backup_offsite.encryption_problem()


@needs_gpg
def test_the_readiness_check_does_not_touch_the_users_keyring(sftp_setup, monkeypatch,
                                                              tmp_path):
    from app.services import backup_offsite
    home = tmp_path / "user-gnupg"
    home.mkdir()
    monkeypatch.setenv("GNUPGHOME", str(home))

    assert backup_offsite.encryption_problem() == ""
    assert list(home.iterdir()) == []


# ── One manifest, several writers (final review of PR #180) ─────────────
#
# verify() and the restore drill write their keys through
# pg_backup._update_manifest: an exclusive lock, a fresh read, only their
# own keys, then replace. _record() wrote `offsite` with its own read and
# replace and no lock, so a drill write in flight could erase it, or it
# could erase the drill's key. Now it goes through the same function.

def _result(status="copied"):
    return {"target": "dir:/mnt/x", "source_sha256": DUMP_SHA, "status": status,
            "attempted_at": "2026-10-08T03:00:00+00:00", "exit_code": 0, "error": "",
            "duration_ms": 5}


@pytest.mark.parametrize("other_key,other_value", [
    ("drill", {"status": "passed", "finished_at": "2026-10-08T03:05:00+00:00"}),
    ("verification", {"status": "verified", "checked_at": "2026-10-08T03:01:00+00:00"}),
])
def test_an_offsite_write_during_another_manifest_write_loses_no_key(
        backup_dir, events, monkeypatch, other_key, other_value):
    """The other writer is paused AFTER it read the manifest and BEFORE it
    writes. The off-site result is recorded in that gap. Both keys must be
    in the file afterwards, whichever order the writes end in."""
    import tempfile
    import threading

    from app.services import backup_offsite, pg_backup
    read_done, release = threading.Event(), threading.Event()
    real_mkstemp = tempfile.mkstemp

    def pause_the_other_writer(*args, **kwargs):
        if threading.current_thread().name == "other-writer":
            read_done.set()
            release.wait(10)
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(pg_backup.tempfile, "mkstemp", pause_the_other_writer)
    other = threading.Thread(name="other-writer", target=pg_backup._update_manifest,
                             args=(BACKUP_ID, {other_key: other_value}))
    other.start()
    assert read_done.wait(10), "the other writer never reached its write"
    offsite = threading.Thread(target=backup_offsite._record,
                               args=(BACKUP_ID, None, _result()))
    offsite.start()
    offsite.join(1.0)   # old code: finished here, with no lock to wait for
    release.set()
    other.join(10)
    offsite.join(10)

    stored = _manifest(backup_dir)
    assert stored[other_key] == other_value, f"the {other_key} write was lost"
    assert stored["offsite"]["status"] == "copied", "the offsite write was lost"
    assert stored["sha256"] == DUMP_SHA and stored["file"] == "padyar.dump"


def test_the_offsite_result_goes_through_the_manifest_lock(backup_dir, events, monkeypatch):
    from app.services import backup_offsite, pg_backup
    calls = []
    real = pg_backup._update_manifest
    monkeypatch.setattr(pg_backup, "_update_manifest",
                        lambda backup_id, fields: calls.append((backup_id, fields))
                        or real(backup_id, fields))
    manifest = _manifest(backup_dir)

    backup_offsite._record(BACKUP_ID, manifest, _result())

    assert calls == [(BACKUP_ID, {"offsite": _result()})], "only its own key, via the lock"
    assert manifest["offsite"] == _result(), "the caller's copy is updated too"
    assert _manifest(backup_dir)["offsite"] == _result()


def test_recording_never_raises_when_the_backup_is_gone(backup_dir, events):
    import shutil

    from app.services import backup_offsite
    shutil.rmtree(backup_dir)

    backup_offsite._record(BACKUP_ID, None, _result("failed"))

    assert not backup_dir.exists(), "no folder or lock file is created for a deleted backup"
    assert [e["event"] for e in events] == ["backup.offsite.failed"]
