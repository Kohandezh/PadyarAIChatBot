"""Build the handover archive.

What matters here is what is LEFT OUT. This archive goes to another person, so
it must not carry a single credential, and it must not carry 1.5 GB of things
that regenerate themselves.

    .venv/bin/python scripts/make-handover-zip.py
    .venv/bin/python scripts/make-handover-zip.py --no-git     # source only

The script REFUSES to build if it finds a secret in what it is about to write —
that check is the point, not a formality.
"""
import argparse
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Never ship. Real credentials, real visitor data, or an admin password.
DENY_NAMES = {
    ".env",
    "ADMIN_CREDENTIALS.txt",
    "chat_history.db",
    "chat_history.db-wal",
    "chat_history.db-shm",
    "otp-dev-outbox.log",
    "pip-audit.json",
}
# `.env.example` is the TEMPLATE the recipient needs — it must ship. Only the
# real file and its backups are denied.
#
# KEEP_NAMES IS AN ALLOWLIST AND IT CANNOT OVERRIDE A CREDENTIAL DENY.
# `ASANAK-CREDENTIALS.env` used to be listed here, which force-included a file
# holding the live Asanak username, password, API key AND `SECRET_KEY` — the
# key every stored `enc:` value is derived from, so the archive shipped both
# the lock and the key. It also sat in SECRET_ALLOW below, so the "refuse to
# build on a credential" guard had been told to look away from it. Two
# allowlists, one file, no alarm.
KEEP_NAMES = {".env.example"}

# Credential files. This denial is UNCONDITIONAL: it is evaluated before
# KEEP_NAMES, so no allowlist entry — present or future — can readmit one.
# Matching is by BASENAME, so a nested or relocated copy
# (`docs/backup/.env`, `old/ASANAK-CREDENTIALS.env`) is caught just the same;
# the previous rule leaned on a path regex that only happened to work at the
# repo root.
CREDENTIAL_DENY_NAMES = {
    ".env",
    "ASANAK-CREDENTIALS.env",
    "ADMIN_CREDENTIALS.txt",
}
# Credential-file shapes, so a variant nobody thought to enumerate still loses:
# `.env.production`, `.env.backup.20260819`, `SMSIR-CREDENTIALS.env`,
# `prod.credentials`. `.env.example` is the single deliberate exception.
CREDENTIAL_DENY_SUFFIXES = ("-credentials.env", "_credentials.env", ".credentials")


def is_credential_file(rel: str) -> bool:
    """True for anything that carries real secrets. Basename-based on purpose.

    Deliberately conservative about what it lets through: source files that
    merely talk about credentials (`tests/test_admin_credentials_file.py`,
    `app/services/secure_store.py`) keep their own extensions and are not
    matched, because the shapes below require a credential-file extension.
    """
    name = Path(rel).name
    low = name.lower()
    if name == ".env.example":                 # the template — must ship
        return False
    if name in CREDENTIAL_DENY_NAMES:
        return True
    if low.startswith(".env."):                # .env.local, .env.backup.*, …
        return True
    if any(low.endswith(sfx) for sfx in CREDENTIAL_DENY_SUFFIXES):
        return True
    if low.startswith("admin_credentials"):    # ADMIN_CREDENTIALS.txt(.1, .bak)
        return True
    return False
DENY_PATTERNS = [
    re.compile(r"(^|/)\.env(\.|$)"),          # .env, .env.backup.*, .env.local
    re.compile(r"chat_history\.backup\..*\.db$"),
    re.compile(r"visit-taxonomy\.backup\..*\.json$"),
    re.compile(r"(^|/)ADMIN_CREDENTIALS"),
]

# Regenerates itself; costs gigabytes.
SKIP_DIRS = {
    ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", ".DS_Store", "backups",
}
# Path prefixes (relative to ROOT) dropped wholesale.
SKIP_PREFIXES = [
    "data/models",              # ~1 GB, downloaded on first run
    "graphify-out/cache",       # extraction cache, rebuilt on demand
    "graphify-out/2026-",       # previous graph snapshots
    "dist/",
]

# Secrets look like these. Checked against every text file that would ship.
# Deliberately narrow. An earlier, looser version flagged
# `ADMIN_PASSWORD = os.getenv(...)` in app/config.py and a fake fixture in a
# test — a scanner that cries wolf gets switched off, so these match the shape
# of a REAL stored secret: a dotenv line at column 0 with no spaces around `=`,
# an OpenAI key, or the generated credentials file's own format.
SECRET_SIGNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"^(ASANAK_PASSWORD|ASANAK_API_KEY|ADMIN_PASSWORD|OPENAI_API_KEY"
               r"|SECRET_KEY|ADMIN_SECURITY_ANSWER)=\S+", re.M),
    re.compile(r"^Password:\s*\S+", re.M),
]
# Files allowed to contain those shapes: placeholders and documentation.
# NOTE: `ASANAK-CREDENTIALS.env` is deliberately NOT here any more. It is now
# denied outright, so the scanner should never meet it — and if some future
# edit readmits it, the scanner must FAIL THE BUILD rather than wave it past.
# An entry here is the scanner being told to look away; that is only ever
# correct for placeholders and prose.
SECRET_ALLOW = re.compile(
    r"(^\.env\.example$|^docs/|^remaining\.md$|^scripts/make-handover-zip\.py$"
    r"|^\.github/workflows/|^CLAUDE\.md$|^AGENTS\.md$|^README\.md$)")

TEXT_SUFFIXES = {".py", ".js", ".json", ".md", ".html", ".css", ".yml", ".yaml",
                 ".txt", ".sh", ".cfg", ".ini", ".toml"}


def denied(rel: str) -> bool:
    # ORDER IS THE SECURITY PROPERTY. The credential check runs FIRST so that
    # no allowlist can readmit a secret — which is exactly how
    # `ASANAK-CREDENTIALS.env` came to be shipped: it was added to KEEP_NAMES,
    # and KEEP_NAMES used to be consulted before every deny rule.
    if is_credential_file(rel):
        return True
    name = Path(rel).name
    if name in KEEP_NAMES:
        return False
    if name in DENY_NAMES:
        return True
    return any(p.search(rel) for p in DENY_PATTERNS)


def skipped(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in SKIP_DIRS for p in parts):
        return True
    return any(rel.startswith(pref) for pref in SKIP_PREFIXES)


def collect(include_git: bool):
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT)
        rel_dir = "" if rel_dir == "." else rel_dir

        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not (d == ".git" and not include_git)
            and not skipped(os.path.join(rel_dir, d))
        ]

        for fn in filenames:
            rel = os.path.normpath(os.path.join(rel_dir, fn)) if rel_dir else fn
            rel = rel.replace(os.sep, "/")
            if skipped(rel) or denied(rel):
                continue
            files.append(rel)
    return sorted(files)


# A key NAME is not a secret; the VALUE is. `OPENAI_API_KEY=""` in the
# installer and `sk-fake-openai-key` in a test are both correct code, and a
# scanner that blocks the build on them teaches people to bypass it.
PLACEHOLDER_WORDS = ("fake", "dummy", "test", "example", "placeholder", "sample",
                     "changeme", "change-me", "your", "xxx", "todo", "none",
                     "local", "ci-", "redacted", "hunter",
                     # Canonical "obviously not real" hex words. Test fixtures
                     # that must LOOK like a live key use these — e.g.
                     # `sk-livedeadbeefcafe1234` in tests/test_applog.py, whose
                     # whole job is to prove the redactor catches key-shaped
                     # text. Without this the build refused on its own test
                     # suite, and the tempting fix is to add the file to
                     # SECRET_ALLOW — which blinds the scanner to everything
                     # else in it. Teaching the scanner is the smaller hammer.
                     "deadbeef", "cafebabe", "deadc0de", "badc0ffee",
                     "sentinel", "synthetic", "notreal", "not-real")


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip('"\'').strip()
    if len(v) < 8:                      # too short to be a real credential
        return True
    low = v.lower()
    if any(w in low for w in PLACEHOLDER_WORDS):
        return True
    if len(set(v)) <= 2:                # "xxxxxxxxxx", "----------"
        return True
    return False


def scan_for_secrets(files):
    """Refuse to build an archive that carries a credential.

    Judges the VALUE, not the key name — see _is_placeholder.
    """
    hits = []
    for rel in files:
        if rel.startswith(".git/") or SECRET_ALLOW.match(rel):
            continue
        if Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            continue
        path = ROOT / rel
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in SECRET_SIGNS:
            for m in pat.finditer(text):
                whole = m.group(0)
                value = whole.split("=", 1)[1] if "=" in whole else whole
                value = value.split(":", 1)[1] if whole.startswith("Password:") else value
                if _is_placeholder(value):
                    continue
                hits.append((rel, pat.pattern, whole[:40]))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-git", action="store_true",
                    help="exclude .git (smaller, but the recipient cannot push with history)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    include_git = not args.no_git
    files = collect(include_git)

    print(f"collecting… {len(files)} files")
    hits = scan_for_secrets(files)
    if hits:
        print("\nREFUSING TO BUILD — a credential-shaped value is in the archive set:\n")
        for rel, pat, sample in hits:
            print(f"  {rel}\n    pattern: {pat}\n    matched: {sample}")
        print("\nRemove or ignore the file, then re-run.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path(args.out) if args.out else ROOT.parent / f"inotex-chatbot-handover-{stamp}.zip"

    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in files:
            src = ROOT / rel
            if not src.is_file():
                continue
            z.write(src, arcname=f"PadyarAIChatbot/{rel}")
            total += src.stat().st_size

    size = out.stat().st_size
    print(f"\narchive : {out}")
    print(f"files   : {len(files)}")
    print(f"raw     : {total / 1e6:.1f} MB  →  zipped {size / 1e6:.1f} MB")
    print(f"git     : {'included' if include_git else 'EXCLUDED'}")
    print("\nexcluded: .venv, data/models, __pycache__, backups, graph cache,")
    print("          chat_history.db, OTP outbox")
    print("credentials excluded (unconditional): .env and .env.*,")
    print("          ASANAK-CREDENTIALS.env, ADMIN_CREDENTIALS.txt,")
    print("          *-CREDENTIALS.env, *.credentials   —  .env.example DOES ship")
    print("\nsecret scan: clean")
    print("\nNOTE: this archive contains NO credentials by design. The recipient")
    print("      configures their own via .env.example, or receives them")
    print("      through a separate channel — never inside this ZIP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
