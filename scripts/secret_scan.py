#!/usr/bin/env python3
"""Refuse to commit a credential. One implementation, shared with the packager.

WHY THIS EXISTS AS A SCRIPT
---------------------------
The check used to live as a regex inside .github/workflows/ci.yml, and it was
RED — seven hits, every one of them a test fixture whose whole job is to look
like a live key so the redaction tests can prove the redactor catches it.

A red gate teaches people to ignore the gate. The two tempting fixes both make
things worse:

  - exclude tests/ — blinds the scanner to the directory where a pasted key is
    most likely to land, and where this project's fixtures already live;
  - delete the patterns — stops catching anything.

`scripts/make-handover-zip.py` had already solved this the right way, and said
so in a comment: judge the VALUE, not the key name or the file. A value
containing `deadbeef`, `sentinel` or `synthetic` is obviously not a real
credential; a high-entropy value with no such word is. That module owns
PLACEHOLDER_WORDS and `_is_placeholder`, so this script imports them rather
than restating them. Two copies of a security vocabulary drift, and the copy
that drifts is the one nobody is looking at.

Because the judgement is value-based, widening the SHAPES is safe, and this
adds the ones the old regex missed:

  sk-ant-…  the old class stopped at the first hyphen, so an Anthropic key
            `sk-ant-api03-…` matched exactly three characters and never fired.
  xai-, gsk_, AIza…, gw_live_   never covered at all.

Exit 0 = clean. Exit 1 = a value that looks real. Run it directly:

    python3 scripts/secret_scan.py
"""
import importlib.util
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_packager():
    """Import make-handover-zip.py by path — the hyphens make it unimportable."""
    path = ROOT / "scripts" / "make-handover-zip.py"
    spec = importlib.util.spec_from_file_location("_handover_packager", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Credential SHAPES. Matching one only makes a value a CANDIDATE; whether it is
# a real credential is decided by `_is_placeholder`.
PATTERNS = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}"          # OpenAI, Anthropic (sk-ant-api03-…)
    r"|xai-[A-Za-z0-9]{20,}"          # xAI
    r"|gsk_[A-Za-z0-9]{20,}"          # Groq
    r"|AIza[A-Za-z0-9_-]{30,}"        # Google
    r"|gw_live_[A-Za-z0-9]{16,}"      # enterprise gateway
    r"|(?:ASANAK_PASSWORD|ADMIN_PASSWORD|SECRET_KEY)=(\S+)"
)

# Files allowed to carry placeholder-shaped values by design.
SKIP = (".env.example", ".github/workflows/", "scripts/secret_scan.py",
        "scripts/make-handover-zip.py")

TEXT_SUFFIXES = {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".html",
                 ".js", ".css", ".sh", ".cfg", ".ini", ".toml", ".env",
                 ".example", ""}


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def scan():
    is_placeholder = _load_packager()._is_placeholder
    hits = []
    for rel in tracked_files():
        if any(rel.startswith(s) or rel == s for s in SKIP):
            continue
        if pathlib.Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in PATTERNS.finditer(line):
                # For KEY=value shapes judge the value; otherwise the token.
                value = m.group(1) if m.lastindex else m.group(0)
                if is_placeholder(value):
                    continue
                # Never print the value itself — this output goes to a public
                # CI log. The location is enough to find it.
                hits.append(f"{rel}:{n}: a value matching "
                            f"{value[:6]}… does not look like a placeholder")
    return hits


def main():
    hits = scan()
    if not hits:
        print("secret-scan: clean — no credential-looking value in a tracked file")
        return 0
    for h in hits:
        print(f"::error::{h}")
    print(f"\nsecret-scan: {len(hits)} suspicious value(s).\n"
          "If it is a TEST FIXTURE, give it a canonical not-real marker "
          "(sentinel / synthetic / deadbeef / notreal) — see PLACEHOLDER_WORDS "
          "in scripts/make-handover-zip.py. Do NOT add the file to an "
          "exclusion list: that blinds the scanner to everything else in it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
