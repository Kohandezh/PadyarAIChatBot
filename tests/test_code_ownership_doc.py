"""docs/engineering/CODE_OWNERSHIP.md must describe the code that exists.

Two rules only (docs/features/review-governance/SPEC.md, REQ-017):

1. Every module in MODULES (app/modules/registry.py) has a row, named as a
   backticked registry key. A new module without an ownership row fails CI.
2. Every backticked repo path in the file exists. A renamed or deleted file
   leaves a dead path in the table, and this catches it.

Deliberately NOT tested: the owner column and the human-review column. Only
the product owner fills them, and filling them must never break CI.
"""

import re
from pathlib import Path

from app.modules.registry import MODULES

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "engineering" / "CODE_OWNERSHIP.md"

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_PATH_SUFFIXES = (".py", ".md", ".yml", ".sql", ".sh")


def _tokens():
    return _BACKTICKED.findall(DOC.read_text(encoding="utf-8"))


def _is_path(token: str) -> bool:
    return "/" in token or token.endswith(_PATH_SUFFIXES)


def test_every_registry_module_has_a_row():
    tokens = set(_tokens())
    missing = sorted(name for name in MODULES if name not in tokens)
    assert not missing, f"CODE_OWNERSHIP.md has no row for: {missing}"


def test_every_backticked_path_exists():
    dead = sorted({t for t in _tokens() if _is_path(t) and not (REPO_ROOT / t).exists()})
    assert not dead, f"CODE_OWNERSHIP.md cites paths that do not exist: {dead}"
