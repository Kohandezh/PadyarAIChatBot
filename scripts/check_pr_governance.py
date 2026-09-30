#!/usr/bin/env python3
"""Check that a pull request description carries the governance sections.

Run by .github/workflows/pr-governance.yml on every pull request. The rules
live in docs/features/review-governance/SPEC.md (REQ-001 to REQ-009, REQ-018) and the
reason the check is advisory is ADR-022 in docs/engineering/DECISIONS.md.

Usage:
    python scripts/check_pr_governance.py --body-file <path> \
        --changed-files <path> [--repo-root <dir>]

Exit codes: 0 pass, 1 problems found, 2 an input file is missing or
unreadable.

The PR body is attacker-controlled text. This script only reads it as a
string: it never executes, evals or shells out with any part of it, and it
only asks whether a cited doc file exists, never what is inside it.

Pure standard library, so the workflow needs no pip install.
"""

import argparse
import os
import re
import sys

IN_SCOPE_PREFIXES = ("app/", "migrations/")

REQUIRED_SECTIONS = ("Root cause", "Design doc", "Tests", "Security", "AI assistance")
# "Human review" is deliberately NOT required. If it were, an agent would
# fill it to turn the check green, which is exactly a faked human review.

MIN_REASON_CHARS = 15

# Comments are stripped from the WHOLE body before it is split into
# sections, so a heading inside a comment does not count. An unclosed comment
# hides the rest of the body on GitHub, so it hides the rest here too, and
# check() says so in its own problem line.
_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_CLOSED_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A doc path stops at whitespace and at the brackets and quotes markdown (or
# Persian text) puts around it, so `[spec](docs/a.md)` and «docs/a.md» both
# yield docs/a.md.
_DOC_PATH = re.compile(r"docs/[^\s()\[\]<>`'\"«»]+")
# ASCII punctuation plus the Persian comma and semicolon.
_TRAILING_PUNCTUATION = ".,;:!?،؛"
_NO_DESIGN = re.compile(r"no design needed:", re.IGNORECASE)


def _in_scope(changed_files) -> bool:
    for path in changed_files or []:
        path = (path or "").strip()
        if path.startswith("./"):
            path = path[2:]
        if path.startswith(IN_SCOPE_PREFIXES):
            return True
    return False


def _normalize(body) -> str:
    return (body or "").replace("\r\n", "\n").replace("\r", "\n")


def _sections(body) -> dict:
    """Map a lower-cased heading to its text, first occurrence wins."""
    text = _HTML_COMMENT.sub("", _normalize(body))
    sections = {}
    current = None
    lines = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if current is not None and current not in sections:
                sections[current] = "\n".join(lines)
            current = line[3:].strip().lower()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None and current not in sections:
        sections[current] = "\n".join(lines)
    return sections


def _is_filled(text: str) -> bool:
    return any(line.strip() for line in text.split("\n"))


def _doc_path_candidates(text: str):
    """Yield (full_path, is_relative) for every docs/...md path in the text.

    full_path keeps any path prefix glued to "docs/" ("../", "./", "/abs/"),
    so a caller can see where the path really points.
    """
    for match in _DOC_PATH.finditer(text):
        path = match.group().rstrip(_TRAILING_PUNCTUATION)
        if not path.endswith(".md"):
            continue
        # Path characters glued to the front: "../", "./", "/abs/", "my".
        prefix = re.search(r"[\w./-]*$", text[:match.start()]).group()
        yield prefix + path, prefix in ("", "./")


def _doc_exists(path: str, repo_root: str) -> bool:
    root = os.path.realpath(repo_root)
    full = os.path.realpath(os.path.join(root, path))
    # A symlink that leads outside the repo does not count as a repo doc.
    if os.path.commonpath([root, full]) != root:
        return False
    return os.path.isfile(full)


def _design_doc_problems(text: str, repo_root: str) -> list:
    """Valid when at least one cited docs/...md path exists, or a
    'No design needed:' reason is long enough. An extra path that does not
    exist, next to a valid one, is not a problem (SPEC REQ-006, REQ-007)."""
    invalid = []
    valid_path = False
    for path, is_relative in _doc_path_candidates(text):
        # "../docs/x.md" and "docs/../x.md" climb out of docs/ or the repo.
        # They never count as a valid doc.
        if ".." in path.split("/"):
            invalid.append(
                f"Design doc: the path {path} must not contain '..' segments.")
            continue
        # "/abs/docs/x.md", a URL, or "mydocs/x.md" is not a repo doc path.
        if not is_relative:
            continue
        if _doc_exists(path, repo_root):
            valid_path = True
        else:
            invalid.append(
                f"Design doc: {path} does not exist in the repository.")

    valid_reason = False
    short_reason = False
    for line in text.split("\n"):
        match = _NO_DESIGN.search(line)
        if not match:
            continue
        if len(line[match.end():].strip()) >= MIN_REASON_CHARS:
            valid_reason = True
        else:
            short_reason = True

    if valid_path or valid_reason:
        return []
    # Nothing valid. Name what was wrong, so the author knows what to fix.
    problems = list(invalid)
    if short_reason:
        problems.append(
            "Design doc: the 'No design needed:' reason must be at least "
            f"{MIN_REASON_CHARS} characters long.")
    if problems:
        return problems
    return [
        "Design doc: give the path of a doc under docs/ (for example "
        "docs/features/<slug>/SPEC.md), or a line 'No design needed: <reason>' "
        f"with a reason of at least {MIN_REASON_CHARS} characters."]


def check(body: str, changed_files: list, repo_root: str) -> list:
    """Return human-readable problems. An empty list means the body passes."""
    if not _in_scope(changed_files):
        return []

    sections = _sections(body)
    problems = []
    if "<!--" in _CLOSED_COMMENT.sub("", _normalize(body)):
        problems.append(
            "Unclosed HTML comment: a '<!--' has no closing '-->', so GitHub "
            "hides everything after it and those sections count as missing.")
    for name in REQUIRED_SECTIONS:
        key = name.lower()
        if key not in sections:
            problems.append(f"Missing section: '## {name}'.")
            continue
        text = sections[key]
        if not _is_filled(text):
            problems.append(
                f"Empty section: '## {name}' has no text outside HTML comments.")
            continue
        if name == "Design doc":
            problems.extend(_design_doc_problems(text, repo_root))
    return problems


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a pull request description for the governance sections.")
    parser.add_argument("--body-file", required=True,
                        help="file holding the PR description")
    parser.add_argument("--changed-files", required=True,
                        help="file with one changed path per line")
    parser.add_argument("--repo-root", default=".",
                        help="repository root used to check doc paths (default: .)")
    args = parser.parse_args(argv)

    try:
        body = _read(args.body_file)
        changed = [line.strip() for line in _read(args.changed_files).splitlines()
                   if line.strip()]
    except OSError as exc:
        print(f"Cannot read input file: {exc}", file=sys.stderr)
        return 2

    problems = check(body, changed, args.repo_root)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("PR governance check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
