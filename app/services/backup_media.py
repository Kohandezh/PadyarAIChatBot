"""Media archives inside backups — shared by both backup engines.

A backup that only carries the database loses every video and logo an
administrator ever uploaded: on restore the answers would point at files
that no longer exist. Each backup set therefore also carries `media.tar`,
an uncompressed tar of the install's media root.

Uncompressed on purpose: the media root is mostly mp4/jpg, which are
already compressed — gzip would burn CPU for a percent of size and make
every scheduled backup slower for nothing.

Security contract for extraction (the load-bearing part of this module):
a tar archive is untrusted input, whether it was uploaded by an operator
or sat on our own disk for a month. Members are accepted one by one and
ONLY if they are regular files or directories, relative, and free of any
path that escapes the media root. symlinks, hardlinks, device nodes and
absolute paths are rejected — never extracted. The tarfile `filter=`
parameter is newer than this project's Python floor, so the checks are
explicit here instead.
"""
import os
import tarfile

import backup_db
from app.config import logger

ARCHIVE_NAME = "media.tar"
# Regenerable caches never belong in a backup.
EXCLUDED_DIRS = {"tts-cache"}

# Upload ceiling for a media archive: generous (an install with an hour of
# video is well past a gigabyte) but bounded, so a runaway upload cannot fill
# the disk before validation runs.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024


class MediaArchiveError(Exception):
    """A media archive could not be created, read or trusted."""

    def __init__(self, message_fa: str):
        super().__init__(message_fa)
        self.message_fa = message_fa


def media_root() -> str:
    """The install's media root, resolved at CALL time.

    app.config is read per call (never captured at import) so tests redirect
    it per test and an install can set MEDIA_ROOT in .env later."""
    from app import config
    return getattr(config, "MEDIA_ROOT", None) or os.path.join(
        backup_db.BASE_DIR, "media")


def _excluded(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return any(p in EXCLUDED_DIRS for p in parts)


def create_archive(dest_path: str) -> dict | None:
    """Tar the media root into `dest_path`. Returns the manifest entry
    ({name, role, bytes, sha256}) or None when there is no media dir.

    A missing media directory is not an error: a fresh install has none,
    and an empty tar would just make every set bigger for no content."""
    root = media_root()
    if not os.path.isdir(root):
        return None

    import hashlib
    with tarfile.open(dest_path, "w") as tar:
        for name in sorted(os.listdir(root)):
            if name in EXCLUDED_DIRS:
                continue
            tar.add(os.path.join(root, name), arcname=name, recursive=True)

    h = hashlib.sha256()
    with open(dest_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {
        "role": "media",
        "name": ARCHIVE_NAME,
        "sha256": h.hexdigest(),
        "bytes": os.path.getsize(dest_path),
    }


def _safe_members(tar: tarfile.TarFile, root: str) -> list:
    """Members that may be extracted: regular files/dirs, relative, inside
    `root`. Anything else aborts the whole extraction — one hostile entry
    makes the archive untrusted, so it is refused, not skipped."""
    root_real = os.path.realpath(root)
    safe = []
    for member in tar.getmembers():
        if member.name.startswith("/") or ".." in member.name.replace("\\", "/").split("/"):
            raise MediaArchiveError(
                "آرشیو حاوی مسیر نامعتبر است و باز نشود.")
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise MediaArchiveError(
                "آرشیو حاوی فایل غیرمجاز (پیوند یا دستگاه) است.")
        target = os.path.realpath(os.path.join(root_real, member.name))
        if target != root_real and not target.startswith(root_real + os.sep):
            raise MediaArchiveError(
                "آرشیو حاوی مسیر نامعتبر است و باز نشود.")
        safe.append(member)
    return safe


def extract_archive(tar_path: str, actor: str = "") -> int:
    """Extract a trusted-on-disk media archive over the media root.

    Returns the number of files written. Raises MediaArchiveError for
    anything that fails the safety checks — in that case NOTHING was
    written (validation of every member happens before the first write)."""
    root = media_root()
    os.makedirs(root, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:") as tar:
            members = _safe_members(tar, root)
            tar.extractall(path=root, members=members)
            return sum(1 for m in members if m.isfile())
    except MediaArchiveError:
        raise
    except (tarfile.TarError, OSError) as exc:
        logger.error("Media archive extraction failed: %s", type(exc).__name__)
        raise MediaArchiveError(
            "باز کردن آرشیو رسانه‌ها ممکن نشد.") from exc


def restore_upload(fileobj, actor: str = "") -> dict:
    """Stream an uploaded archive to a temp file, validate, extract.

    Streams in chunks — a media archive can be gigabytes, and buffering it
    in memory would cap every restore at the worker's RAM."""
    import hashlib
    import tempfile

    fd, tmp_path = tempfile.mkstemp(suffix=".media.tar")
    try:
        h = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise MediaArchiveError(
                        "حجم آرشیو رسانه‌ها بیش از حد مجاز است.")
                h.update(chunk)
                out.write(chunk)

        if total == 0:
            raise MediaArchiveError("فایل ارسالی خالی است.")
        # Fail before touching the media root if this is not a tar at all.
        try:
            with tarfile.open(tmp_path, "r:") as probe:
                probe.getmembers()
        except tarfile.TarError as exc:
            raise MediaArchiveError(
                "فایل ارسالی یک آرشیو رسانه‌های معتبر نیست.") from exc

        files = extract_archive(tmp_path, actor=actor)
        return {"sha256": h.hexdigest(), "bytes": total, "files": files}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
