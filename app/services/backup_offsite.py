"""Off-site copy of a VERIFIED PostgreSQL backup dump.

WHY A SECOND COPY
-----------------
pg_backup writes every dump under BASE_DIR/backups/postgres — on the same
host, and usually the same disk, as the database it protects. One host-level
event (disk death, ransomware, a bad rm) then takes the database AND every
copy of it together. This module copies the dump that just passed verify()
to a second failure domain: another machine over rsync/ssh, or an
already-mounted network path.

FAILURE IS NON-FATAL — BY DESIGN
--------------------------------
The local backup is valid the moment verify() passes. The off-site copy is
hardening, not a gate: a full remote disk, a dead network or a wrong target
must never turn a good nightly backup into a failed job, and must never
raise into the backup pipeline. Every failure is recorded in the backup's
manifest.json (`offsite` block) and as a service event, then swallowed.

COMMAND SAFETY
--------------
The target comes from OFFSITE_BACKUP_TARGET (operator env), or from an
sftp: destination an admin saved in the panel (offsite_destination.py,
checked there field by field and again by parse_sftp_target below). It is
dispatched by strict prefix. rsync runs with a FIXED argv list — no shell,
no interpolation of anything except the dump path and the operator's own
target string, the same discipline pg_backup applies to pg_dump/pg_restore.

THE sftp: TARGET (PR H)
-----------------------
For a destination that is an SFTP-only account (no shell, so rsync cannot
run there). Three rules, each one a reason this target exists:

  * Encrypted first. The dump is encrypted with gpg to a PUBLIC key
    (OFFSITE_GPG_PUBLIC_KEY, checked against OFFSITE_GPG_FINGERPRINT) and
    only the .gpg file is uploaded. The private key is on paper, off the
    server, so neither this server nor the destination can read the copy.
    gpg runs with a throwaway --homedir per copy and --recipient-file, so
    nothing is imported into any keyring and the app user's own GnuPG home
    is never touched. A key file that holds a PRIVATE key is refused.
  * Strict host keys. For the env destination, key login only:
    StrictHostKeyChecking=yes against OFFSITE_SFTP_KNOWN_HOSTS, the identity
    in OFFSITE_SFTP_IDENTITY_FILE, BatchMode and no password or
    keyboard-interactive login. A panel destination (SPEC-H2) logs in with
    its own pasted key or password and a pinned host key instead; see
    offsite_destination.login(). -F /dev/null either way, so the app user's
    ~/.ssh/config cannot change any of that.
  * Nothing reaches sftp's own command parser unchecked. The batch commands
    go to `sftp -b -` on stdin, and sftp parses quotes and glob characters in
    them. So the target path is limited to letters, digits and . _ - /, the
    file names come from the backup id (already a fixed pattern), and the
    local temp path is refused if it holds a quote or a glob character.

After the upload, a listing checks that the remote size equals the local
encrypted size, and the encrypted file's sha256 goes into the manifest.
A size check, not a download: re-reading every dump over the uplink would
double the nightly traffic, and a truncated or missing upload (the failure
seen in practice) shows up as a size mismatch. The sha256 lets anyone check
a downloaded copy later.

PRUNE
-----
After a successful copy, sftp: and dir: destinations keep the newest
OFFSITE_REMOTE_KEEP copies (default: the local keep setting) and delete the
older ones. Only names this module writes FOR THIS INSTALL are candidates:
`<database>.pg_<date>_<time>_<6 hex>.dump.gpg` for sftp:, `.dump` for dir:,
where <database> is the install's own database name from DATABASE_URL
(padyar_<slug>). One server runs two installs; if both pointed at the same
path, a bare `pg_<id>` pattern would let each delete the other's copies.
Anything else at the destination is never touched, including copies an
older version of this module wrote without the database name. On sftp:,
the same prefix rule also removes this install's own leftover `.part`
files (from a cut or timed-out upload) after the next good copy.

rsync: is not pruned: it would need a remote shell command or an
`rsync --delete` filter run against a directory the operator may share
with other files.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import logger
from app.services import applog

_RSYNC_PREFIX = "rsync:"
_DIR_PREFIX = "dir:"
_SFTP_PREFIX = "sftp:"

# The names this module writes are `<database>.<backup id><ext>`, and only
# this install's own names may be pruned. Same id pattern as pg_backup._ID_RE.
_ID = r"pg_\d{8}_\d{6}_[0-9a-f]{6}"
_SFTP_EXT = ".dump.gpg"
_DIR_EXT = ".dump"
# A database name goes into a remote file name and an sftp batch command.
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,63}$")

# user@host[:port]:/path. A host name or IPv4 only: an IPv6 literal would
# need brackets and colons, which this format cannot tell from the port.
# The three parts are also what the admin panel's form accepts
# (offsite_destination.py), so the panel cannot save a target this refuses.
SFTP_USER = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}"
SFTP_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
SFTP_PATH = r"/[A-Za-z0-9._/-]*"
_SFTP_TARGET_RE = re.compile(
    rf"^(?P<user>{SFTP_USER})"
    rf"@(?P<host>{SFTP_HOST})"
    r"(?::(?P<port>\d{1,5}))?"
    rf":(?P<path>{SFTP_PATH})$")
# Characters sftp's batch parser would treat as quoting, escaping or globbing.
_SFTP_UNSAFE_LOCAL = re.compile(r'["\'\\*?\[\]\n\r]')


def _destination():
    """(target text, panel destination or None).

    A destination saved in the admin panel wins over OFFSITE_BACKUP_TARGET
    (SPEC-H2, see offsite_destination.py); with the panel empty, the env
    target works exactly as before. Both are read at CALL time, not import
    time, so a panel save applies to the next copy and tests monkeypatch one
    source of truth.
    """
    from app.services import offsite_destination
    panel = offsite_destination.stored()
    if panel is not None:
        return panel.target, panel
    from app.config import OFFSITE_BACKUP_TARGET
    return (OFFSITE_BACKUP_TARGET or "").strip(), None


def _target() -> str:
    return _destination()[0]


def _timeout() -> int:
    from app.config import OFFSITE_BACKUP_TIMEOUT
    return OFFSITE_BACKUP_TIMEOUT


def _install_name() -> str:
    """This install's database name (padyar_<slug>), the prefix of every
    remote name. Taken from pg_backup's own DATABASE_URL split, so the
    dump and its copy name the same database."""
    from app.services import pg_backup
    name = pg_backup._conn_parts()["dbname"]
    if not _DB_NAME_RE.match(name or ""):
        raise ValueError("the database name in DATABASE_URL cannot be used in a "
                         "file name (letters, digits, _ and - only)")
    return name


def _remote_name(backup_id: str, ext: str) -> str:
    return f"{_install_name()}.{backup_id}{ext}"


def _own_names_re(ext: str):
    """The names this install writes with `ext`, and nothing else."""
    return re.compile(rf"^{re.escape(_install_name())}\.{_ID}{re.escape(ext)}$")


def copy_verified_dump(backup_id: str, manifest: dict, actor: str = ""):
    """Copy one verified dump off-site. Returns the result block, or None
    when no target is configured. NEVER raises — see the module docstring.
    """
    from app.services import pg_backup

    target, panel = _destination()
    if not target:
        return None

    started = time.perf_counter()
    result = {
        "target": target,
        "source_sha256": (manifest or {}).get("sha256", ""),
        "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "failed",
        "exit_code": None,
        "error": "",
    }
    try:
        dump = pg_backup.member_path(backup_id, "padyar.dump")
        if not dump:
            raise FileNotFoundError("padyar.dump")
        if target.startswith(_RSYNC_PREFIX):
            rc = _rsync(dump, target[len(_RSYNC_PREFIX):], backup_id)
            result["exit_code"] = rc
            if rc != 0:
                raise RuntimeError(f"rsync exited {rc}")
        elif target.startswith(_DIR_PREFIX):
            directory = _dir_copy(dump, target[len(_DIR_PREFIX):], backup_id)
            result["exit_code"] = 0
            _prune_dir(directory, result)
        elif target.startswith(_SFTP_PREFIX):
            _sftp_copy(dump, target[len(_SFTP_PREFIX):], backup_id, result, panel)
        else:
            raise ValueError(
                f"unrecognized OFFSITE_BACKUP_TARGET (expected "
                f"{_RSYNC_PREFIX}…, {_DIR_PREFIX}… or {_SFTP_PREFIX}…)")
        result["status"] = "copied"
    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except Exception as e:  # noqa: BLE001 — non-fatal by contract
        result["error"] = f"{type(e).__name__}: {e}"[:300]
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)

    _record(backup_id, manifest, result, actor=actor)
    return result


def _rsync(dump: str, remote: str, backup_id: str) -> int:
    """rsync to `remote` (no trailing slash). Returns the exit code."""
    dest = f"{remote.rstrip('/')}/{backup_id}.dump"
    proc = subprocess.run(
        ["rsync", "-a", "--chmod=F600", dump, dest],
        capture_output=True, text=True, timeout=_timeout())
    if proc.returncode != 0:
        logger.error("[backup_offsite] rsync failed rc=%s: %s",
                     proc.returncode, (proc.stderr or "")[:400])
    return proc.returncode


def _dir_copy(dump: str, directory: str, backup_id: str) -> str:
    """Copy onto an ALREADY-MOUNTED path, then fsync so a crash right after
    the copy does not report a file the storage never received."""
    directory = directory.rstrip("/") or "/"
    # The directory must already exist: creating it here would silently
    # "succeed" onto the local disk when the mount is down — the exact
    # single-host failure this feature exists to remove.
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"{directory} is not a mounted directory")
    dest = os.path.join(directory, _remote_name(backup_id, _DIR_EXT))
    shutil.copyfile(dump, dest)
    os.chmod(dest, 0o600)
    fd = os.open(dest, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    # Best-effort: some platforms/filesystems refuse fsync on a directory fd.
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return directory


def _remote_keep() -> int:
    """OFFSITE_REMOTE_KEEP, else the local keep setting (Settings > Backup),
    read at call time so an admin change applies to the next copy."""
    from app.config import OFFSITE_REMOTE_KEEP
    if OFFSITE_REMOTE_KEEP:
        return max(1, OFFSITE_REMOTE_KEEP)
    try:
        from app.services import backup
        return max(1, backup.configured_keep())
    except Exception:  # noqa: BLE001 (a settings read must not stop a copy)
        from app.services import pg_backup
        return pg_backup.KEEP


def _to_delete(names, pattern, keep: int) -> list:
    """The names matching `pattern` beyond the newest `keep`. Within one
    install's prefix the backup id starts with its timestamp, so name order
    is age order."""
    ours = sorted((n for n in names if pattern.match(n)), reverse=True)
    return ours[keep:]


def _prune_dir(directory: str, result: dict) -> None:
    keep = _remote_keep()
    result["remote_keep"] = keep
    result["pruned"] = []
    try:
        for name in _to_delete(os.listdir(directory), _own_names_re(_DIR_EXT), keep):
            os.remove(os.path.join(directory, name))
            result["pruned"].append(name)
    except OSError as e:
        # The copy itself succeeded; a failed prune costs space, not data.
        result["prune_error"] = f"{type(e).__name__}: {e}"[:300]


# ── sftp: ────────────────────────────────────────────────────────────────

@dataclass
class SftpTarget:
    user: str
    host: str
    port: int
    path: str


def parse_sftp_target(text: str) -> SftpTarget:
    """`user@host:/path` or `user@host:port:/path` -> SftpTarget, else
    ValueError. Strict on purpose: everything here ends up in sftp's argv
    or in its batch commands."""
    m = _SFTP_TARGET_RE.match(text or "")
    if not m:
        raise ValueError("malformed sftp: target (expected sftp:user@host:/path "
                         "or sftp:user@host:port:/path)")
    path = m.group("path").rstrip("/") or "/"
    if ".." in path.split("/"):
        raise ValueError("malformed sftp: target (the path may not contain ..)")
    port = int(m.group("port") or 22)
    if not 1 <= port <= 65535:
        raise ValueError("malformed sftp: target (port out of range)")
    return SftpTarget(m.group("user"), m.group("host"), port, path)


_SSH_FILE_SETTINGS = ("OFFSITE_SFTP_IDENTITY_FILE", "OFFSITE_SFTP_KNOWN_HOSTS")
_GPG_SETTINGS = ("OFFSITE_GPG_PUBLIC_KEY", "OFFSITE_GPG_FINGERPRINT")


def _sftp_settings(ssh_files: bool = True) -> dict:
    """The env settings one sftp: copy needs. A panel destination brings its
    own login (pasted secret, pinned host key), so it needs only the gpg
    pair; the env destination also needs the two ssh files."""
    from app import config
    names = (_SSH_FILE_SETTINGS if ssh_files else ()) + _GPG_SETTINGS
    values = {n: (getattr(config, n, "") or "").strip() for n in names}
    missing = [n for n, v in values.items() if not v]
    if missing:
        raise ValueError(f"sftp: target needs {', '.join(missing)}; nothing uploaded")
    values["OFFSITE_GPG_FINGERPRINT"] = (
        values["OFFSITE_GPG_FINGERPRINT"].replace(" ", "").upper())
    for n in (n for n in names if n != "OFFSITE_GPG_FINGERPRINT"):
        if not os.path.isfile(values[n]):
            raise FileNotFoundError(f"{n} is not a file; nothing uploaded")
    # ssh reads UserKnownHostsFile as a whitespace-separated LIST of files,
    # so a space would quietly point it at two other paths.
    for n in _SSH_FILE_SETTINGS if ssh_files else ():
        if any(c.isspace() for c in values[n]):
            raise ValueError(f"{n} may not contain spaces; nothing uploaded")
    return values


@contextmanager
def _login(settings: dict, panel):
    """The env login, or the panel's (which pins the host key over the
    network and removes its temp files when the copy is done)."""
    if panel is None:
        yield _env_login(settings)
        return
    from app.services import offsite_destination
    with offsite_destination.login(panel) as login:
        yield login


def _gpg(home: str, *args):
    return subprocess.run(
        ["gpg", "--homedir", home, "--batch", "--no-tty", "--no-autostart", *args],
        capture_output=True, text=True, timeout=_timeout(),
        env={**os.environ, "GNUPGHOME": home})


def _encrypt(dump: str, out: str, key_file: str, fingerprint: str) -> None:
    """Encrypt `dump` to the public key in `key_file` and write `out`.
    Raises before writing anything when the key is not the expected one."""
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        shown = _gpg(home, "--with-colons", "--show-keys", key_file)
        if shown.returncode != 0:
            raise ValueError("OFFSITE_GPG_PUBLIC_KEY is not a readable gpg key")
        records = [line.split(":") for line in shown.stdout.splitlines()]
        if any(r[0] in ("sec", "ssb") for r in records):
            raise ValueError("OFFSITE_GPG_PUBLIC_KEY holds a PRIVATE key; put only "
                             "the public key on the server")
        primaries = [i for i, r in enumerate(records) if r[0] == "pub"]
        if len(primaries) != 1:
            raise ValueError("OFFSITE_GPG_PUBLIC_KEY must hold exactly one key")
        fprs = [r[9] for r in records[primaries[0] + 1:] if r[0] == "fpr"]
        if not fprs or fprs[0].upper() != fingerprint:
            raise ValueError("gpg fingerprint mismatch: OFFSITE_GPG_PUBLIC_KEY is "
                             "not the key OFFSITE_GPG_FINGERPRINT names")
        enc = _gpg(home, "--recipient-file", key_file, "--output", out,
                   "--encrypt", "--", dump)
        if enc.returncode != 0:
            raise RuntimeError(f"gpg exited {enc.returncode}: "
                               f"{(enc.stderr or '').strip()[-200:]}")
    finally:
        shutil.rmtree(home, ignore_errors=True)


@dataclass
class SftpLogin:
    """How one sftp run trusts the server and proves who it is.

    `before_batch` goes in front of `-b`: sftp's `-b` adds BatchMode=yes,
    ssh keeps the FIRST value it sees for an option, and BatchMode=yes turns
    password login off. Only the panel's password login puts anything here.
    """
    known_hosts: str
    options: list
    before_batch: list = field(default_factory=list)
    env: dict = None


def _env_login(settings: dict) -> SftpLogin:
    """H1's login: the key file and known_hosts file named in env."""
    return SftpLogin(
        known_hosts=settings["OFFSITE_SFTP_KNOWN_HOSTS"],
        options=["-o", "BatchMode=yes",
                 "-o", "IdentitiesOnly=yes",
                 "-o", "PasswordAuthentication=no",
                 "-o", "KbdInteractiveAuthentication=no",
                 "-i", settings["OFFSITE_SFTP_IDENTITY_FILE"]])


def _sftp(target: SftpTarget, login: SftpLogin, batch: str, timeout: float = None,
          connect_timeout: int = 30):
    argv = [
        "sftp", *login.before_batch, "-b", "-", "-F", "/dev/null",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={login.known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "UpdateHostKeys=no",
        *login.options,
        "-o", f"ConnectTimeout={connect_timeout}",
        "-P", str(target.port),
        "--", f"{target.user}@{target.host}",
    ]
    return subprocess.run(argv, input=batch, capture_output=True, text=True,
                          timeout=timeout or _timeout(), env=login.env)


def _remote_sizes(listing: str) -> dict:
    """Parse `ls -ln` output into {name: size} for regular files. A line looks
    like `-rw-------  ? 1001 100  1234 Oct  7 03:00 /upload/<name>`."""
    sizes = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 9 and parts[0].startswith("-") and parts[4].isdigit():
            sizes[parts[-1].rsplit("/", 1)[-1]] = int(parts[4])
    return sizes


def _sftp_copy(dump: str, target_text: str, backup_id: str, result: dict,
               panel=None) -> None:
    target = parse_sftp_target(target_text)
    settings = _sftp_settings(ssh_files=panel is None)
    name = _remote_name(backup_id, _SFTP_EXT)
    if not _own_names_re(_SFTP_EXT).match(name):
        raise ValueError("unexpected backup id")
    encrypted = os.path.join(os.path.dirname(dump), f".{name}.tmp")
    if _SFTP_UNSAFE_LOCAL.search(encrypted):
        raise ValueError("the backup directory path holds a quote or glob character")
    remote = f"{target.path.rstrip('/')}/{name}"
    with _login(settings, panel) as login:
        try:
            _encrypt(dump, encrypted, settings["OFFSITE_GPG_PUBLIC_KEY"],
                     settings["OFFSITE_GPG_FINGERPRINT"])
            with open(encrypted, "rb") as f:
                result["encrypted_sha256"] = hashlib.sha256(f.read()).hexdigest()
            local_size = os.path.getsize(encrypted)
            result["encrypted_bytes"] = local_size
            result["remote_file"] = name

            # Upload under .part, then rename: a cut connection leaves a
            # .part, never a short file under the final name that prune
            # would count.
            up = _sftp(target, login,
                       f'put "{encrypted}" {remote}.part\n'
                       f"chmod 600 {remote}.part\n"
                       f"-rm {remote}\n"
                       f"rename {remote}.part {remote}\n")
            result["exit_code"] = up.returncode
            if up.returncode != 0:
                logger.error("[backup_offsite] sftp upload failed rc=%s: %s",
                             up.returncode, (up.stderr or "")[-400:])
                raise RuntimeError(f"sftp exited {up.returncode}")
        finally:
            try:
                os.remove(encrypted)
            except OSError:
                pass

        listed = _sftp(target, login, f"ls -ln {target.path}\n")
        if listed.returncode != 0:
            raise RuntimeError(f"sftp listing exited {listed.returncode}")
        sizes = _remote_sizes(listed.stdout)
        result["remote_bytes"] = sizes.get(name)
        if sizes.get(name) != local_size:
            raise RuntimeError(f"remote size {sizes.get(name)} does not match the "
                               f"encrypted file's {local_size} bytes")

        keep = _remote_keep()
        result["remote_keep"] = keep
        result["pruned"] = []
        result["removed_parts"] = []
        old = _to_delete(sizes, _own_names_re(_SFTP_EXT), keep)
        # A cut or timed-out upload leaves its .part behind, and nothing else
        # ever removes it. This upload just renamed its own .part, so any
        # .part with this install's prefix that is still listed is a leftover.
        parts = sorted(n for n in sizes
                       if _own_names_re(_SFTP_EXT + ".part").match(n))
        if old or parts:
            pruned = _sftp(target, login, "".join(
                f"rm {target.path.rstrip('/')}/{n}\n" for n in old + parts))
            if pruned.returncode == 0:
                result["pruned"] = old
                result["removed_parts"] = parts
            else:
                # The new copy is safe; a failed prune costs space, not data.
                result["prune_error"] = f"sftp rm exited {pruned.returncode}"


# ── What the admin page shows ────────────────────────────────────────────

def last_result_view(manifests: list) -> dict:
    """The newest backup's off-site result, for Infrastructure > Backups.

    `manifests` is newest first (pg_backup.list_backups()). Only a state and
    a time leave the server: the target string and the error text can hold
    a host, a user and a path, and they stay in the manifest and the log.
    """
    if not _target():
        return {"configured": False, "state": "off", "attempted_at": None,
                "backup_id": None}
    newest = (manifests or [{}])[0] or {}
    block = newest.get("offsite") or {}
    state = {"copied": "copied", "failed": "failed"}.get(block.get("status"), "none")
    return {"configured": True, "state": state,
            "attempted_at": block.get("attempted_at"),
            "backup_id": newest.get("backup_id")}


def _record(backup_id: str, manifest: dict, result: dict, actor: str = "") -> None:
    """Persist the offsite block into the backup's manifest + one service
    event. Recording must never raise either — it runs after the copy, in
    the success path of verify().
    """
    from app.services import pg_backup

    if manifest is not None:
        manifest["offsite"] = result
    try:
        path = os.path.join(pg_backup.backup_dir(backup_id), "manifest.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            stored["offsite"] = result
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(stored, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.error("[backup_offsite] could not record result: %s",
                     type(e).__name__)

    if result["status"] == "copied":
        applog.service(
            "backup.offsite.copied", "پشتیبان روی مقصد خارج از سرور کپی شد",
            actor=actor, target=backup_id, outcome="ok",
            duration_ms=result["duration_ms"],
            metadata={"offsite_target": result["target"],
                      "sha256": result["source_sha256"]})
    else:
        applog.service(
            "backup.offsite.failed",
            "کپی پشتیبان روی مقصد خارج از سرور ناموفق بود (پشتیبان محلی سالم است)",
            level="error", actor=actor, target=backup_id, outcome="failed",
            duration_ms=result["duration_ms"],
            metadata={"offsite_target": result["target"],
                      "exit_code": result["exit_code"],
                      "error": result["error"]})
