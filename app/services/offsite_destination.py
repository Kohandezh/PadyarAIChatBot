"""The SFTP off-site destination, set by an admin in the panel (SPEC-H2).

WHY THIS EXISTS
---------------
H1 (backup_offsite.py) read the destination only from env vars and files on
the server, so changing it needed a shell on the server. The operator of an
install runs it from the admin panel and should not need one. Here the
destination lives in the `settings` table, is typed into Infrastructure >
Backups, and becomes the same `sftp:` target H1 copies to.

WHICH DESTINATION WINS
----------------------
The order of sms.py's `setting()` (table, then env, then default), applied to
the destination as a whole: a host saved in the panel wins, else
OFFSITE_BACKUP_TARGET from env, else none. Not field by field: the env
destination logs in with a key FILE and a known_hosts FILE, the panel one
with a pasted secret and a pinned fingerprint, and half of each is no
destination. Unlike sms.save_settings, the panel never writes .env, so H1's
env lines stay as the operator wrote them and emptying the panel falls back
to them.

SECRETS
-------
The password and the private key are stored with secure_store.protect() and
handed to ssh only by login(), for one run. They never go into a response, a
log line, an audit row or an error text: view() says only whether one is
saved. login() also registers each with applog.register_secret(), so
scrub_text removes it from anything that would reach the log store anyway.

HOST KEY PIN
------------
No host key is ever accepted on trust. login() asks the server for its host
keys of every common type (ssh-keyscan; the spike saw a client pick a key
type nobody expected), keeps only the key whose SHA256 fingerprint equals
the saved pin, writes that one key to a private known_hosts file, and limits
HostKeyAlgorithms to its type. sftp then runs with StrictHostKeyChecking=yes
against that file, so a server that shows another key on the real
connection is refused too. No match, or no pin saved: no connection at all.

THE GPG PUBLIC KEY (SPEC-H3)
----------------------------
Every off-site copy is encrypted to a gpg PUBLIC key, and that key can be set
here too (save_gpg_key). Same order as the destination: a key saved in the
panel wins, else the OFFSITE_GPG_PUBLIC_KEY + OFFSITE_GPG_FINGERPRINT env
pair, else none (backup_offsite._gpg_key picks it).

The key is public, so it is stored as plain text, not with secure_store, and
the response shows only its fingerprint, user id and creation date. What is
never allowed in is a PRIVATE key: it stays on paper, off the server. The
check is backup_offsite._inspect_key, run in a throwaway GNUPGHOME with
`gpg --show-keys`, which shows its input and imports nothing. The key is fed
to gpg on stdin and is never written to a file, so a refused private key
reaches no keyring, no disk, no row, no log line and no response. What is
stored is not the pasted text but gpg's own armored copy of the one key it
accepted (`--import-options import-export`, which keeps nothing), so a binary
.gpg upload works and anything pasted around the key is dropped.

NO SECRET ON ARGV
-----------------
Key login: the key is written to a 0600 file in a private temp dir for the
run and removed with the dir. Password login: OpenSSH's SSH_ASKPASS with
SSH_ASKPASS_REQUIRE=force and a helper script that prints the password from
the environment; the script itself holds no secret. `sftp -b` turns on
BatchMode, which turns password login off, so `-o BatchMode=no` goes BEFORE
`-b` (ssh keeps the first value it sees). Checked against OpenSSH 10.3.
"""
import base64
import hashlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.config import logger
from app.services import applog

KEYS = {
    "host": "offsite_sftp_host",
    "port": "offsite_sftp_port",
    "user": "offsite_sftp_user",
    "path": "offsite_sftp_path",
    "auth": "offsite_sftp_auth",
    "password": "offsite_sftp_password",
    "private_key": "offsite_sftp_private_key",
    "fingerprint": "offsite_sftp_host_fingerprint",
}
# The gpg public key rows (SPEC-H3). The fingerprint is saved with the key, so
# the key's own fingerprint is the expected one: no second field to mistype.
GPG_KEYS = {"public_key": "offsite_gpg_public_key", "fingerprint": "offsite_gpg_fingerprint"}
# Measured on the raw bytes, before anything else looks at them. A public key
# with a few user ids is a few KB; 64 KB leaves room for a photo id.
MAX_GPG_KEY = 64 * 1024
AUTH_METHODS = ("password", "key")
DEFAULT_PORT = 22

# A connection test runs while the operator waits on the page.
TEST_TIMEOUT = 30
# How long one host-key scan may take, inside that budget.
SCAN_TIMEOUT = 10
# The slice of that budget kept back for removing a half-written test file.
CLEANUP_TIMEOUT = 8
# Connection tests one admin may run per window. Each test opens a few SSH
# connections, and OpenSSH 9.8+ (PerSourcePenalties) refuses this server's
# address for a while after a burst of failed logins.
TEST_LIMIT = 5
TEST_WINDOW = 600

MAX_PASSWORD = 512
MAX_KEY = 16384

# The form `ssh-keygen -lf` prints: "SHA256:" and 43 base64 characters.
_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}=?$")
_KEY_RE = re.compile(
    r"^-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----\n.+\n-----END \1PRIVATE KEY-----$",
    re.DOTALL)
# Host key types a scan may return, and the signature algorithms each allows.
# ssh-rsa keys sign with SHA-2 only; the SHA-1 `ssh-rsa` algorithm stays off.
_ALGORITHMS = {
    "ssh-ed25519": "ssh-ed25519",
    "ecdsa-sha2-nistp256": "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384": "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521": "ecdsa-sha2-nistp521",
    "ssh-rsa": "rsa-sha2-512,rsa-sha2-256",
}
_PASSWORD_ENV = "PADYAR_SFTP_PASSWORD"
# Answers only a password prompt. Anything else ssh might ask gets no answer.
_ASKPASS = ('#!/bin/sh\n'
            'case "$1" in\n'
            f'  *[Pp]assword*) printf \'%s\\n\' "${_PASSWORD_ENV}" ;;\n'
            '  *) exit 1 ;;\n'
            'esac\n')

# Every sentence an operator can see from this module. The test-result ones
# name one reason each (SPEC-H2 item 7, Amendment 2).
MESSAGES = {
    "bad_request": "درخواست درست نیست. صفحه را دوباره باز کنید و دوباره امتحان کنید.",
    "bad_host": "نشانی سرور درست نیست. فقط حروف انگلیسی، عدد، نقطه و خط تیره مجاز است.",
    "bad_port": "درگاه باید عددی بین ۱ تا ۶۵۵۳۵ باشد.",
    "missing_user": "نام کاربری را وارد کنید.",
    "bad_user": "نام کاربری درست نیست. فقط حروف انگلیسی، عدد، نقطه، خط تیره و زیرخط مجاز است.",
    "missing_path": "پوشهٔ روی سرور را وارد کنید.",
    "relative_path": "پوشه باید با / شروع شود، مثلا /upload.",
    "bad_path": "نام پوشه فقط می‌تواند حروف انگلیسی، عدد و نشانه‌های . _ - / داشته باشد.",
    "bad_auth": "روش ورود را انتخاب کنید: رمز عبور یا کلید خصوصی.",
    "missing_fingerprint": "اثر انگشت کلید سرور را وارد کنید. بدون آن هیچ اتصالی برقرار نمی‌شود.",
    "bad_fingerprint": "اثر انگشت درست نیست. باید با SHA256: شروع شود، همان متنی که "
                       "ssh-keygen -lf نشان می‌دهد.",
    "bad_password": "رمز عبور نباید چندخطی یا بیشتر از ۵۱۲ نویسه باشد.",
    "bad_key": "کلید خصوصی درست نیست. متن کامل کلید را از -----BEGIN تا -----END بچسبانید.",
    "ok": "اتصال برقرار شد و نوشتن فایل آزمایشی موفق بود.",
    "not_saved": "هنوز مقصدی در این صفحه ذخیره نشده است. اول فرم را پر کنید و «ذخیره» را بزنید.",
    "no_password": "رمز عبور ذخیره نشده است. آن را وارد کنید و «ذخیره» را بزنید.",
    "no_key": "کلید خصوصی ذخیره نشده است. آن را وارد کنید و «ذخیره» را بزنید.",
    "host_key": "کلید سرور با اثر انگشتی که وارد کرده‌اید یکی نیست، پس اتصال برقرار نشد. "
                "اثر انگشت درست را از سرویس‌دهنده بگیرید.",
    "auth_password": "نام کاربری یا رمز عبور درست نیست.",
    "auth_key": "نام کاربری یا کلید خصوصی درست نیست، یا این کلید روی سرور مقصد ثبت نشده است.",
    "not_writable": "اتصال برقرار شد، ولی در این پوشه نمی‌توان فایل نوشت. نام پوشه را بررسی "
                    "کنید یا از سرویس‌دهنده اجازهٔ نوشتن بگیرید.",
    "not_deletable": "فایل آزمایشی نوشته شد، ولی دیدن یا پاک کردن فایل‌ها در این پوشه ممکن "
                     "نشد. حساب SFTP باید اجازهٔ پاک کردن هم داشته باشد.",
    "unreachable": "به سرور مقصد وصل نشد. نشانی و درگاه را بررسی کنید و مطمئن شوید سرور روشن است.",
    "refused_for_now": "سرور مقصد فعلاً اتصال این سرور را نمی‌پذیرد. این معمولاً بعد از چند "
                       "تلاش با رمز یا کلید نادرست پیش می‌آید و بعد از کمی صبر خودش برطرف "
                       "می‌شود. یک دقیقه صبر کنید و دوباره امتحان کنید. اگر باز هم همین پیام "
                       "آمد، از سرویس‌دهنده بپرسید آیا نشانی این سرور را بسته است.",
    "timeout": "سرور مقصد در زمان مجاز پاسخ نداد. نشانی و درگاه را بررسی کنید یا کمی بعد "
               "دوباره امتحان کنید.",
    "error": "آزمایش انجام نشد. جزئیات در بخش گزارش‌ها ثبت شد.",
    "encryption_not_ready": "اتصال برقرار شد و نوشتن فایل آزمایشی موفق بود، ولی رمزگذاری "
                            "نسخه‌ها آماده نیست، پس هنوز هیچ نسخه‌ای بیرون فرستاده نمی‌شود. "
                            "کلید عمومی رمزگذاری را در همین صفحه بگذارید.",
    "also_no_encryption": "رمزگذاری نسخه‌ها هم آماده نیست. کلید عمومی رمزگذاری را در همین "
                          "صفحه بگذارید.",
    # The gpg public key (SPEC-H3). One sentence per refusal, and every one
    # says nothing was saved. The private-key one says what to do instead.
    "gpg_empty": "کلیدی وارد نشده است. متن کلید عمومی را بچسبانید یا فایل آن را انتخاب کنید. "
                 "چیزی ذخیره نشد.",
    "gpg_too_big": "این متن یا فایل بزرگ‌تر از ۶۴ کیلوبایت است و کلید عمومی نیست. "
                   "چیزی ذخیره نشد.",
    "gpg_unreadable": "این متن یا فایل یک کلید قابل خواندن نیست. متن کامل کلید عمومی را از "
                      "-----BEGIN PGP PUBLIC KEY BLOCK----- تا -----END PGP PUBLIC KEY BLOCK----- "
                      "بچسبانید. چیزی ذخیره نشد.",
    "gpg_private": "این یک کلید خصوصی است. اینجا فقط کلید عمومی گذاشته می‌شود. کلید خصوصی "
                   "باید فقط روی کاغذ بماند و هرگز روی سرور نیاید. چیزی ذخیره نشد.",
    "gpg_not_one": "در این متن یا فایل بیشتر از یک کلید هست. فقط یک کلید عمومی بگذارید. "
                   "چیزی ذخیره نشد.",
    "gpg_revoked": "این کلید باطل شده است و نمی‌شود با آن رمزگذاری کرد. کلید دیگری بگذارید. "
                   "چیزی ذخیره نشد.",
    "gpg_expired": "این کلید منقضی شده است. یک کلید تازه بسازید و آن را بگذارید. "
                   "چیزی ذخیره نشد.",
    "gpg_cannot_encrypt": "این کلید نمی‌تواند رمزگذاری کند (فقط برای امضا است، یا زیرکلید "
                          "رمزگذاری‌اش منقضی یا باطل شده). کلیدی بگذارید که رمزگذاری هم بکند. "
                          "چیزی ذخیره نشد.",
}


class DestinationError(ValueError):
    """Bad form input. Nothing was saved. `message_fa` is for the operator."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.message_fa = MESSAGES[code]


class GpgKeyRefused(ValueError):
    """The pasted or uploaded gpg key was refused. Nothing was saved. `code`
    is the reason (a key of MESSAGES without the gpg_ prefix); `message_fa` is
    for the operator. The text holds the code only, never any of the input,
    so a refused private key cannot leak through an exception."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.message_fa = MESSAGES["gpg_" + code]


class LoginRefused(Exception):
    """No connection was made, or it was refused. `code` keys MESSAGES.
    The text holds the code only, so it is safe in a manifest or a log."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PanelDestination:
    host: str
    port: int
    user: str
    path: str
    auth: str
    fingerprint: str
    # repr=False: a destination printed into a log line must not carry them.
    password: str = field(default="", repr=False)
    private_key: str = field(default="", repr=False)

    @property
    def target(self) -> str:
        return f"sftp:{self.user}@{self.host}:{self.port}:{self.path}"


# ── Reading ─────────────────────────────────────────────────────────────

def _read() -> dict:
    """The panel's rows, secrets decrypted, read fresh (a save in another
    worker shows at once). {} when the table cannot be read: the caller then
    falls back to env, as get_setting falls back to its default."""
    from app.db.queries import read_settings_strict
    try:
        return read_settings_strict(KEYS.values())
    except Exception as e:  # noqa: BLE001 (a settings read must not stop a copy)
        logger.error("[offsite_destination] settings unreadable: %s", type(e).__name__)
        return {}


def _stored_port(value) -> int:
    try:
        return int(value or DEFAULT_PORT)
    except (TypeError, ValueError):
        return 0  # parse_sftp_target refuses it, so the copy fails visibly


def stored():
    """The saved panel destination, or None when no host is saved."""
    rows = _read()
    host = (rows.get(KEYS["host"]) or "").strip()
    if not host:
        return None
    return PanelDestination(
        host=host, port=_stored_port(rows.get(KEYS["port"])),
        user=rows.get(KEYS["user"]) or "", path=rows.get(KEYS["path"]) or "",
        auth=rows.get(KEYS["auth"]) or "", fingerprint=rows.get(KEYS["fingerprint"]) or "",
        password=rows.get(KEYS["password"]) or "",
        private_key=rows.get(KEYS["private_key"]) or "")


def _env_target() -> str:
    from app.config import OFFSITE_BACKUP_TARGET
    return (OFFSITE_BACKUP_TARGET or "").strip()


def view() -> dict:
    """What the page may see (SPEC-H2 Amendment 1). Never a secret, and never
    the env target: H1's rule keeps a host, a user and a path from env off the
    browser, so `source` says only where the destination comes from."""
    rows = _read()
    host = (rows.get(KEYS["host"]) or "").strip()
    auth = rows.get(KEYS["auth"]) or ""
    return {
        "host": host,
        "port": _stored_port(rows.get(KEYS["port"])) if host else DEFAULT_PORT,
        "user": rows.get(KEYS["user"]) or "",
        "path": rows.get(KEYS["path"]) or "",
        "auth": auth if auth in AUTH_METHODS else "password",
        "fingerprint": rows.get(KEYS["fingerprint"]) or "",
        "password_saved": bool(rows.get(KEYS["password"])),
        "private_key_saved": bool(rows.get(KEYS["private_key"])),
        "source": "panel" if host else ("env" if _env_target() else "none"),
    }


# ── Saving ──────────────────────────────────────────────────────────────

def _text(value, code: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DestinationError(code)
    return value.strip()


def _port(value) -> int:
    if value is None or value == "":
        return DEFAULT_PORT
    if isinstance(value, bool):
        raise DestinationError("bad_port")
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.strip().isdecimal():
        port = int(value.strip())  # Persian digits too: int() reads any decimal digit
    else:
        raise DestinationError("bad_port")
    if not 1 <= port <= 65535:
        raise DestinationError("bad_port")
    return port


def _fingerprint(value) -> str:
    text = _text(value, "bad_fingerprint")
    if not text:
        raise DestinationError("missing_fingerprint")
    if not _FINGERPRINT_RE.match(text):
        raise DestinationError("bad_fingerprint")
    return text.rstrip("=")


def normal_key(text: str) -> str:
    """One key, LF line ends, exactly one final newline. OpenSSH refuses a
    key file without the final newline ("invalid format"), and a form field
    usually loses it."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def _private_key(value) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise DestinationError("bad_key")
    key = normal_key(value)
    if len(key) > MAX_KEY or "\0" in key or not _KEY_RE.match(key.rstrip("\n")):
        raise DestinationError("bad_key")
    return key


def _password(value) -> str:
    # Not stripped: a space can be part of a password.
    if value is None or value == "":
        return ""
    if (not isinstance(value, str) or len(value) > MAX_PASSWORD
            or any(c in value for c in "\r\n\0")):
        raise DestinationError("bad_password")
    return value


def _validated(body: dict, host: str) -> dict:
    """Every field checked, in form order, before anything is written."""
    from app.services import backup_offsite
    host = host.lower()
    if not re.fullmatch(backup_offsite.SFTP_HOST, host):
        raise DestinationError("bad_host")
    port = _port(body.get("port"))
    user = _text(body.get("user"), "bad_user")
    if not user:
        raise DestinationError("missing_user")
    if not re.fullmatch(backup_offsite.SFTP_USER, user):
        raise DestinationError("bad_user")
    path = _text(body.get("path"), "bad_path")
    if not path:
        raise DestinationError("missing_path")
    if not path.startswith("/"):
        raise DestinationError("relative_path")
    if not re.fullmatch(backup_offsite.SFTP_PATH, path) or ".." in path.split("/"):
        raise DestinationError("bad_path")
    auth = _text(body.get("auth"), "bad_auth")
    if auth not in AUTH_METHODS:
        raise DestinationError("bad_auth")
    values = {"host": host, "port": port, "user": user, "path": path.rstrip("/") or "/",
              "auth": auth, "fingerprint": _fingerprint(body.get("fingerprint")),
              "password": _password(body.get("password")),
              "private_key": _private_key(body.get("private_key"))}
    # The last word is H1's own parser, so the panel cannot save a target the
    # copy would refuse.
    try:
        backup_offsite.parse_sftp_target(
            f"{values['user']}@{host}:{port}:{values['path']}")
    except ValueError:
        raise DestinationError("bad_path") from None
    return values


def save(body: dict) -> dict:
    """Validate the whole form, then write it in one transaction.

    Raises DestinationError with nothing written. An empty host removes the
    panel destination with both secrets (Amendment 2: that is not bad input).
    An empty secret keeps the stored one; `clear_secret` removes both, and a
    secret sent with it is then stored. Returns what changed, for the audit
    row: names and flags, never a value.
    """
    from app.services.secure_store import protect
    if not isinstance(body, dict):
        raise DestinationError("bad_request")
    clear = body.get("clear_secret", False)
    if clear is None:
        clear = False
    if not isinstance(clear, bool):
        raise DestinationError("bad_request")
    host = _text(body.get("host"), "bad_host")
    if not host:
        _write({key: None for key in KEYS.values()})
        return {"removed": True}

    values = _validated(body, host)
    writes = {KEYS[f]: values[f] for f in ("host", "port", "user", "path", "auth",
                                           "fingerprint")}
    if clear:
        writes[KEYS["password"]] = None
        writes[KEYS["private_key"]] = None
    for secret in ("password", "private_key"):
        if values[secret]:
            writes[KEYS[secret]] = protect(values[secret])
    _write(writes)
    # Key names avoid applog's secret hints ("auth", "password", "private"),
    # which would blank these harmless flags in the audit row.
    return {"removed": False, "method": values["auth"], "cleared": clear,
            "replaced": [s for s in ("password", "private_key") if values[s]],
            "target": f"sftp:{values['user']}@{host}:{values['port']}:{values['path']}"}


def _write(writes: dict) -> None:
    """One transaction: a failure halfway leaves the old destination whole.
    None deletes the row."""
    from app.db.connection import get_db_connection
    from app.db.queries import clear_settings_cache
    conn = get_db_connection()
    try:
        for key, value in writes.items():
            if value is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                             (key, str(value)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    clear_settings_cache()


# ── The gpg public key (SPEC-H3) ────────────────────────────────────────

def _read_gpg_rows() -> dict:
    """The two key rows, read fresh like _read. {} when the table cannot be
    read: the caller then falls back to the env key, and a settings outage
    never stops a copy."""
    from app.db.queries import read_settings_strict
    try:
        return read_settings_strict(GPG_KEYS.values())
    except Exception as e:  # noqa: BLE001 (a settings read must not stop a copy)
        logger.error("[offsite_destination] settings unreadable: %s", type(e).__name__)
        return {}


def stored_gpg_key():
    """(armored public key, fingerprint) saved in the panel, or None. Both
    rows must be there: a key without its fingerprint is not used."""
    rows = _read_gpg_rows()
    key = rows.get(GPG_KEYS["public_key"]) or ""
    fingerprint = (rows.get(GPG_KEYS["fingerprint"]) or "").replace(" ", "").upper()
    if not key.strip() or not fingerprint:
        return None
    return key, fingerprint


def _grouped(fingerprint: str) -> str:
    """40 hex characters in groups of 4, the way gpg --fingerprint prints it
    and the way a person reads one out."""
    return " ".join(fingerprint[i:i + 4] for i in range(0, len(fingerprint), 4))


def gpg_key_view() -> dict:
    """What the page may see about the key in use. Never the key text.

    `ready` is the same answer the copy and "Test connection" use
    (backup_offsite.encryption_problem), so the page cannot say ready for a
    key the next copy would refuse. The fields come from gpg looking at the
    key itself, also for the env key; when it cannot (a broken file, an
    expired panel key) they stay empty and ready is False, except that a
    panel key still shows the fingerprint it was saved with."""
    from app.services import backup_offsite
    source, _key, saved_fingerprint = backup_offsite._key_source()
    view = {"fingerprint": None, "uid": None, "created": None, "source": source,
            "ready": False}
    if source == "none":
        return view
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        with backup_offsite._gpg_key(home) as (key_file, _fingerprint, _source):
            info = backup_offsite._inspect_key(
                home, key_file, timeout=backup_offsite.READY_CHECK_TIMEOUT)
        view.update(fingerprint=_grouped(info.fingerprint), uid=info.uid,
                    created=info.created)
    except (ValueError, OSError, subprocess.SubprocessError):
        if source == "panel":
            view["fingerprint"] = _grouped(saved_fingerprint)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    view["ready"] = backup_offsite.encryption_problem() == ""
    return view


def _vet_gpg_key(home: str, data: bytes) -> tuple:
    """(armored public key, fingerprint) for the one key in `data`, or
    GpgKeyRefused. `home` is a throwaway GNUPGHOME for gpg's own state.

    The key bytes go to gpg on stdin and are never written to a file, here or
    in gpg's home: a private key pasted by mistake must not reach this
    server's disk even for a moment (owner decision, ADR-028)."""
    from app.services import backup_offsite
    wait = backup_offsite.READY_CHECK_TIMEOUT
    try:
        info = backup_offsite._inspect_key(home, data, timeout=wait)
    except backup_offsite.KeyProblem as e:
        raise GpgKeyRefused(e.code) from None
    # Only now, with one valid PUBLIC key shown, is it re-written. The stored
    # text is gpg's own armored copy of that key, not the pasted text: this
    # takes a binary .gpg file as well as armor, and drops anything else that
    # was pasted around the key. import-export prints the key as an import
    # would have kept it and stores nothing (GnuPG 2.1.14 and newer), so no
    # keyring is touched and no key file is written.
    copied = backup_offsite._gpg(home, "--armor", "--import-options", "import-export",
                                 "--import", input=data, timeout=wait)
    armored = copied.stdout
    if copied.returncode != 0 or "BEGIN PGP PUBLIC KEY BLOCK" not in armored:
        raise GpgKeyRefused("unreadable")
    # What is stored must be what the copy will accept: look at it once more.
    try:
        same = backup_offsite._inspect_key(home, armored.encode("utf-8"),
                                           timeout=wait).fingerprint
    except backup_offsite.KeyProblem:
        raise GpgKeyRefused("unreadable") from None
    if same != info.fingerprint:
        raise GpgKeyRefused("unreadable")
    return armored, info.fingerprint


def save_gpg_key(raw) -> dict:
    """Check the pasted or uploaded key and, only if it is one valid PUBLIC
    key, store it. Returns gpg_key_view(). Raises GpgKeyRefused with nothing
    written; the input is never logged, returned or put in an exception.

    `raw` is a str for pasted text and bytes for an uploaded file. Pasted text
    is trimmed (an operator's copy often carries spaces or blank lines around
    the key); a file is not, because a binary .gpg export may begin or end
    with a byte that looks like whitespace. The 64 KB limit looks at what
    arrived, before any trimming, so padding cannot get around it."""
    pasted = isinstance(raw, str)
    # surrogatepass only to MEASURE: JSON may carry a lone surrogate
    # ("\ud800"), which strict UTF-8 cannot encode. It is still counted, so
    # the size rule holds, and then refused below as unreadable, not a 500.
    data = raw.encode("utf-8", "surrogatepass") if pasted else bytes(raw or b"")
    if len(data) > MAX_GPG_KEY:
        raise GpgKeyRefused("too_big")
    if pasted:
        try:
            data = raw.strip().encode("utf-8")
        except UnicodeEncodeError:
            raise GpgKeyRefused("unreadable") from None
    if not data.strip():
        raise GpgKeyRefused("empty")
    home = tempfile.mkdtemp(prefix="padyar-gpg-")
    try:
        armored, fingerprint = _vet_gpg_key(home, data)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # Both rows in one transaction: a key without its fingerprint is no key.
    _write({GPG_KEYS["public_key"]: armored, GPG_KEYS["fingerprint"]: fingerprint})
    return gpg_key_view()


def clear_gpg_key() -> dict:
    """Remove the panel key, so the env pair (if any) is used again."""
    _write({key: None for key in GPG_KEYS.values()})
    return gpg_key_view()


# ── Login ───────────────────────────────────────────────────────────────

def fingerprint_of(blob: str) -> str:
    """SHA256 fingerprint of a base64 key blob, as `ssh-keygen -lf` prints it."""
    digest = hashlib.sha256(base64.b64decode(blob)).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _blob_type(blob: str) -> str:
    """The key type written inside a key blob (its first SSH string)."""
    raw = base64.b64decode(blob, validate=True)
    size = int.from_bytes(raw[:4], "big")
    return raw[4:4 + size].decode("ascii")


def _scanned_keys(stdout: str) -> list:
    """(type, blob) pairs from ssh-keyscan output, only well-formed ones."""
    keys = []
    for line in (stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 3 or line.startswith("#") or parts[1] not in _ALGORITHMS:
            continue
        try:
            if _blob_type(parts[2]) == parts[1]:
                keys.append((parts[1], parts[2]))
        except (ValueError, UnicodeDecodeError):
            continue
    return keys


def _remaining(deadline, cap: float) -> float:
    if deadline is None:
        return cap
    left = deadline - time.monotonic()
    if left <= 0:
        raise subprocess.TimeoutExpired("sftp", 0)
    return min(cap, left)


# What sshd writes instead of its banner when it turns an address away.
_REFUSAL_LINES = (b"Not allowed at this time", b"Exceeded MaxStartups")


def _why_no_keys(dest: PanelDestination, timeout: float) -> str:
    """The scan found no key. Unreachable, too slow, or refusing us for now?
    One plain socket connect tells them apart (only on this failure path,
    since every connect that does not log in costs a PerSourcePenalties
    second).

    OpenSSH 9.8+ refuses an address for a while once more than 15 s of
    penalty has built up (5 s per failed login, 1 s per connection that
    never logs in): it accepts the TCP connection, writes one line instead
    of its banner and closes. Seen after two wrong-password tests in review;
    the line, seen live from sshd 10.3, is "Not allowed at this time".
    "Exceeded MaxStartups" is sshd turning us away for being busy. Both
    pass by themselves, and so may a close with no line at all."""
    try:
        conn = socket.create_connection((dest.host, dest.port), timeout=timeout)
    except (socket.timeout, TimeoutError):
        return "timeout"
    except OSError:
        return "unreachable"
    with conn:
        conn.settimeout(timeout)
        try:
            banner = conn.recv(64)
        except (socket.timeout, TimeoutError):
            return "timeout"
        except ConnectionResetError:
            return "refused_for_now"
        except OSError:
            return "unreachable"
    if not banner or banner.startswith(_REFUSAL_LINES):
        return "refused_for_now"
    return "unreachable"


def _pinned_key(dest: PanelDestination, deadline) -> tuple:
    """The server's host key whose fingerprint is the saved pin, as (type,
    blob). Raises LoginRefused before any login is tried."""
    timeout = max(1, int(_remaining(deadline, SCAN_TIMEOUT)))
    scan = subprocess.run(
        ["ssh-keyscan", "-T", str(timeout), "-t", "ed25519,ecdsa,rsa",
         "-p", str(dest.port), "--", dest.host],
        capture_output=True, text=True, timeout=_remaining(deadline, timeout * 2 + 5))
    keys = _scanned_keys(scan.stdout)
    if not keys:
        raise LoginRefused(_why_no_keys(dest, timeout))
    for key_type, blob in keys:
        if fingerprint_of(blob) == dest.fingerprint:
            return key_type, blob
    raise LoginRefused("host_key")


def _write_private(path: str, text: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


@contextmanager
def login(dest: PanelDestination, deadline=None):
    """Yield a backup_offsite.SftpLogin for `dest`, pinned to its host key.
    Every temp file (known_hosts, key, askpass helper) lives in one private
    dir that is removed on the way out, also on error."""
    from app.services.backup_offsite import SftpLogin
    if not dest.fingerprint:
        raise LoginRefused("missing_fingerprint")
    if dest.auth not in AUTH_METHODS:
        raise LoginRefused("bad_auth")
    secret = dest.password if dest.auth == "password" else dest.private_key
    if not secret:
        raise LoginRefused("no_password" if dest.auth == "password" else "no_key")
    applog.register_secret(secret)

    home = tempfile.mkdtemp(prefix="padyar-sftp-")
    try:
        key_type, blob = _pinned_key(dest, deadline)
        known = os.path.join(home, "known_hosts")
        host = dest.host if dest.port == DEFAULT_PORT else f"[{dest.host}]:{dest.port}"
        _write_private(known, f"{host} {key_type} {blob}\n", 0o600)
        options = ["-o", f"HostKeyAlgorithms={_ALGORITHMS[key_type]}",
                   "-o", "CheckHostIP=no",
                   "-o", "IdentityAgent=none"]
        if dest.auth == "key":
            identity = os.path.join(home, "id")
            _write_private(identity, normal_key(dest.private_key), 0o600)
            yield SftpLogin(known_hosts=known, options=options + [
                "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes",
                "-o", "PreferredAuthentications=publickey",
                "-o", "PasswordAuthentication=no",
                "-o", "KbdInteractiveAuthentication=no",
                "-i", identity])
        else:
            askpass = os.path.join(home, "askpass")
            _write_private(askpass, _ASKPASS, 0o700)
            yield SftpLogin(
                known_hosts=known,
                before_batch=["-o", "BatchMode=no"],
                options=options + [
                    "-o", "PubkeyAuthentication=no",
                    "-o", "PreferredAuthentications=password,keyboard-interactive",
                    "-o", "NumberOfPasswordPrompts=1"],
                env={**os.environ, "SSH_ASKPASS": askpass,
                     "SSH_ASKPASS_REQUIRE": "force", _PASSWORD_ENV: dest.password})
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ── Test connection ─────────────────────────────────────────────────────

def _outcome(code: str) -> dict:
    return {"ok": code == "ok", "reason": code, "message": MESSAGES[code]}


def _failed_command(stdout: str) -> str:
    """In batch mode sftp echoes each command before it runs it, and stops at
    the first one that fails, so the last echo names the failed command."""
    echoed = [line for line in (stdout or "").splitlines() if line.startswith("sftp> ")]
    return echoed[-1].split()[1] if echoed and len(echoed[-1].split()) > 1 else ""


# stderr markers of an sftp run that ended before any login, so it cannot
# have written the test file. `kex_exchange_identification` is the server
# closing before its banner: PerSourcePenalties (or MaxStartups) refusing.
_BEFORE_LOGIN = ("Permission denied", "Host key verification failed",
                 "IDENTIFICATION HAS CHANGED", "kex_exchange_identification",
                 "ssh: connect to host", "Could not resolve hostname",
                 "Connection closed by ")


def _refused_before_login(proc) -> bool:
    """sshd turned this sftp run away before any login. OpenSSH 10.3 then
    prints only "Connection closed by <host> port <port>" (seen live under
    PerSourcePenalties): no batch echo, no "Permission denied". sftp echoes
    each batch command line by line (also checked live), so no echo means
    no command ran."""
    err = proc.stderr or ""
    return ("kex_exchange_identification" in err
            or (proc.returncode == 255 and "Connection closed by " in err
                and "Permission denied" not in err
                and "sftp> " not in (proc.stdout or "")))


def _classify(proc, auth: str) -> str:
    if proc.returncode == 0:
        return "ok"
    err = proc.stderr or ""
    if "Host key verification failed" in err or "IDENTIFICATION HAS CHANGED" in err:
        return "host_key"
    if _refused_before_login(proc):
        return "refused_for_now"
    if proc.returncode == 255:
        if "Permission denied" in err:
            return "auth_password" if auth == "password" else "auth_key"
        if "timed out" in err:
            return "timeout"
        return "unreachable"
    if _failed_command(proc.stdout) in ("put", "chmod", "rename"):
        return "not_writable"
    return "not_deletable"


def _may_have_written(proc) -> bool:
    """Could this run have left the test file (or its .part) behind? Not
    after exit 0 (its own rm ran) and not when it ended before any login.
    Yes for everything else: a failed step, a connection lost mid-batch,
    or an ending we cannot place."""
    if proc.returncode == 0:
        return False
    if "sftp> " in (proc.stdout or ""):
        return True  # sftp echoes each batch command, so it had logged in
    return not any(marker in (proc.stderr or "") for marker in _BEFORE_LOGIN)


def _clean_up(target, session, remote: str, deadline) -> None:
    """Best effort, on a new connection: remove both names the test may have
    left. Its outcome is never the operator's answer, so nothing it raises
    or returns reaches them."""
    from app.services import backup_offsite
    try:
        backup_offsite._sftp(
            target, session, f"-rm {remote}.part\n-rm {remote}\n",
            timeout=max(1.0, min(CLEANUP_TIMEOUT, deadline - time.monotonic())),
            connect_timeout=max(1, int(min(CLEANUP_TIMEOUT, SCAN_TIMEOUT))))
    except Exception as e:  # noqa: BLE001 (best effort by design)
        logger.warning("[offsite_destination] test file cleanup failed: %s",
                       type(e).__name__)


def _with_encryption(code: str, problem: str) -> dict:
    """A copy needs the connection AND a usable gpg key. A test that only
    connected must not read as ready (review fix 3)."""
    if not problem:
        return _outcome(code)
    if code == "ok":
        return _outcome("encryption_not_ready")
    result = _outcome(code)
    result["message"] = f"{MESSAGES[code]} {MESSAGES['also_no_encryption']}"
    return result


def try_connection() -> dict:
    """Try the SAVED destination once: pinned host key, login, then the same
    steps a copy takes (put a .part, chmod, rename, list, delete) on one
    small file that holds no backup data, and check that a copy could also
    be ENCRYPTED. Returns {"ok", "reason", "message"} (plus "encryption",
    the English reason, for the audit row only); never raises, never runs
    longer than TEST_TIMEOUT."""
    from app.services import backup_offsite
    dest = stored()
    if dest is None:
        return _outcome("not_saved")
    deadline = time.monotonic() + TEST_TIMEOUT
    problem = backup_offsite.encryption_problem()
    work = tempfile.mkdtemp(prefix="padyar-sftp-test-")
    try:
        result = _with_encryption(_connect_and_write(dest, deadline, work), problem)
    except LoginRefused as e:
        result = _with_encryption(e.code if e.code in MESSAGES else "error", problem)
    except subprocess.TimeoutExpired:
        result = _with_encryption("timeout", problem)
    except Exception as e:  # noqa: BLE001 (the page gets a sentence, the log the detail)
        applog.exception("backup", "backup.offsite.test_error", e,
                         "آزمایش اتصال مقصد بیرون از سرور خطا داد", outcome="error")
        result = _outcome("error")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if problem:
        result["encryption"] = problem
    return result


def _connect_and_write(dest: PanelDestination, deadline, work: str) -> str:
    """The network half of try_connection. Returns the reason code; raises
    LoginRefused / TimeoutExpired from the login step. Once the batch has
    started, the test file is removed on every path where it may exist."""
    from app.services import backup_offsite
    target = backup_offsite.parse_sftp_target(dest.target[len("sftp:"):])
    local = os.path.join(work, "probe")
    with open(local, "w", encoding="utf-8") as f:
        f.write("Padyar off-site connection test. Safe to delete.\n")
    remote = f"{target.path.rstrip('/')}/.padyar-connection-test-{secrets.token_hex(8)}"
    batch = (f'put "{local}" {remote}.part\n'
             f"chmod 600 {remote}.part\n"
             f"rename {remote}.part {remote}\n"
             f"ls -ln {target.path}\n"
             f"rm {remote}\n")
    with login(dest, deadline) as session:
        # The batch gets the budget minus the cleanup's slice, so a cleanup
        # after a timeout still fits inside TEST_TIMEOUT.
        budget = _remaining(deadline, TEST_TIMEOUT) - CLEANUP_TIMEOUT
        if budget < 1:
            raise subprocess.TimeoutExpired("sftp", 0)
        try:
            proc = backup_offsite._sftp(
                target, session, batch, timeout=budget,
                connect_timeout=max(1, int(min(budget, SCAN_TIMEOUT))))
        except subprocess.TimeoutExpired:
            # Killed mid-batch: the put may have run. A timeout leaves no
            # trustworthy output to tell, so always try.
            _clean_up(target, session, remote, deadline)
            raise
        if _may_have_written(proc):
            _clean_up(target, session, remote, deadline)
        return _classify(proc, dest.auth)
