"""How the panel destination connects (SPEC-H2 items 5 to 7), with no server.

offsite_destination.login() and try_connection() are pinned here with
`ssh-keyscan` and `sftp` replaced by a fake that records each call and looks
at the files it was handed WHILE the call runs (they are gone afterwards):

  * the host key is pinned: only the scanned key whose fingerprint is the
    saved pin reaches known_hosts, HostKeyAlgorithms names only its type,
    and a mismatch stops before any login;
  * no secret on argv: a key is a 0600 temp file for the call only; a
    password goes through SSH_ASKPASS, with BatchMode=no BEFORE -b, and the
    askpass helper answers a password prompt and nothing else;
  * each failure has its own reason, and a half-done test cleans up after
    itself.

Two cases use real sockets and the real ssh-keyscan (no SSH server needed):
a closed port is "unreachable", a server that never answers is "timeout".
tests/test_offsite_panel_live.py runs the same flow against a real server.
"""
import base64
import os
import shlex
import socket
import stat
import subprocess
import threading

import pytest

# The fake below replaces subprocess.run process-wide; a test that must run a
# real program (the askpass helper) uses this.
REAL_RUN = subprocess.run
PASSWORD = "Throwaway-pw-9f3a1c"
KEY = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
       "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
       "-----END OPENSSH PRIVATE KEY-----")


def _blob(key_type, payload):
    def ssh_string(b):
        return len(b).to_bytes(4, "big") + b
    return base64.b64encode(ssh_string(key_type.encode()) + ssh_string(payload)).decode()


ED = ("ssh-ed25519", _blob("ssh-ed25519", b"\x11" * 32))
RSA = ("ssh-rsa", _blob("ssh-rsa", b"\x01\x00\x01" + b"\x22" * 64))
ECDSA = ("ecdsa-sha2-nistp256", _blob("ecdsa-sha2-nistp256", b"nistp256" + b"\x33" * 65))


def _scan_output(*keys, host="[backup.example.com]:2222"):
    lines = [f"# {host} SSH-2.0-OpenSSH_10.3"]
    lines += [f"{host} {key_type} {blob}" for key_type, blob in keys]
    return "\n".join(lines) + "\n"


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "offsite-connection.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "OFFSITE_BACKUP_TARGET", "")
    from app.db.connection import init_db
    init_db()


def _save(**over):
    from app.services import offsite_destination
    body = {"host": "backup.example.com", "port": 2222, "user": "backup",
            "path": "/upload/myevent", "auth": "password", "password": PASSWORD,
            "fingerprint": over.pop("pin", ED)}
    if isinstance(body["fingerprint"], tuple):
        body["fingerprint"] = offsite_destination.fingerprint_of(body["fingerprint"][1])
    body.update(over)
    offsite_destination.save(body)


class FakeSsh:
    """Stands in for ssh-keyscan and sftp. `on_sftp(argv, kwargs)` runs inside
    the sftp call and returns (rc, stdout, stderr); by default the batch runs
    against `remote`, a local directory standing in for the server."""

    def __init__(self, remote, scan=_scan_output(ED, RSA)):
        self.remote, self.scan, self.calls, self.on_sftp = remote, scan, [], None

    def _local(self, path):
        return self.remote / path.lstrip("/")

    def run_batch(self, batch):
        out = []
        for line in batch.splitlines():
            out.append(f"sftp> {line.lstrip('-')}")
            ignore = line.startswith("-")
            words = shlex.split(line.lstrip("-"))
            try:
                if words[0] == "put":
                    self._local(words[2]).write_bytes(open(words[1], "rb").read())
                elif words[0] == "chmod":
                    os.chmod(self._local(words[2]), int(words[1], 8))
                elif words[0] == "rename":
                    self._local(words[1]).rename(self._local(words[2]))
                elif words[0] == "ls":
                    list(self._local(words[-1]).iterdir())
                elif words[0] == "rm":
                    self._local(words[1]).unlink()
            except OSError as e:
                if not ignore:
                    return 1, "\n".join(out), f"{words[0]}: {e}"
        return 0, "\n".join(out) + "\n", ""

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(argv, 0, stdout=self.scan, stderr="")
        assert argv[0] == "sftp", argv
        if self.on_sftp:
            rc, out, err = self.on_sftp(argv, kwargs)
        else:
            rc, out, err = self.run_batch(kwargs["input"])
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)

    def sftp_calls(self):
        return [c for c in self.calls if c[0][0] == "sftp"]


@pytest.fixture
def remote(tmp_path):
    root = tmp_path / "remote"
    (root / "upload" / "myevent").mkdir(parents=True)
    return root


@pytest.fixture
def fake(app_db, remote, monkeypatch):
    from app.services import offsite_destination
    f = FakeSsh(remote)
    monkeypatch.setattr(offsite_destination.subprocess, "run", f)
    monkeypatch.setattr("app.services.backup_offsite.subprocess.run", f)
    return f


def _opt(argv, name):
    """The value of `-o name=value` in argv."""
    for item in argv:
        if item.startswith(f"{name}="):
            return item.split("=", 1)[1]
    return None


def _try():
    from app.services import offsite_destination
    return offsite_destination.try_connection()


# ── Nothing to connect to ───────────────────────────────────────────────

def test_with_nothing_saved_no_command_runs(fake):
    result = _try()

    assert result["ok"] is False and result["reason"] == "not_saved"
    assert fake.calls == []


@pytest.mark.parametrize("auth,reason", [("password", "no_password"), ("key", "no_key")])
def test_a_destination_without_its_secret_makes_no_connection(fake, auth, reason):
    _save(auth=auth, password=None, private_key=None)

    result = _try()

    assert result["reason"] == reason
    assert fake.calls == []


# ── The host key pin ────────────────────────────────────────────────────

def test_a_host_key_that_does_not_match_the_pin_is_refused_before_any_login(fake, remote):
    _save(pin=ECDSA)

    result = _try()

    assert result["ok"] is False and result["reason"] == "host_key"
    assert [c[0][0] for c in fake.calls] == ["ssh-keyscan"]
    assert list((remote / "upload" / "myevent").iterdir()) == []


def test_the_scan_asks_for_every_common_key_type_and_holds_no_secret(fake):
    _save()

    _try()

    argv = fake.calls[0][0]
    assert argv[0] == "ssh-keyscan"
    assert argv[argv.index("-t") + 1] == "ed25519,ecdsa,rsa"
    assert argv[argv.index("-p") + 1] == "2222"
    assert argv[-2:] == ["--", "backup.example.com"]
    assert PASSWORD not in " ".join(argv)


@pytest.mark.parametrize("pin,algorithms", [
    (ED, "ssh-ed25519"),
    (RSA, "rsa-sha2-512,rsa-sha2-256"),
    (ECDSA, "ecdsa-sha2-nistp256"),
])
def test_only_the_pinned_key_is_trusted_and_only_its_type_is_offered(fake, pin, algorithms):
    fake.scan = _scan_output(ED, RSA, ECDSA)
    _save(pin=pin)
    seen = {}

    def inspect(argv, kwargs):
        with open(_opt(argv, "UserKnownHostsFile")) as f:
            seen["known_hosts"] = f.read()
        return fake.run_batch(kwargs["input"])

    fake.on_sftp = inspect

    assert _try()["ok"] is True

    argv = fake.sftp_calls()[0][0]
    assert seen["known_hosts"] == f"[backup.example.com]:2222 {pin[0]} {pin[1]}\n"
    assert _opt(argv, "HostKeyAlgorithms") == algorithms
    assert _opt(argv, "StrictHostKeyChecking") == "yes"
    assert _opt(argv, "GlobalKnownHostsFile") == "/dev/null"
    assert argv[-2:] == ["--", "backup@backup.example.com"]
    assert argv[argv.index("-P") + 1] == "2222"


def test_on_port_22_the_known_hosts_line_names_the_bare_host(fake):
    """OpenSSH looks a port-22 host up without brackets; `[host]:22` would
    never match and every connection would fail as a host-key error."""
    fake.scan = _scan_output(ED, host="backup.example.com")
    _save(port=22)
    seen = {}

    def inspect(argv, kwargs):
        with open(_opt(argv, "UserKnownHostsFile")) as f:
            seen["known_hosts"] = f.read()
        return fake.run_batch(kwargs["input"])

    fake.on_sftp = inspect

    assert _try()["ok"] is True
    assert seen["known_hosts"] == f"backup.example.com {ED[0]} {ED[1]}\n"


def test_a_scanned_line_whose_blob_names_another_type_is_ignored(fake):
    fake.scan = _scan_output(ED, ("ssh-ed25519", RSA[1]))
    _save(pin=RSA)

    assert _try()["reason"] == "host_key"
    assert fake.sftp_calls() == []


# ── No secret on argv ───────────────────────────────────────────────────

def test_a_password_never_reaches_argv_and_batch_mode_is_off_before_dash_b(fake):
    _save()
    seen = {}

    def inspect(argv, kwargs):
        env = kwargs["env"]
        askpass = env["SSH_ASKPASS"]
        seen["mode"] = stat.S_IMODE(os.stat(askpass).st_mode)
        seen["script"] = open(askpass).read()
        seen["answer"] = REAL_RUN([askpass, "backup@backup.example.com's password: "],
                                        env=env, capture_output=True, text=True)
        seen["other"] = REAL_RUN([askpass, "Are you sure you want to continue?"],
                                       env=env, capture_output=True, text=True)
        return fake.run_batch(kwargs["input"])

    fake.on_sftp = inspect

    result = _try()
    assert result["ok"] is True, (result, seen)

    argv, kwargs = fake.sftp_calls()[0]
    assert all(PASSWORD not in item for item in argv)
    assert argv.index("BatchMode=no") < argv.index("-b")
    assert _opt(argv, "PubkeyAuthentication") == "no"
    assert kwargs["env"]["SSH_ASKPASS_REQUIRE"] == "force"
    assert seen["mode"] == 0o700 and PASSWORD not in seen["script"]
    assert seen["answer"].stdout == PASSWORD + "\n"
    assert seen["other"].returncode != 0 and PASSWORD not in seen["other"].stdout
    assert not os.path.exists(kwargs["env"]["SSH_ASKPASS"]), "the helper outlived the call"


def test_a_private_key_is_a_0600_file_for_the_call_only(fake):
    _save(auth="key", password=None, private_key=KEY)
    seen = {}

    def inspect(argv, kwargs):
        identity = argv[argv.index("-i") + 1]
        seen["path"] = identity
        seen["mode"] = stat.S_IMODE(os.stat(identity).st_mode)
        seen["content"] = open(identity).read()
        return fake.run_batch(kwargs["input"])

    fake.on_sftp = inspect

    assert _try()["ok"] is True

    argv, kwargs = fake.sftp_calls()[0]
    assert seen["mode"] == 0o600
    assert seen["content"] == KEY + "\n", "OpenSSH refuses a key file without a final newline"
    assert _opt(argv, "BatchMode") == "yes" and argv.index("-b") > 0
    assert _opt(argv, "PasswordAuthentication") == "no"
    assert _opt(argv, "IdentitiesOnly") == "yes"
    assert not os.path.exists(seen["path"])
    assert not os.path.exists(os.path.dirname(seen["path"]))
    assert all("PRIVATE KEY" not in item for item in argv)


def test_the_temp_dir_is_removed_when_the_pin_does_not_match(fake, monkeypatch):
    import tempfile

    from app.services import offsite_destination
    made = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(offsite_destination.tempfile, "mkdtemp",
                        lambda **kw: made.append(real(**kw)) or made[-1])
    _save(pin=ECDSA)

    assert _try()["reason"] == "host_key"
    assert made and not any(os.path.exists(d) for d in made)


# ── One reason per failure ──────────────────────────────────────────────

ECHO_PUT = "sftp> put \"/tmp/probe\" /upload/myevent/.padyar-connection-test-1.part\n"
ECHO_TO_RM = (ECHO_PUT + "sftp> chmod 600 x.part\nsftp> rename x.part x\n"
              "sftp> ls -ln /upload/myevent\nsftp> rm /upload/myevent/x\n")


@pytest.mark.parametrize("auth,rc,out,err,reason", [
    ("password", 255, "", "backup@h: Permission denied (publickey,password).\r\n"
                          "Connection closed\r\n", "auth_password"),
    ("key", 255, "", "backup@h: Permission denied (publickey).\r\n", "auth_key"),
    ("password", 255, "", "Host key verification failed.\r\n", "host_key"),
    ("password", 255, "", "ssh: connect to host h port 2222: Connection refused\r\n",
     "unreachable"),
    ("password", 255, "", "ssh: connect to host h port 2222: Operation timed out\r\n",
     "timeout"),
    ("password", 255, "", "Connection closed\r\n", "unreachable"),
    ("password", 1, ECHO_PUT, 'remote open("/upload/myevent/x.part"): Permission denied',
     "not_writable"),
    ("password", 1, ECHO_PUT, 'remote open("/nope/x.part"): No such file or directory',
     "not_writable"),
    ("password", 1, ECHO_TO_RM, 'remote delete "/upload/myevent/x": Permission denied',
     "not_deletable"),
])
def test_each_failure_has_its_own_plain_reason(fake, auth, rc, out, err, reason):
    from app.services import offsite_destination
    _save(auth=auth, private_key=KEY if auth == "key" else None)
    fake.on_sftp = lambda argv, kwargs: (rc, out, err)

    result = _try()

    assert result["ok"] is False and result["reason"] == reason
    assert result["message"] == offsite_destination.MESSAGES[reason]


def test_a_timeout_is_its_own_reason(fake):
    _save()

    def hang(argv, kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    fake.on_sftp = hang

    assert _try()["reason"] == "timeout"


def test_every_call_fits_inside_the_hard_deadline(fake, monkeypatch):
    from app.services import offsite_destination
    monkeypatch.setattr(offsite_destination, "TEST_TIMEOUT", 7)
    _save()

    _try()

    assert fake.calls
    assert all(0 < kwargs["timeout"] <= 7 for _, kwargs in fake.calls)


def test_a_step_that_fails_after_the_put_cleans_up_the_test_file(fake):
    _save()
    batches = []

    def rename_refused(argv, kwargs):
        batches.append(kwargs["input"])
        if len(batches) == 1:
            return 1, ECHO_PUT + "sftp> chmod 600 x.part\nsftp> rename x.part x\n", "denied"
        return 0, "", ""

    fake.on_sftp = rename_refused

    assert _try()["reason"] == "not_writable"
    assert len(batches) == 2
    assert all(line.startswith("-rm ") for line in batches[1].splitlines())


def test_a_successful_test_writes_one_small_file_and_deletes_it(fake, remote):
    _save()
    seen = []

    def watch(argv, kwargs):
        *steps, last = kwargs["input"].splitlines()
        assert last.startswith("rm "), "the delete must be the last step"
        rc, out, err = fake.run_batch("\n".join(steps) + "\n")
        seen.extend(p.read_bytes() for p in (remote / "upload" / "myevent").iterdir())
        rc2, out2, err2 = fake.run_batch(last + "\n")
        return rc or rc2, out + out2, err + err2

    fake.on_sftp = watch

    result = _try()

    assert result == {"ok": True, "reason": "ok",
                      "message": "اتصال برقرار شد و نوشتن فایل آزمایشی موفق بود."}
    assert len(seen) == 1 and b"connection test" in seen[0] and len(seen[0]) < 100
    assert list((remote / "upload" / "myevent").iterdir()) == []


# ── Real sockets ────────────────────────────────────────────────────────

needs_keyscan = pytest.mark.skipif(
    REAL_RUN(["sh", "-c", "command -v ssh-keyscan"], capture_output=True).returncode,
    reason="ssh-keyscan is not installed")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@needs_keyscan
def test_a_closed_port_is_unreachable(app_db):
    _save(host="127.0.0.1", port=_free_port())

    assert _try()["reason"] == "unreachable"


@needs_keyscan
def test_a_server_that_never_answers_is_a_timeout(app_db, monkeypatch):
    from app.services import offsite_destination
    monkeypatch.setattr(offsite_destination, "SCAN_TIMEOUT", 2)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held = []
    stop = threading.Event()

    def accept_and_say_nothing():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(server.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=accept_and_say_nothing, daemon=True)
    thread.start()
    try:
        _save(host="127.0.0.1", port=server.getsockname()[1])
        assert _try()["reason"] == "timeout"
    finally:
        stop.set()
        thread.join(2)
        for conn in held:
            conn.close()
        server.close()
