"""The gpg PUBLIC key that encrypts every off-site copy, set in the admin
panel (SPEC-H3).

Until now the key came only from env (OFFSITE_GPG_PUBLIC_KEY + a fingerprint),
so "encryption ready" needed a shell on the server. H3 lets the admin paste or
upload the PUBLIC key on Infrastructure > Backups. This file pins the server
side, with no network and no Docker (tests/test_offsite_gpg_panel_live.py does
the real SFTP server):

  * one key check (backup_offsite._inspect_key) behind the panel save, the
    env key, the nightly copy and the ready state: exactly one PUBLIC key,
    not revoked, not expired, able to encrypt;
  * save: a pasted armored key and a binary .gpg export are both stored as
    gpg's own armored public export; a REAL encrypt with it decrypts with the
    matching throwaway private key;
  * refuse: a private key (alone, or next to a public block), two keys,
    garbage, empty, over 64 KB, revoked, expired, sign-only. Each has its own
    plain Persian message, nothing is stored, and a refused private key shows
    up in no response, exception, log line or audit row;
  * precedence: panel, then env, then none; clear falls back to env;
  * the door: admin session for read and write, CSRF on write;
  * every save, refusal and clear is audited (who, fingerprint, never the key).

Every key is a throwaway made in a temp GNUPGHOME and removed afterwards.
Skipped cleanly when gpg is not installed.
"""
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from tests import offsite_keys
# Fixtures and helpers shared with the H1 and H2 tests. Imported by name so
# pytest finds the fixtures here too.
from tests.test_backup_offsite import (  # noqa: F401
    BACKUP_ID, FakeSftp, _manifest, _ours, _own_settings_db, _uploaded, backup_dir,
    events, install_db, remote, sftp_setup, vault)
from tests.test_offsite_destination import (  # noqa: F401
    FPR, _audit, _log_text, _signed_in, _valid, anon, app_db, client)

pytestmark = pytest.mark.skipif(not offsite_keys.gpg_available(),
                                reason="gpg is not installed")

API = "/admin/api/infra/backups/offsite-gpg"
SETTINGS_API = "/admin/api/infra/backups/offsite-settings"
CONTRACT_KEYS = {"fingerprint", "uid", "created", "source", "ready"}
ROWS = ("offsite_gpg_public_key", "offsite_gpg_fingerprint")


# ── Throwaway keys ──────────────────────────────────────────────────────

def _gpg(home, *args, faked_time=None):
    cmd = ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", ""]
    if faked_time:
        cmd += ["--faked-system-time", faked_time]
    return subprocess.run([*cmd, *args], env={**os.environ, "GNUPGHOME": home},
                          capture_output=True, timeout=120)


def _fingerprint(home):
    listing = _gpg(home, "--with-colons", "--list-keys").stdout.decode()
    return next(line.split(":")[9] for line in listing.splitlines()
                if line.startswith("fpr:"))


class Keys:
    """Every key shape the tests need. `.pub` is armored public text, `.bin`
    the binary export, `.fpr` the fingerprint."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.homes = []
        self.good = offsite_keys.make_vault_key(tmp_path, "padyar-backup-good")
        self.second = offsite_keys.make_vault_key(tmp_path, "padyar-backup-second")
        self.homes += [self.good.home, self.second.home]
        self.good_pub = _read(self.good.public_file)
        self.good_secret = _read(self.good.secret_file)
        self.second_pub = _read(self.second.public_file)
        self.good_bin = self._export(self.good.home, self.good.fingerprint, armor=False)
        self.sign_only_pub, self.sign_only_fpr = self._sign_only()
        self.expired_pub, self.expired_fpr = self._expired()
        self.old_sub_pub, self.old_sub_fpr = self._expired_encryption_subkey()
        self.revoked_pub, self.revoked_fpr = self._revoked()

    def _home(self):
        home = tempfile.mkdtemp(prefix="gpgt")
        os.chmod(home, 0o700)
        self.homes.append(home)
        return home

    @staticmethod
    def _export(home, fpr, armor=True):
        args = ("--armor",) if armor else ()
        out = _gpg(home, *args, "--export", fpr).stdout
        return out.decode() if armor else out

    def _sign_only(self):
        """A primary key that can sign and certify but has no way to encrypt."""
        home = self._home()
        _gpg(home, "--quick-gen-key", "sign-only", "ed25519", "sign", "never")
        fpr = _fingerprint(home)
        return self._export(home, fpr), fpr

    def _expired(self):
        """Made with a faked clock in 2024 and an expiry a month later, so it
        is already expired now (primary and subkey both)."""
        home = self._home()
        _gpg(home, "--quick-gen-key", "long-expired", "ed25519", "cert", "20240201T000000",
             faked_time="20240101T000000")
        fpr = _fingerprint(home)
        _gpg(home, "--quick-add-key", fpr, "cv25519", "encr", "20240201T000000",
             faked_time="20240101T000000")
        return self._export(home, fpr), fpr

    def _expired_encryption_subkey(self):
        """A primary key that never expires, whose ONLY encryption subkey has
        expired: the key is valid, and still cannot encrypt."""
        home = self._home()
        _gpg(home, "--quick-gen-key", "old-subkey", "ed25519", "cert", "never",
             faked_time="20240101T000000")
        fpr = _fingerprint(home)
        _gpg(home, "--quick-add-key", fpr, "cv25519", "encr", "20240201T000000",
             faked_time="20240101T000000")
        return self._export(home, fpr), fpr

    def _revoked(self):
        """A key plus the revocation certificate gpg wrote at generation."""
        home = self._home()
        _gpg(home, "--quick-gen-key", "revoked-one", "ed25519", "cert", "never")
        fpr = _fingerprint(home)
        _gpg(home, "--quick-add-key", fpr, "cv25519", "encr", "never")
        cert = _read(os.path.join(home, "openpgp-revocs.d", f"{fpr}.rev"))
        revoke = os.path.join(home, "revoke.asc")
        with open(revoke, "w") as f:
            f.write(cert.replace(":-----BEGIN", "-----BEGIN"))
        done = _gpg(home, "--import", revoke)
        assert done.returncode == 0, done.stderr
        return self._export(home, fpr), fpr

    def destroy(self):
        for home in self.homes:
            subprocess.run(["gpgconf", "--kill", "all"], capture_output=True,
                           env={**os.environ, "GNUPGHOME": home})
            shutil.rmtree(home, ignore_errors=True)


def _read(path):
    with open(path) as f:
        return f.read()


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    made = Keys(tmp_path_factory.mktemp("gpg-panel-keys"))
    yield made
    made.destroy()


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    """A developer's own OFFSITE_GPG_* must not change any result here."""
    import app.config as config
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "")
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", "")


def _body_marker(armored):
    """One line from inside an armored block: enough to find the key text in
    any output, and not part of the BEGIN/END or checksum lines."""
    lines = [ln for ln in armored.splitlines()
             if ln and not ln.startswith(("-----", "=", "Version", "Comment"))]
    return lines[len(lines) // 2]


def _stored():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'offsite_gpg_%'"
                            ).fetchall()
    finally:
        conn.close()
    return {r["key"]: r["value"] for r in rows}


def _grouped(fpr):
    return " ".join(fpr[i:i + 4] for i in range(0, 40, 4))


def _today():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _persian(text):
    return any("؀" <= c <= "ۿ" for c in text or "")


def _inspect(keys_text, tmp_path):
    """backup_offsite._inspect_key on a key given as text."""
    from app.services import backup_offsite
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        path = os.path.join(home, "k.asc")
        mode = "wb" if isinstance(keys_text, bytes) else "w"
        with open(path, mode) as f:
            f.write(keys_text)
        return backup_offsite._inspect_key(home, path)
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ── The one key check ───────────────────────────────────────────────────

def test_a_valid_public_key_gives_its_fingerprint_user_id_creation_date_and_encryption(
        keys, tmp_path):
    before = _today()

    info = _inspect(keys.good_pub, tmp_path)

    assert info.fingerprint == keys.good.fingerprint
    assert re.fullmatch(r"[0-9A-F]{40}", info.fingerprint)
    assert info.uid == "padyar-backup-good"
    assert info.created in {before, _today()}
    assert info.can_encrypt is True


def test_a_binary_gpg_export_is_inspected_too(keys, tmp_path):
    assert _inspect(keys.good_bin, tmp_path).fingerprint == keys.good.fingerprint


def test_the_user_id_reads_gpgs_escapes_back(tmp_path):
    from app.services import backup_offsite
    assert backup_offsite._uid_text(r"Ann \x3a Bob <a@example.com>") == "Ann : Bob <a@example.com>"
    assert backup_offsite._uid_text(r"back\x5cslash") == "back\\slash"


@pytest.mark.parametrize("which,code", [
    ("garbage", "unreadable"),
    ("good_secret", "private"),
    ("public_and_private", "private"),
    ("two_keys", "not_one"),
    ("revoked", "revoked"),
    ("expired", "expired"),
    ("sign_only", "cannot_encrypt"),
    ("old_subkey", "cannot_encrypt"),
])
def test_the_key_check_refuses_each_bad_key_with_its_own_code(keys, tmp_path, which, code):
    from app.services import backup_offsite
    texts = {
        "garbage": "this is not a key\n",
        "good_secret": keys.good_secret,
        "public_and_private": keys.second_pub + "\n" + keys.good_secret,
        "two_keys": keys.good_pub + "\n" + keys.second_pub,
        "revoked": keys.revoked_pub,
        "expired": keys.expired_pub,
        "sign_only": keys.sign_only_pub,
        "old_subkey": keys.old_sub_pub,
    }

    with pytest.raises(backup_offsite.KeyProblem) as refused:
        _inspect(texts[which], tmp_path)

    assert refused.value.code == code
    assert isinstance(refused.value, ValueError)


def test_a_key_problem_message_never_holds_the_key(keys, tmp_path):
    from app.services import backup_offsite
    with pytest.raises(backup_offsite.KeyProblem) as refused:
        _inspect(keys.good_secret, tmp_path)

    assert _body_marker(keys.good_secret) not in str(refused.value)
    assert "PRIVATE" in str(refused.value)


def test_check_key_still_passes_the_right_fingerprint_and_refuses_another(keys, tmp_path):
    from app.services import backup_offsite
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        backup_offsite._check_key(home, keys.good.public_file, keys.good.fingerprint)
        with pytest.raises(ValueError, match="(?i)fingerprint mismatch"):
            backup_offsite._check_key(home, keys.good.public_file, keys.second.fingerprint)
    finally:
        shutil.rmtree(home, ignore_errors=True)


@pytest.mark.parametrize("which", ["expired_pub", "sign_only_pub", "old_sub_pub", "revoked_pub"])
def test_the_env_key_gets_the_same_expired_and_cannot_encrypt_check(keys, tmp_path, which):
    """One check for both sources (SPEC-H3 AC3): an env key that is expired,
    revoked or unable to encrypt is now refused too, not first found when the
    nightly copy fails."""
    from app.services import backup_offsite
    path = tmp_path / "env.asc"
    path.write_text(getattr(keys, which))
    fpr = {"expired_pub": keys.expired_fpr, "sign_only_pub": keys.sign_only_fpr,
           "old_sub_pub": keys.old_sub_fpr, "revoked_pub": keys.revoked_fpr}[which]
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        with pytest.raises(ValueError):
            backup_offsite._check_key(home, str(path), fpr)
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ── Saving a key ────────────────────────────────────────────────────────

def test_a_pasted_armored_key_is_stored_as_an_armored_public_key_with_its_fingerprint(
        app_db, keys):
    from app.services import offsite_destination as od

    view = od.save_gpg_key(keys.good_pub)

    rows = _stored()
    assert set(rows) == set(ROWS)
    assert rows["offsite_gpg_fingerprint"] == keys.good.fingerprint
    assert rows["offsite_gpg_public_key"].startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    assert "PRIVATE" not in rows["offsite_gpg_public_key"]
    assert view["source"] == "panel"
    assert view["fingerprint"] == _grouped(keys.good.fingerprint)
    assert view["uid"] == "padyar-backup-good"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", view["created"])
    assert view["ready"] is True
    assert set(view) == CONTRACT_KEYS


def test_an_uploaded_binary_gpg_export_is_stored_as_armored_text(app_db, keys):
    from app.services import offsite_destination as od

    view = od.save_gpg_key(keys.good_bin)

    stored = _stored()["offsite_gpg_public_key"]
    assert stored.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    assert _stored()["offsite_gpg_fingerprint"] == keys.good.fingerprint
    assert view["ready"] is True


def test_text_pasted_around_the_key_is_not_stored(app_db, keys):
    from app.services import offsite_destination as od

    od.save_gpg_key("My backup key, made today:\n\n" + keys.good_pub + "\nThanks!\n")

    stored = _stored()["offsite_gpg_public_key"]
    assert "My backup key" not in stored and "Thanks" not in stored
    assert stored.count("BEGIN PGP PUBLIC KEY BLOCK") == 1


def test_saving_a_second_key_replaces_the_first(app_db, keys):
    from app.services import offsite_destination as od
    od.save_gpg_key(keys.good_pub)

    view = od.save_gpg_key(keys.second_pub)

    assert _stored()["offsite_gpg_fingerprint"] == keys.second.fingerprint
    assert view["fingerprint"] == _grouped(keys.second.fingerprint)


def test_a_key_saved_in_the_panel_really_encrypts_and_the_paper_key_decrypts(
        app_db, keys, tmp_path):
    """The whole point: not "the text was stored" but "a copy made with it is
    readable only with the matching private key"."""
    from app.services import backup_offsite, offsite_destination as od
    od.save_gpg_key(keys.good_pub)
    dump = tmp_path / "padyar.dump"
    dump.write_bytes(b"PGDMP" + os.urandom(2048) + b"09121234567")
    out = tmp_path / "padyar.dump.gpg"

    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        with backup_offsite._gpg_key(home) as (key_file, fingerprint, source):
            assert source == "panel" and fingerprint == keys.good.fingerprint
            backup_offsite._encrypt(str(dump), str(out), key_file, fingerprint)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    assert b"09121234567" not in out.read_bytes()
    restored = tmp_path / "restored.dump"
    assert keys.good.decrypt(str(out), str(restored)).returncode == 0
    assert restored.read_bytes() == dump.read_bytes()
    assert keys.second.decrypt(str(out), str(tmp_path / "nope")).returncode != 0


def test_saving_never_touches_the_users_own_keyring(app_db, keys, tmp_path, monkeypatch):
    from app.services import offsite_destination as od
    home = tmp_path / "user-gnupg"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))

    od.save_gpg_key(keys.good_pub)
    od.gpg_key_view()
    with pytest.raises(od.GpgKeyRefused):
        od.save_gpg_key(keys.good_secret)

    assert list(home.iterdir()) == []


def test_the_temporary_gnupg_homes_are_removed(app_db, keys, monkeypatch):
    from app.services import offsite_destination as od
    # A SHORT directory: gpg-agent's socket path has a length limit on macOS.
    tmp_path = __import__("pathlib").Path(tempfile.mkdtemp(prefix="gpgtd"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    od.save_gpg_key(keys.good_pub)
    od.gpg_key_view()
    with pytest.raises(od.GpgKeyRefused):
        od.save_gpg_key("garbage")

    left = [p.name for p in tmp_path.iterdir()]
    shutil.rmtree(tmp_path, ignore_errors=True)
    assert left == []


# ── Refusing a key ──────────────────────────────────────────────────────

def _refusals(keys):
    return {
        "private": keys.good_secret,
        "public_and_private": keys.second_pub + "\n" + keys.good_secret,
        "two_keys": keys.good_pub + "\n" + keys.second_pub,
        "garbage": "not a key at all\n",
        "empty": "",
        "blank": "  \n\n  ",
        "too_big": b"A" * (64 * 1024 + 1),
        "revoked": keys.revoked_pub,
        "expired": keys.expired_pub,
        "sign_only": keys.sign_only_pub,
        "old_subkey": keys.old_sub_pub,
    }


EXPECTED_CODE = {
    "private": "private", "public_and_private": "private", "two_keys": "not_one",
    "garbage": "unreadable", "empty": "empty", "blank": "empty", "too_big": "too_big",
    "revoked": "revoked", "expired": "expired", "sign_only": "cannot_encrypt",
    "old_subkey": "cannot_encrypt",
}


@pytest.mark.parametrize("which", sorted(EXPECTED_CODE))
def test_a_bad_key_is_refused_with_its_own_plain_message_and_nothing_changes(
        app_db, keys, which):
    from app.services import offsite_destination as od
    od.save_gpg_key(keys.second_pub)
    before = _stored()

    with pytest.raises(od.GpgKeyRefused) as refused:
        od.save_gpg_key(_refusals(keys)[which])

    assert refused.value.code == EXPECTED_CODE[which]
    assert _persian(refused.value.message_fa)
    assert refused.value.message_fa == od.MESSAGES["gpg_" + EXPECTED_CODE[which]]
    assert _stored() == before, "a refusal must leave the saved key as it was"


def test_every_refusal_has_a_different_message():
    from app.services import offsite_destination as od
    codes = {"empty", "too_big", "unreadable", "private", "not_one", "revoked", "expired",
             "cannot_encrypt"}

    texts = {c: od.MESSAGES["gpg_" + c] for c in codes}

    assert len(set(texts.values())) == len(codes)
    assert all(_persian(t) for t in texts.values())


def test_the_private_key_message_says_only_the_public_key_belongs_here():
    from app.services import offsite_destination as od
    text = od.MESSAGES["gpg_private"]
    assert "خصوصی" in text and "عمومی" in text and "کاغذ" in text
    assert "ذخیره نشد" in text


def test_a_first_save_that_is_refused_stores_nothing_at_all(app_db, keys):
    from app.services import offsite_destination as od
    with pytest.raises(od.GpgKeyRefused):
        od.save_gpg_key(keys.good_secret)

    assert _stored() == {}


@pytest.mark.parametrize("which", ["private", "public_and_private"])
def test_a_refused_private_key_appears_in_no_exception_log_line_or_audit_row(
        app_db, keys, caplog, which):
    from app.services import offsite_destination as od
    marker = _body_marker(keys.good_secret)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(od.GpgKeyRefused) as refused:
        od.save_gpg_key(_refusals(keys)[which])

    assert marker not in str(refused.value) and marker not in repr(refused.value)
    assert marker not in refused.value.message_fa
    assert marker not in "\n".join(r.getMessage() for r in caplog.records)
    assert marker not in _log_text(app_db)
    assert marker not in json.dumps(_stored())


def test_an_oversized_key_is_refused_before_gpg_runs(app_db, monkeypatch):
    from app.services import backup_offsite, offsite_destination as od
    ran = []
    monkeypatch.setattr(backup_offsite, "_gpg", lambda *a, **k: ran.append(a))

    with pytest.raises(od.GpgKeyRefused) as refused:
        od.save_gpg_key(b"A" * (64 * 1024 + 1))

    assert refused.value.code == "too_big" and ran == []


def test_a_key_of_exactly_64_kb_is_not_refused_for_its_size(app_db):
    from app.services import offsite_destination as od
    with pytest.raises(od.GpgKeyRefused) as refused:
        od.save_gpg_key(b"A" * (64 * 1024))

    assert refused.value.code == "unreadable"


# ── What the page sees ──────────────────────────────────────────────────

def test_with_nothing_set_the_view_says_none_and_not_ready(app_db):
    from app.services import offsite_destination as od

    assert od.gpg_key_view() == {"fingerprint": None, "uid": None, "created": None,
                                 "source": "none", "ready": False}


def test_the_view_never_holds_the_key_text(app_db, keys):
    from app.services import offsite_destination as od
    od.save_gpg_key(keys.good_pub)

    text = json.dumps(od.gpg_key_view())

    assert "BEGIN PGP" not in text and _body_marker(keys.good_pub) not in text


def test_clearing_removes_both_rows(app_db, keys):
    from app.services import offsite_destination as od
    od.save_gpg_key(keys.good_pub)

    view = od.clear_gpg_key()

    assert _stored() == {}
    assert view["source"] == "none" and view["ready"] is False


# ── Which key wins ──────────────────────────────────────────────────────

def _use_env_key(monkeypatch, key):
    import app.config as config
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", key.public_file)
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", key.fingerprint)


def test_the_env_key_is_used_when_no_panel_key_is_saved(app_db, keys, monkeypatch):
    from app.services import backup_offsite, offsite_destination as od
    _use_env_key(monkeypatch, keys.good)

    view = od.gpg_key_view()

    assert view == {"fingerprint": _grouped(keys.good.fingerprint),
                    "uid": "padyar-backup-good", "created": view["created"],
                    "source": "env", "ready": True}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", view["created"])
    assert backup_offsite.encryption_problem() == ""


def test_the_panel_key_beats_the_env_key(app_db, keys, monkeypatch):
    from app.services import backup_offsite, offsite_destination as od
    _use_env_key(monkeypatch, keys.good)

    view = od.save_gpg_key(keys.second_pub)

    assert view["source"] == "panel"
    assert view["fingerprint"] == _grouped(keys.second.fingerprint)
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        with backup_offsite._gpg_key(home) as (_, fingerprint, source):
            assert (fingerprint, source) == (keys.second.fingerprint, "panel")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_clearing_the_panel_key_falls_back_to_the_env_key(app_db, keys, monkeypatch):
    from app.services import offsite_destination as od
    _use_env_key(monkeypatch, keys.good)
    od.save_gpg_key(keys.second_pub)

    view = od.clear_gpg_key()

    assert view["source"] == "env"
    assert view["fingerprint"] == _grouped(keys.good.fingerprint)
    assert view["ready"] is True


def test_a_broken_env_key_is_reported_not_ready_with_empty_fields(app_db, keys, monkeypatch,
                                                                    tmp_path):
    import app.config as config
    from app.services import offsite_destination as od
    bad = tmp_path / "broken.asc"
    bad.write_text("not a key")
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", str(bad))
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", keys.good.fingerprint)

    assert od.gpg_key_view() == {"fingerprint": None, "uid": None, "created": None,
                                 "source": "env", "ready": False}


def test_an_env_key_whose_fingerprint_names_another_key_is_not_ready(app_db, keys,
                                                                     monkeypatch):
    import app.config as config
    from app.services import offsite_destination as od
    _use_env_key(monkeypatch, keys.good)
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", keys.second.fingerprint)

    view = od.gpg_key_view()

    assert view["source"] == "env" and view["ready"] is False
    assert view["fingerprint"] == _grouped(keys.good.fingerprint), \
        "the page shows the key that is really in the file"


def test_only_one_of_the_two_env_settings_is_no_env_key(app_db, keys, monkeypatch):
    import app.config as config
    from app.services import backup_offsite, offsite_destination as od
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", keys.good.public_file)

    assert od.gpg_key_view()["source"] == "none"
    problem = backup_offsite.encryption_problem()
    assert "OFFSITE_GPG_PUBLIC_KEY" in problem and "OFFSITE_GPG_FINGERPRINT" in problem


def test_with_no_key_anywhere_the_problem_names_the_panel_and_the_env_pair(app_db):
    from app.services import backup_offsite
    problem = backup_offsite.encryption_problem()

    assert "panel" in problem and "OFFSITE_GPG_PUBLIC_KEY" in problem
    assert problem.endswith("nothing uploaded")


def test_an_unreadable_settings_table_falls_back_to_env_and_never_raises(
        app_db, keys, monkeypatch, caplog):
    from app.db import queries
    from app.services import backup_offsite, offsite_destination as od
    od.save_gpg_key(keys.second_pub)
    _use_env_key(monkeypatch, keys.good)

    def broken(_keys):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(queries, "read_settings_strict", broken)
    caplog.set_level(logging.ERROR)

    assert backup_offsite.encryption_problem() == ""
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        with backup_offsite._gpg_key(home) as (_, fingerprint, source):
            assert (fingerprint, source) == (keys.good.fingerprint, "env")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    assert "settings unreadable" in caplog.text


def test_a_panel_key_row_without_its_fingerprint_row_is_no_panel_key(app_db, keys):
    from app.db.queries import set_setting
    from app.services import offsite_destination as od
    set_setting("offsite_gpg_public_key", keys.good_pub)

    assert od.gpg_key_view()["source"] == "none"


# ── Ready state and the nightly copy follow the panel key ───────────────

def test_a_panel_key_makes_a_panel_destination_ready_without_any_env_key(
        app_db, keys, monkeypatch):
    from app.services import backup_offsite, offsite_destination as od
    od.save(_valid())
    assert backup_offsite.last_result_view([])["state"] == "not_ready"

    od.save_gpg_key(keys.good_pub)

    assert backup_offsite.encryption_problem() == ""
    view = backup_offsite.last_result_view([])
    assert view["configured"] is True and view["state"] != "not_ready"


def test_test_connection_reports_ok_once_a_panel_key_is_saved(app_db, keys, monkeypatch):
    from app.services import offsite_destination as od
    od.save(_valid())
    monkeypatch.setattr(od, "_connect_and_write", lambda *a, **k: "ok")
    before = od.try_connection()
    assert before["ok"] is False and before["reason"] == "encryption_not_ready"

    od.save_gpg_key(keys.good_pub)
    after = od.try_connection()

    assert after["ok"] is True and after["reason"] == "ok"
    assert "encryption" not in after


def test_an_env_only_install_behaves_exactly_as_before(app_db, keys, monkeypatch):
    from app.services import backup_offsite
    assert "OFFSITE_GPG_PUBLIC_KEY" in backup_offsite.encryption_problem()
    _use_env_key(monkeypatch, keys.good)

    assert backup_offsite.encryption_problem() == ""


def test_the_nightly_copy_encrypts_to_the_panel_key_when_env_has_another(
        backup_dir, events, sftp_setup, remote, vault, monkeypatch):
    """sftp_setup configures the env key (vault key one). The panel key (key
    two) must win: the uploaded copy opens with key two and not with key one."""
    from app.services import backup_offsite, offsite_destination as od
    env_key, panel_key = vault
    panel_text = _read(panel_key.public_file)
    od.save_gpg_key(panel_text)

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    uploaded = remote / "upload" / _ours(BACKUP_ID, ".dump.gpg")
    plain = remote / "plain"
    assert panel_key.decrypt(str(uploaded), str(plain)).returncode == 0
    assert plain.read_bytes() == (backup_dir / "padyar.dump").read_bytes()
    assert env_key.decrypt(str(uploaded), str(remote / "nope")).returncode != 0


def test_the_nightly_copy_needs_no_env_key_when_the_panel_has_one(
        backup_dir, events, sftp_setup, remote, vault, monkeypatch):
    import app.config as config
    from app.services import backup_offsite, offsite_destination as od
    key, _ = vault
    monkeypatch.setattr(config, "OFFSITE_GPG_PUBLIC_KEY", "")
    monkeypatch.setattr(config, "OFFSITE_GPG_FINGERPRINT", "")
    od.save_gpg_key(_read(key.public_file))

    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "copied", result["error"]
    assert _uploaded(remote) == [_ours(BACKUP_ID, ".dump.gpg")]


def test_a_stored_panel_key_that_has_since_expired_stops_the_copy_before_upload(
        backup_dir, events, sftp_setup, remote, keys, monkeypatch):
    """The stored text was valid when saved. The copy checks it again, so a
    key that expires later fails the copy visibly, uploading nothing."""
    from app.db.queries import set_setting
    set_setting("offsite_gpg_public_key", keys.expired_pub)
    set_setting("offsite_gpg_fingerprint", keys.expired_fpr)

    from app.services import backup_offsite
    result = backup_offsite.copy_verified_dump(BACKUP_ID, _manifest(backup_dir))

    assert result["status"] == "failed" and "expired" in result["error"]
    assert sftp_setup.calls == [] and _uploaded(remote) == []


# ── The door ────────────────────────────────────────────────────────────

def test_the_read_and_the_write_refuse_an_anonymous_caller(anon, keys):
    assert anon.get(API).status_code == 401
    assert anon.post(API, json={"public_key": keys.good_pub}).status_code == 401
    assert anon.post(API, json={"clear": True}).status_code == 401
    assert anon.post(API, files={"file": ("k.asc", keys.good_pub.encode())}).status_code == 401
    assert _stored() == {}


def test_a_save_without_the_csrf_token_is_refused_and_stores_nothing(anon, keys):
    _signed_in(anon, csrf=False)

    assert anon.post(API, json={"public_key": keys.good_pub}).status_code == 403
    assert anon.post(API, files={"file": ("k.asc", keys.good_pub.encode())}).status_code == 403
    assert _stored() == {}


def test_a_clear_without_the_csrf_token_is_refused_and_keeps_the_key(client, anon, keys):
    client.post(API, json={"public_key": keys.good_pub})
    _signed_in(anon, csrf=False)
    anon.headers.pop("X-CSRF-Token", None)

    assert anon.post(API, json={"clear": True}).status_code == 403
    assert set(_stored()) == set(ROWS)


def test_with_a_session_and_the_csrf_token_the_routes_answer(client, keys):
    assert client.get(API).status_code == 200
    assert client.post(API, json={"public_key": keys.good_pub}).status_code == 200
    assert client.post(API, json={"clear": True}).status_code == 200


def test_the_literal_path_is_not_taken_by_a_backup_id_route(client, keys):
    """/admin/api/infra/backups/{backup_id}... would catch a path like this
    if it were registered first. GET and POST must reach the key routes."""
    assert set(client.get(API).json()) == CONTRACT_KEYS
    assert set(client.post(API, json={"public_key": keys.good_pub}).json()) == CONTRACT_KEYS


# ── Saving through the API ──────────────────────────────────────────────

def test_an_empty_install_reports_no_key(client):
    assert client.get(API).json() == {"fingerprint": None, "uid": None, "created": None,
                                      "source": "none", "ready": False}


def test_a_json_save_answers_with_the_fixed_shape(client, keys):
    res = client.post(API, json={"public_key": keys.good_pub})

    assert res.status_code == 200
    body = res.json()
    assert set(body) == CONTRACT_KEYS
    assert body["fingerprint"] == _grouped(keys.good.fingerprint)
    assert body["uid"] == "padyar-backup-good"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["created"])
    assert body["source"] == "panel" and body["ready"] is True
    assert client.get(API).json() == body


def test_an_uploaded_asc_file_is_saved(client, keys):
    res = client.post(API, files={"file": ("backup-key.asc", keys.good_pub.encode(),
                                           "application/pgp-keys")})

    assert res.status_code == 200, res.text
    assert res.json()["fingerprint"] == _grouped(keys.good.fingerprint)


def test_an_uploaded_binary_gpg_file_is_saved(client, keys):
    res = client.post(API, files={"file": ("backup-key.gpg", keys.good_bin,
                                           "application/octet-stream")})

    assert res.status_code == 200, res.text
    assert res.json()["source"] == "panel"
    assert _stored()["offsite_gpg_public_key"].startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")


def test_the_clear_flag_removes_the_panel_key(client, keys):
    client.post(API, json={"public_key": keys.good_pub})

    res = client.post(API, json={"clear": True})

    assert res.status_code == 200
    assert res.json()["source"] == "none"
    assert _stored() == {}


@pytest.mark.parametrize("which", sorted(EXPECTED_CODE))
def test_a_bad_key_gets_a_400_with_plain_persian_and_nothing_is_stored(client, keys, which):
    from app.services import offsite_destination as od
    text = _refusals(keys)[which]
    payload = text.decode() if isinstance(text, bytes) else text

    res = client.post(API, json={"public_key": payload})

    assert res.status_code == 400
    assert res.json() == {"detail": od.MESSAGES["gpg_" + EXPECTED_CODE[which]]}
    assert _stored() == {}
    assert _body_marker(keys.good_secret) not in res.text


def test_a_private_key_upload_is_refused_and_does_not_come_back(client, keys):
    res = client.post(API, files={"file": ("secret.asc", keys.good_secret.encode())})

    assert res.status_code == 400
    assert _body_marker(keys.good_secret) not in res.text
    assert "PRIVATE KEY BLOCK" not in res.text
    assert _stored() == {}


def test_a_huge_upload_is_refused_as_too_big(client):
    from app.services import offsite_destination as od
    res = client.post(API, files={"file": ("big.asc", b"A" * (3 * 1024 * 1024))})

    assert res.status_code == 400
    assert res.json() == {"detail": od.MESSAGES["gpg_too_big"]}


def test_a_body_with_neither_field_is_refused_as_empty(client):
    from app.services import offsite_destination as od
    for body in ({}, {"public_key": ""}, {"public_key": None}):
        res = client.post(API, json=body)
        assert res.status_code == 400
        assert res.json() == {"detail": od.MESSAGES["gpg_empty"]}


def test_a_multipart_request_without_the_file_field_is_refused_as_empty(client):
    from app.services import offsite_destination as od
    res = client.post(API, data={"other": "x"}, files={"other_file": ("a", b"b")})

    assert res.status_code == 400
    assert res.json() == {"detail": od.MESSAGES["gpg_empty"]}


@pytest.mark.parametrize("body", [{"public_key": 123}, {"public_key": ["x"]},
                                  {"clear": "yes", "public_key": None}, [1, 2]])
def test_a_body_of_the_wrong_type_is_a_plain_400(client, body):
    from app.services import offsite_destination as od
    res = client.post(API, json=body)

    assert res.status_code == 400
    assert res.json()["detail"] in (od.MESSAGES["bad_request"], od.MESSAGES["gpg_empty"])
    assert _stored() == {}


def test_a_body_that_is_not_json_is_a_plain_400(client):
    res = client.post(API, content=b"{broken", headers={"Content-Type": "application/json"})

    assert res.status_code == 400
    assert _persian(res.json()["detail"])


def test_an_unexpected_failure_is_a_500_with_a_generic_sentence(client, keys, monkeypatch):
    from app.routers import backups
    from app.services import offsite_destination as od

    def boom(_raw):
        raise RuntimeError("secret detail /srv/x")

    monkeypatch.setattr(od, "save_gpg_key", boom)
    res = client.post(API, json={"public_key": keys.good_pub})

    assert res.status_code == 500
    assert res.json() == {"detail": backups.FA_GENERIC}
    assert "secret detail" not in res.text


def test_a_failed_check_with_gpg_missing_is_a_500_not_a_stored_key(client, keys, monkeypatch):
    from app.services import backup_offsite

    def no_gpg(*a, **k):
        raise FileNotFoundError("gpg")

    monkeypatch.setattr(backup_offsite, "_gpg", no_gpg)
    res = client.post(API, json={"public_key": keys.good_pub})

    assert res.status_code == 500
    assert _stored() == {}


# ── What the browser and the logs never get ─────────────────────────────

def test_a_read_never_returns_the_key_text(client, keys):
    client.post(API, json={"public_key": keys.good_pub})

    text = client.get(API).text

    assert "BEGIN PGP" not in text
    assert _body_marker(keys.good_pub) not in text
    assert keys.good.fingerprint not in text, "the fingerprint is shown grouped in 4s"


def test_the_save_response_never_returns_the_key_text(client, keys):
    text = client.post(API, json={"public_key": keys.good_pub}).text

    assert "BEGIN PGP" not in text and _body_marker(keys.good_pub) not in text


def test_the_page_data_does_not_hold_the_key(client, keys):
    client.post(API, json={"public_key": keys.good_pub})

    text = client.get("/admin/api/infra/backups").text

    assert _body_marker(keys.good_pub) not in text


# ── Audit ───────────────────────────────────────────────────────────────

def test_a_save_is_audited_with_who_and_the_fingerprint_and_never_the_key(
        client, keys, app_db):
    client.post(API, json={"public_key": keys.good_pub})

    rows = _audit("admin.backup.offsite_gpg.saved")

    assert len(rows) == 1
    assert rows[0]["actor"] == "panel" and rows[0]["outcome"] == "ok"
    meta = json.loads(rows[0]["metadata"])
    assert meta["fingerprint"] == keys.good.fingerprint
    assert "padyar-backup-good" in meta["uid"]
    logs = _log_text(app_db)
    assert _body_marker(keys.good_pub) not in logs and "BEGIN PGP" not in logs


def test_a_refusal_is_audited_with_its_code_and_without_the_key(client, keys, app_db):
    client.post(API, json={"public_key": keys.good_secret})

    rows = _audit("admin.backup.offsite_gpg.saved")

    assert len(rows) == 1 and rows[0]["outcome"] == "refused"
    assert json.loads(rows[0]["metadata"]) == {"code": "private"}
    logs = _log_text(app_db)
    assert _body_marker(keys.good_secret) not in logs and "PRIVATE KEY" not in logs


def test_a_clear_is_audited_with_the_old_fingerprint(client, keys):
    client.post(API, json={"public_key": keys.good_pub})

    client.post(API, json={"clear": True})

    rows = _audit("admin.backup.offsite_gpg.cleared")
    assert len(rows) == 1 and rows[0]["actor"] == "panel" and rows[0]["outcome"] == "ok"
    assert json.loads(rows[0]["metadata"])["fingerprint"] == keys.good.fingerprint


def test_an_upload_save_is_audited_like_a_paste(client, keys):
    client.post(API, files={"file": ("k.gpg", keys.good_bin)})

    rows = _audit("admin.backup.offsite_gpg.saved")
    assert len(rows) == 1
    assert json.loads(rows[0]["metadata"])["fingerprint"] == keys.good.fingerprint


# ── Whitespace around pasted text ───────────────────────────────────────

def test_a_pasted_key_with_spaces_and_blank_lines_around_it_is_accepted(app_db, keys):
    from app.services import offsite_destination as od
    padded = "  \n\t\n   " + keys.good_pub + "   \n\n  "

    view = od.save_gpg_key(padded)

    assert view["fingerprint"] == _grouped(keys.good.fingerprint)
    assert _stored()["offsite_gpg_fingerprint"] == keys.good.fingerprint


def test_a_padded_paste_through_the_api_is_accepted(client, keys):
    res = client.post(API, json={"public_key": "   " + keys.good_pub + "  \n  "})

    assert res.status_code == 200, res.text
    assert res.json()["fingerprint"] == _grouped(keys.good.fingerprint)


@pytest.mark.parametrize("blank", ["   ", "\n\t \r\n", " " * 5000])
def test_pasted_whitespace_alone_is_still_empty(app_db, blank):
    from app.services import offsite_destination as od
    with pytest.raises(od.GpgKeyRefused) as refused:
        od.save_gpg_key(blank)

    assert refused.value.code == "empty"


def test_the_size_limit_looks_at_the_raw_paste_not_the_trimmed_one(app_db, keys):
    """A key plus 64 KB of spaces is over the limit as it arrived. Trimming
    first would let it through, and the cap would no longer bound the input."""
    from app.services import offsite_destination as od
    with pytest.raises(od.GpgKeyRefused) as refused:
        od.save_gpg_key(" " * (64 * 1024) + keys.good_pub)

    assert refused.value.code == "too_big"
    assert _stored() == {}


def test_an_uploaded_file_is_not_trimmed(app_db, keys, monkeypatch):
    """A file may be a binary .gpg export, where a first or last byte that
    looks like whitespace is data. Only pasted text is trimmed."""
    from app.services import backup_offsite, offsite_destination as od
    inputs = []
    real = backup_offsite._gpg

    def spy(home, *args, **kw):
        if kw.get("input") is not None:
            inputs.append(kw["input"])
        return real(home, *args, **kw)

    monkeypatch.setattr(backup_offsite, "_gpg", spy)
    raw = b"\n\n" + keys.good_bin + b"  \n"

    try:
        od.save_gpg_key(raw)
    except od.GpgKeyRefused:
        pass  # whether gpg takes the padding is not the point

    assert inputs and inputs[0] == raw


def test_an_uploaded_binary_gpg_file_is_still_accepted(app_db, keys):
    from app.services import offsite_destination as od
    assert od.save_gpg_key(keys.good_bin)["fingerprint"] == _grouped(keys.good.fingerprint)


# ── The pasted or uploaded bytes never touch the disk ───────────────────

class _Watched:
    """An empty directory that stands in for the system temp directory, and a
    scan of every file under it for the bytes of one key."""

    def __init__(self, path):
        self.path = path
        self.hits = []
        self.paused = False

    def files(self):
        for root, _dirs, names in os.walk(self.path):
            for name in names:
                full = os.path.join(root, name)
                if os.path.isfile(full) and not os.path.islink(full):
                    yield full

    def scan(self, needles, when):
        if self.paused:
            return
        for full in self.files():
            try:
                with open(full, "rb") as f:
                    blob = f.read()
            except OSError:
                continue
            for label, needle in needles.items():
                if needle in blob:
                    self.hits.append(f"{when}: {label} found in {os.path.basename(full)}")


@pytest.fixture
def watched(monkeypatch):
    # A SHORT path: gpg-agent's socket path has a length limit on macOS.
    path = tempfile.mkdtemp(prefix="gpgw")
    monkeypatch.setenv("TMPDIR", path)
    monkeypatch.setattr(tempfile, "tempdir", path)  # tempfile caches the directory
    yield _Watched(path)
    shutil.rmtree(path, ignore_errors=True)


def _needles(armored):
    """What to look for: a long slice of the key's base64 body, the same
    slice as raw packet bytes, and the armor header of a private key."""
    import base64
    body = [ln for ln in armored.splitlines()
            if ln and not ln.startswith(("-----", "=", "Version", "Comment"))]
    packed = base64.b64decode("".join(body))
    found = {"base64 slice": _body_marker(armored).encode(),
             "packet bytes": packed[len(packed) // 2: len(packed) // 2 + 32]}
    assert len(found["base64 slice"]) >= 40
    if "PRIVATE KEY BLOCK" in armored:
        found["private header"] = b"PRIVATE KEY BLOCK"
    return found


def _scan_at_every_gpg_call(monkeypatch, watched, needles):
    """Scan the watched directory just before and just after each gpg run.
    Returns the list of gpg argvs, so a test can tell gpg really ran."""
    from app.services import backup_offsite
    real = subprocess.run
    ran = []

    def run(argv, *a, **kw):
        is_gpg = bool(argv) and argv[0] == "gpg"
        if is_gpg:
            ran.append(list(argv))
            watched.scan(needles, "before gpg")
        done = real(argv, *a, **kw)
        if is_gpg:
            watched.scan(needles, "after gpg")
        return done

    monkeypatch.setattr(backup_offsite.subprocess, "run", run)
    return ran


def _no_scan_while_the_view_runs(monkeypatch, watched):
    """After a save the page view is built. For an ACCEPTED key that view
    writes the stored public key to a 0600 file for the moment it takes
    (backup_offsite._gpg_key, unchanged: that key is public and is already in
    the settings table). The scans of the save itself stay on; only the
    view's own gpg calls are not scanned. The final scan still covers it."""
    from app.services import offsite_destination as od
    real = od.gpg_key_view

    def view():
        watched.paused = True
        try:
            return real()
        finally:
            watched.paused = False

    monkeypatch.setattr(od, "gpg_key_view", view)


def test_a_private_key_pasted_by_mistake_is_refused_without_ever_reaching_the_disk(
        app_db, keys, watched, monkeypatch):
    from app.services import offsite_destination as od
    needles = _needles(keys.good_secret)
    ran = _scan_at_every_gpg_call(monkeypatch, watched, needles)

    with pytest.raises(od.GpgKeyRefused) as refused:
        od.save_gpg_key(keys.good_secret)

    watched.scan(needles, "after the refusal")
    assert refused.value.code == "private"
    assert refused.value.message_fa == od.MESSAGES["gpg_private"]
    assert ran, "gpg must have looked at the key"
    assert watched.hits == []
    assert _stored() == {}


@pytest.mark.parametrize("tiny_spool", [False, True], ids=["default-spool", "tiny-spool"])
def test_a_private_key_uploaded_by_mistake_never_reaches_the_disk(
        client, keys, watched, monkeypatch, tiny_spool):
    """The multipart parser keeps a small upload in memory and spills a big
    one to a temp FILE. The route must never let it spill, whatever the
    parser's own default is."""
    from starlette import formparsers
    needles = _needles(keys.good_secret)
    ran = _scan_at_every_gpg_call(monkeypatch, watched, needles)
    spools = []

    class Recording(tempfile.SpooledTemporaryFile):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            spools.append(self)

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", Recording)
    if tiny_spool:
        monkeypatch.setattr(formparsers.MultiPartParser, "spool_max_size", 64)

    res = client.post(API, files={"file": ("secret.asc", keys.good_secret.encode())})

    watched.scan(needles, "after the request")
    assert res.status_code == 400
    assert res.json() == {"detail": __import__("app.services.offsite_destination",
                          fromlist=["MESSAGES"]).MESSAGES["gpg_private"]}
    assert ran, "gpg must have looked at the key"
    assert spools, "the upload must have gone through the parser"
    assert [s for s in spools if getattr(s, "_rolled", False)] == []
    assert watched.hits == []
    assert _stored() == {}


def test_an_accepted_public_key_leaves_no_file_with_its_bytes(
        app_db, keys, watched, monkeypatch):
    from app.services import offsite_destination as od
    needles = _needles(keys.good_pub)
    ran = _scan_at_every_gpg_call(monkeypatch, watched, needles)
    _no_scan_while_the_view_runs(monkeypatch, watched)

    od.save_gpg_key(keys.good_pub)

    watched.scan(needles, "after the save")
    assert ran
    assert watched.hits == []
    assert "BEGIN PGP PUBLIC KEY BLOCK" in _stored()["offsite_gpg_public_key"]


def test_an_accepted_upload_through_the_api_leaves_no_file_with_its_bytes(
        client, keys, watched, monkeypatch):
    needles = _needles(keys.second_pub)
    ran = _scan_at_every_gpg_call(monkeypatch, watched, needles)
    _no_scan_while_the_view_runs(monkeypatch, watched)

    res = client.post(API, files={"file": ("k.gpg", keys.second_pub.encode())})

    watched.scan(needles, "after the request")
    assert res.status_code == 200, res.text
    assert ran
    assert watched.hits == []


def test_the_key_check_reads_a_key_given_as_bytes_from_stdin(keys, watched):
    """The key is handed to gpg on stdin: no key path is on the command line."""
    from app.services import backup_offsite
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    seen = []
    real = backup_offsite._gpg
    try:
        def spy(h, *args, **kw):
            seen.append((args, kw.get("input")))
            return real(h, *args, **kw)

        backup_offsite._gpg = spy
        info = backup_offsite._inspect_key(home, keys.good_pub.encode())
    finally:
        backup_offsite._gpg = real
        shutil.rmtree(home, ignore_errors=True)

    assert info.fingerprint == keys.good.fingerprint
    args, stdin = seen[0]
    assert stdin == keys.good_pub.encode()
    assert args[-1] == "--show-keys"
