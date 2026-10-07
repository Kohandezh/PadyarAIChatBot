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
    dest = mount / f"{BACKUP_ID}.dump"
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


def _old_id(day):
    return f"pg_202609{day:02d}_030000_{day:06x}"


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
    name = f"{BACKUP_ID}.dump.gpg"
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
    old = remote / "upload" / f"{_old_id(1)}.dump.gpg"
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
        (up / f"{_old_id(day)}.dump.gpg").write_bytes(b"old")
    foreign = ["notes.txt", f"{_old_id(1)}.dump", "pg_manual.dump.gpg",
               f"{_old_id(2)}.dump.gpg.part", f"x{_old_id(3)}.dump.gpg"]
    for name in foreign:
        (up / name).write_bytes(b"not ours")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 3)

    result = backup_offsite.copy_verified_dump(BACKUP_ID,
                                               _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    ours = sorted(n for n in _uploaded(remote) if n not in foreign)
    assert ours == [f"{_old_id(4)}.dump.gpg", f"{_old_id(5)}.dump.gpg",
                    f"{BACKUP_ID}.dump.gpg"]
    assert all((up / n).exists() for n in foreign)
    assert sorted(result["pruned"]) == [f"{_old_id(d)}.dump.gpg" for d in (1, 2, 3)]


@needs_gpg
def test_remote_keep_defaults_to_the_local_keep_setting(backup_dir, events, sftp_setup,
                                                        remote, monkeypatch):
    from app.services import backup, backup_offsite
    for day in range(1, 4):
        (remote / "upload" / f"{_old_id(day)}.dump.gpg").write_bytes(b"old")
    monkeypatch.setattr(backup, "configured_keep", lambda: 2)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["remote_keep"] == 2
    assert _uploaded(remote) == [f"{_old_id(3)}.dump.gpg", f"{BACKUP_ID}.dump.gpg"]


def test_a_dir_target_is_pruned_with_the_same_rule(backup_dir, events, tmp_path,
                                                   monkeypatch):
    import app.config as config
    from app.services import backup_offsite
    mount = tmp_path / "offsite-mount"
    mount.mkdir()
    for day in range(1, 4):
        (mount / f"{_old_id(day)}.dump").write_bytes(b"old")
    (mount / "README.txt").write_text("operator file")
    (mount / f"{_old_id(1)}.dump.gpg").write_bytes(b"not this pattern")
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", f"dir:{mount}")
    monkeypatch.setattr(config, "OFFSITE_REMOTE_KEEP", 2)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied"
    assert sorted(p.name for p in mount.iterdir()) == sorted([
        f"{_old_id(3)}.dump", f"{BACKUP_ID}.dump", "README.txt", f"{_old_id(1)}.dump.gpg"])


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
