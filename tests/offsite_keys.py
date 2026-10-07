"""Throwaway gpg key pairs for the off-site encryption tests.

Shared by tests/test_backup_offsite.py and tests/test_offsite_sftp_live.py.
Not a test file itself (no `test_` prefix), so pytest only imports it.

The "vault" side (the key pair that would live on paper) is made the way
the runbook says: a cert-only ed25519 primary key plus an encrypt-only
cv25519 subkey. Its GNUPGHOME comes from tempfile.mkdtemp(), not pytest's
tmp_path, on purpose: gpg-agent puts a unix socket in GNUPGHOME, and
pytest's long tmp_path names pass the 104-byte socket path limit on macOS
("No agent running" at key generation).
"""
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass


@dataclass
class VaultKey:
    fingerprint: str
    public_file: str   # armored PUBLIC key, what goes on the server
    secret_file: str   # armored SECRET key, what goes on paper
    home: str          # GNUPGHOME holding the secret key (the "vault")

    def decrypt(self, encrypted: str, out: str) -> subprocess.CompletedProcess:
        return _gpg(self.home, "--batch", "--yes", "--output", out,
                    "--decrypt", encrypted)


def gpg_available() -> bool:
    return shutil.which("gpg") is not None


def _gpg(home, *args):
    env = {**os.environ, "GNUPGHOME": home}
    return subprocess.run(["gpg", *args], env=env, capture_output=True,
                          text=True, timeout=120)


def make_vault_key(out_dir, name="padyar-backup-test") -> VaultKey:
    home = tempfile.mkdtemp(prefix="gpgv")
    os.chmod(home, 0o700)
    common = ("--batch", "--pinentry-mode", "loopback", "--passphrase", "")
    _gpg(home, *common, "--quick-gen-key", name, "ed25519", "cert", "never")
    listing = _gpg(home, "--batch", "--with-colons", "--list-keys").stdout
    fpr = next(line.split(":")[9] for line in listing.splitlines()
               if line.startswith("fpr:"))
    _gpg(home, *common, "--quick-add-key", fpr, "cv25519", "encr", "never")
    public_file = os.path.join(str(out_dir), f"{name}-{fpr[-8:]}.pub.asc")
    secret_file = os.path.join(str(out_dir), f"{name}-{fpr[-8:]}.sec.asc")
    with open(public_file, "w") as f:
        f.write(_gpg(home, "--batch", "--armor", "--export", fpr).stdout)
    with open(secret_file, "w") as f:
        f.write(_gpg(home, *common, "--armor", "--export-secret-keys", fpr).stdout)
    return VaultKey(fpr, public_file, secret_file, home)


def destroy(key: VaultKey) -> None:
    subprocess.run(["gpgconf", "--kill", "all"], capture_output=True,
                   env={**os.environ, "GNUPGHOME": key.home})
    shutil.rmtree(key.home, ignore_errors=True)
