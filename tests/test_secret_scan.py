"""The secret scanner must catch a real key and must not cry wolf.

WHY THIS FILE MATTERS MORE THAN MOST
------------------------------------
The scanner was RED on this repository for its whole life — seven hits, every
one a test fixture whose job is to look like a live key. A gate that is always
red teaches people to ignore it, which is worse than no gate: it is a gate plus
a false sense of having one.

The two obvious ways to make it green both destroy it. Excluding `tests/`
blinds it to the directory where a pasted key is most likely to land.  Deleting
the patterns stops it catching anything.  So these tests pin BOTH halves: it
still fires on a realistic key anywhere — including inside a test file — and it
stays quiet on the fixtures this project legitimately needs.
"""
import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scripts" / "secret_scan.py"


# A test that proves the scanner rejects real-looking keys has to HAVE
# real-looking keys — and this file is scanned like every other tracked file,
# so a literal here is flagged, correctly. (It was: the first version of this
# file turned CI red, because the scanner did exactly its job.)
#
# Composing the value at runtime is the honest way out. No credential-shaped
# token exists on disk — each prefix is too short to match a shape on its own,
# and each body has no prefix — while the value handed to the scanner is the
# full, realistic thing. The alternative, exempting this file, would blind the
# scanner to a real key pasted into it later.
def _key(prefix: str, body: str) -> str:
    return prefix + body


_BODY_A = "7Kq2mVx9Lp4RtZw8Nb3Yc6Hd1Fg5Js0Aa"
_BODY_B = "9Fk2Lm4Pq7Rt1Vw6Yz3Bc8Nd5Hg0Js"

REALISTIC = {
    "openai":    _key("sk-proj-", _BODY_A),
    "anthropic": _key("sk-ant-api03-", _BODY_B),
    "xai":       _key("xai-", _BODY_B),
    "groq":      _key("gsk_", _BODY_B),
    "google":    _key("AIza", "Sy" + _BODY_B + "4Xp"),
    "gateway":   _key("gw_live_", "9Fk2Lm4Pq7Rt1Vw6Yz"),
}


@pytest.fixture(scope="module")
def scan():
    spec = importlib.util.spec_from_file_location("_secret_scan", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── It stays quiet on the repository as it stands ───────────────────────

def test_the_repository_is_clean():
    """If this fails, either a credential was committed or a new fixture needs
    a canonical not-real marker. Do NOT fix it by excluding the file."""
    result = subprocess.run([sys.executable, str(SCANNER)],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


# ── It fires on values that look real ───────────────────────────────────

@pytest.mark.parametrize("provider", sorted(REALISTIC))
def test_a_realistic_key_is_rejected(scan, provider):
    """`sk-ant-` is the one that motivated widening the shapes: the previous
    class stopped at the first hyphen, so an Anthropic key matched exactly
    three characters and the pattern never fired."""
    value = REALISTIC[provider]
    assert scan.PATTERNS.search(value), f"no shape matched {provider}"
    packager = scan._load_packager()
    assert not packager._is_placeholder(value), f"{provider} judged fake"


def test_a_planted_key_in_a_test_file_is_caught(scan, tmp_path, monkeypatch):
    """Excluding tests/ is the tempting fix and the wrong one. A key pasted
    into a test file must be caught exactly like one in application code."""
    planted = ROOT / "tests" / "_planted_canary_tmp.py"
    planted.write_text(f'K = "{REALISTIC["openai"]}"\n', encoding="utf-8")
    try:
        monkeypatch.setattr(scan, "tracked_files",
                            lambda: ["tests/_planted_canary_tmp.py"])
        hits = scan.scan()
        assert len(hits) == 1
        assert "tests/_planted_canary_tmp.py" in hits[0]
    finally:
        planted.unlink(missing_ok=True)


# ── It stays quiet on values that are obviously not real ────────────────

@pytest.mark.parametrize("value", [
    "sk-livedeadbeefcafe1234",                  # redaction-test fixture
    "sk-controlplane-sentinel-0001",
    "sk-ant-api03-" + "SENTINEL" + "AbCdEfGhIjKlMnOpQrSt",
    "synthetic-not-real-000",
    "sk-your-api-key-here",
])
def test_a_marked_fixture_is_accepted(scan, value):
    """The convention: a synthetic credential carries a canonical not-real
    word. The vocabulary lives in scripts/make-handover-zip.py and is shared,
    not restated — two copies of a security vocabulary drift, and the copy
    that drifts is the one nobody is looking at."""
    assert scan._load_packager()._is_placeholder(value)


def test_the_vocabulary_has_exactly_one_definition(scan):
    """The scanner must not carry its own copy of PLACEHOLDER_WORDS."""
    source = SCANNER.read_text(encoding="utf-8")
    assert "PLACEHOLDER_WORDS = (" not in source
    assert scan._load_packager().PLACEHOLDER_WORDS


def test_the_scanner_never_prints_the_value_it_found(scan, monkeypatch):
    """Its output goes to a public CI log. A scanner that echoes the secret it
    caught has published it more widely than the commit did."""
    planted = ROOT / "tests" / "_planted_canary_tmp2.py"
    secret = REALISTIC["openai"]
    planted.write_text(f'K = "{secret}"\n', encoding="utf-8")
    try:
        monkeypatch.setattr(scan, "tracked_files",
                            lambda: ["tests/_planted_canary_tmp2.py"])
        blob = " ".join(scan.scan())
        assert secret not in blob
        assert secret[8:] not in blob          # not even the distinctive tail
    finally:
        planted.unlink(missing_ok=True)
