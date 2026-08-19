"""Encryption for stored secrets + the writer for the project's `.env`.

Why this module exists
----------------------
The customer's requirement for the SMS gateway credentials is: typed once in
the admin panel, saved into the environment file, and *not readable* there.
That is two jobs, and they belong together because neither is useful alone:

1. `protect()` / `reveal()` — authenticated encryption of a single settings
   value (Fernet: AES-128-CBC + HMAC-SHA256, from the `cryptography` package).
   Ciphertext is stored with an `enc:` prefix, so a value written before this
   existed stays plaintext and keeps working untouched — `reveal()` passes
   anything that is not one of our tokens straight through.

2. `write_env_values()` — updates keys inside `.env` **in place**: every other
   key, every comment and the file's order survive. The new file is written to
   a temp file in the same directory and `os.replace`d over the original, so a
   crash mid-write cannot truncate it, and the result is chmod 0600.

Where the key comes from
------------------------
Derived (HKDF-SHA256) from the secret this install already has: `SECRET_KEY`
from the environment, or the `app_secret_key` row the app generates on first
run (`app.auth.security.get_app_secret`). No new secret for an operator to
manage — and deliberately NOT written into `.env`, because a key sitting next
to its own ciphertext protects nothing.

Consequence worth knowing: the ciphertext in `.env` is only readable by an app
whose secret is unchanged. Losing the database (when SECRET_KEY is empty) or
changing SECRET_KEY means the stored secrets can no longer be decrypted and
must be re-entered in the admin panel. The app says so instead of sending
garbage to the gateway: `reveal()` fails closed and returns "".
"""
import base64
import os
import shutil
import tempfile
import time
from typing import Dict

from app.config import logger

# Marks a value as encrypted by this module. Anything without it is a legacy
# plaintext value and is returned as-is.
PREFIX = "enc:"

# HKDF context string. Changing it invalidates every stored secret, so don't.
_KDF_INFO = b"padyar.secure_store.v1"

# Fernet tokens always start with the version byte 0x80. Used to tell "this is
# ours but we cannot decrypt it" (fail closed) from "this is not ours at all"
# (return it unchanged) — an operator is free to type a value that happens to
# begin with "enc:".
_FERNET_VERSION = 0x80

_fernet_cache = None  # (app_secret, Fernet) — recomputed if the secret changes


def _fernet():
    global _fernet_cache
    from app.auth.security import get_app_secret
    secret = get_app_secret()
    if _fernet_cache and _fernet_cache[0] == secret:
        return _fernet_cache[1]

    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=_KDF_INFO).derive(secret.encode("utf-8"))
    fernet = Fernet(base64.urlsafe_b64encode(raw))
    _fernet_cache = (secret, fernet)
    return fernet


def is_protected(value: str) -> bool:
    """True if `value` was written by `protect()`."""
    return bool(value) and value.startswith(PREFIX)


def protect(value: str) -> str:
    """Encrypt one secret for storage. Empty in, empty out."""
    if not value:
        return ""
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def reveal(value: str) -> str:
    """Decrypt a value written by `protect()`; pass anything else through.

    Fails closed: a token this install cannot decrypt (wrong SECRET_KEY, a
    restored database, tampering) returns "" so the caller reports "not
    configured" instead of sending ciphertext to a gateway. Never logs the
    value.
    """
    if not is_protected(value):
        return value or ""
    token = value[len(PREFIX):]
    try:
        if base64.urlsafe_b64decode(token.encode("ascii"))[0] != _FERNET_VERSION:
            return value  # not one of ours — an operator typed "enc:..."
    except Exception:  # noqa: BLE001 — not base64 at all, so not ours
        return value
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as e:  # noqa: BLE001 — only the type is safe to log
        logger.error("[secure_store] cannot decrypt a stored secret (%s) — "
                     "re-enter it in the admin panel", type(e).__name__)
        return ""


# --- .env file ---------------------------------------------------------

def env_path() -> str:
    """The project's environment file, resolved where the app resolves it."""
    import app.config as config
    return config.ENV_FILE


def _quote(value: str) -> str:
    """Quote only when the value needs it (dotenv-compatible)."""
    if value and all(c.isalnum() or c in "._-:/=+@" for c in value):
        return value
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


_backed_up = set()


def _backup(path: str) -> None:
    """One timestamped copy before this process first edits the file.

    Backups go to the sibling `backups/` directory, which is already
    gitignored — a copy named `.env.backup.*` next to `.env` would NOT be
    (`.gitignore` ignores the exact name `.env`).
    """
    if path in _backed_up or not os.path.exists(path):
        return
    folder = os.path.join(os.path.dirname(path) or ".", "backups")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    dest = os.path.join(folder, ".env.backup.%s" % time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(path, dest)
    os.chmod(dest, 0o600)
    _backed_up.add(path)
    logger.info("[secure_store] backed up the environment file before editing")


_MANAGED_HEADER = "# --- Written by the admin panel (Settings -> SMS) ---"


def _rewrite(text: str, updates: Dict[str, str]) -> str:
    """Replace the given keys in place; append the ones the file lacks."""
    remaining = dict(updates)
    lines = text.splitlines()
    out = []
    for line in lines:
        stripped = line.lstrip()
        key = ""
        if stripped and not stripped.startswith("#") and "=" in stripped:
            candidate = stripped.split("=", 1)[0].strip()
            if candidate.startswith("export "):
                candidate = candidate[len("export "):].strip()
            if candidate.replace("_", "").isalnum():
                key = candidate
        if key and key in updates:
            prefix = "export " if stripped.startswith("export ") else ""
            out.append("%s%s%s=%s" % (line[:len(line) - len(stripped)], prefix,
                                      key, _quote(updates[key])))
            remaining.pop(key, None)
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        if _MANAGED_HEADER not in out:
            out.append(_MANAGED_HEADER)
        for key, value in remaining.items():
            out.append("%s=%s" % (key, _quote(value)))
    return "\n".join(out).rstrip("\n") + "\n"


def write_env_values(updates: Dict[str, str], path: str = "") -> bool:
    """Update keys in `.env`, preserving everything else. True if written.

    Never raises: a read-only deployment (or a root-owned .env) must not make
    the admin panel fail — the settings table already holds the value, so the
    caller reports "saved, but the environment file could not be updated".
    """
    if not updates:
        return True
    path = path or env_path()
    try:
        _backup(path)
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        content = _rewrite(existing, updates)

        folder = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".env.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)          # survives the replace: same inode
            os.replace(tmp, path)         # atomic — never a truncated .env
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        logger.info("[secure_store] updated %d key(s) in the environment file",
                    len(updates))
        return True
    except Exception as e:  # noqa: BLE001 — the key names are safe, values are not
        logger.error("[secure_store] could not update the environment file: %s",
                     type(e).__name__)
        return False
