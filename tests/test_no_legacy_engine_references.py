"""Guard: the legacy lexical engine era is closed for good.

The second search engine and its ``search_backend`` setting were removed on
2026-08-28; retrieval has been single-engine ever since. This test fails if
any live surface — code, tests, admin assets, deploy scripts, docs, skills —
grows a reference to the retired engine again, so the removal cannot silently
regress.

Deliberately out of scope (historical record, allowed to mention it):
git history, CHANGELOG.md, applied files under migrations/ (checksum-guarded,
immutable), and this test file itself.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

SCAN_DIRS = (
    "app",
    "scripts",
    "templates",
    "themes",
    "static",
    "deploy",
    "docs",
    ".github",
    ".claude",
    "data/eval",
    "tests",
)
ROOT_TEXT_SUFFIXES = {".md", ".py", ".sh", ".html", ".txt", ".js", ".json", ".toml", ".ini", ".cfg", ".yml", ".yaml"}
ROOT_EXCLUDED = {"CHANGELOG.md"}
SKIP_DIR_NAMES = {"__pycache__", "node_modules", "vendor", ".git", "graphify-out", ".hfcache", "models"}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2", ".ttf", ".otf",
    ".ico", ".pyc", ".gz", ".bin", ".safetensors", ".onnx", ".mp4", ".webm",
}
BANNED = ("tf-idf", "tfidf", "tf idf", "search_backend")


def _iter_files():
    for name in SCAN_DIRS:
        root = REPO_ROOT / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.resolve() == SELF:
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            if SKIP_DIR_NAMES.intersection(path.parts):
                continue
            yield path
    for path in REPO_ROOT.iterdir():
        if (
            path.is_file()
            and path.suffix.lower() in ROOT_TEXT_SUFFIXES
            and path.name not in ROOT_EXCLUDED
        ):
            yield path


def test_no_legacy_engine_references():
    offenders = []
    for path in _iter_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for needle in BANNED:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} -> {needle}")
                break
    assert not offenders, (
        "References to the retired lexical engine found — it must not come back:\n"
        + "\n".join(offenders)
    )
