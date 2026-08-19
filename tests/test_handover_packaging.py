"""The handover archive must never carry a credential.

This archive is built to be handed to another person. It once force-included
`ASANAK-CREDENTIALS.env` — the live Asanak username, password, API key and
`SECRET_KEY` — through the `KEEP_NAMES` allowlist, and the same file was also
listed in `SECRET_ALLOW`, so the script's own "refuse to build if a credential
is present" guard had been told to ignore it. Two allowlists pointing at one
file, and therefore no alarm.

The rule these tests hold down is an ORDERING one, which is the part that is
easy to regress: **a credential denial is evaluated before any allowlist**, so
no future KEEP_NAMES entry can readmit a secret.

Nothing here touches the real credential files; every case is synthetic.
"""
import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make-handover-zip.py"


@pytest.fixture(scope="module")
def mod():
    """Load the hyphenated script as a module."""
    spec = importlib.util.spec_from_file_location("make_handover_zip", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── The four named files ────────────────────────────────────────────────

def test_the_env_template_is_included(mod):
    """The recipient needs it to configure their own install."""
    assert mod.denied(".env.example") is False
    assert mod.is_credential_file(".env.example") is False


@pytest.mark.parametrize("rel", [
    ".env",
    "ASANAK-CREDENTIALS.env",
    "ADMIN_CREDENTIALS.txt",
])
def test_live_credential_files_are_excluded(mod, rel):
    assert mod.is_credential_file(rel) is True
    assert mod.denied(rel) is True


def test_the_asanak_file_is_no_longer_force_included(mod):
    """The specific regression: it used to sit in KEEP_NAMES."""
    assert "ASANAK-CREDENTIALS.env" not in mod.KEEP_NAMES


def test_the_secret_scanner_is_no_longer_blind_to_the_asanak_file(mod):
    """It was ALSO in SECRET_ALLOW, so even the build-refusal guard skipped it.

    Fixing only KEEP_NAMES would have left the scanner willing to wave the
    file through if anyone ever readmitted it.
    """
    assert not mod.SECRET_ALLOW.match("ASANAK-CREDENTIALS.env")


# ── Nested and relocated copies ─────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "docs/.env",
    "backup/old/.env",
    "deploy/ASANAK-CREDENTIALS.env",
    "a/b/c/ADMIN_CREDENTIALS.txt",
    "handover/copy/.env.production",
])
def test_nested_copies_are_excluded_too(mod, rel):
    """Matching is by basename precisely so a moved copy cannot escape.
    The previous rule was a path regex anchored near the repo root."""
    assert mod.denied(rel) is True


# ── Shapes nobody enumerated ────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    ".env.local",
    ".env.production",
    ".env.backup.20260819_120000",
    "SMSIR-CREDENTIALS.env",
    "KAVENEGAR_CREDENTIALS.env",
    "prod.credentials",
    "ADMIN_CREDENTIALS.txt.bak",
])
def test_credential_shaped_variants_are_excluded(mod, rel):
    """A future provider's credential file must lose by default, not by
    someone remembering to add it to a list."""
    assert mod.denied(rel) is True


# ── Harmless lookalikes must NOT be swept up ────────────────────────────
# Over-denial is its own failure: it silently ships a broken archive, and the
# person who notices is the recipient.

@pytest.mark.parametrize("rel", [
    ".env.example",
    "tests/test_admin_credentials_file.py",
    "app/services/secure_store.py",
    "docs/engineering/SECURITY_MODEL.md",
    "app/auth/csrf.py",
    "static/admin/js/settings_sms.js",
    "scripts/make-handover-zip.py",
])
def test_harmless_files_that_merely_mention_credentials_still_ship(mod, rel):
    assert mod.is_credential_file(rel) is False


def test_an_allowlist_entry_cannot_readmit_a_credential(mod, monkeypatch):
    """The ordering property, asserted directly.

    Someone adding a credential back into KEEP_NAMES — the exact mistake that
    caused this — must no longer be able to ship it.
    """
    monkeypatch.setattr(mod, "KEEP_NAMES",
                        {".env.example", "ASANAK-CREDENTIALS.env", ".env"})
    assert mod.denied("ASANAK-CREDENTIALS.env") is True
    assert mod.denied(".env") is True
    assert mod.denied(".env.example") is False      # the real template survives


# ── End to end, against a real ZIP ──────────────────────────────────────

def test_a_built_archive_contains_no_credential_file(mod, tmp_path, monkeypatch):
    """Build a real archive from a synthetic tree and inspect its members."""
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "nested" / "deep").mkdir(parents=True)

    # Files that must ship.
    (root / ".env.example").write_text("OPENAI_API_KEY=sk-your-api-key-here\n")
    (root / "app.py").write_text("print('hello')\n")
    (root / "docs" / "guide.md").write_text("# guide\n")

    # Files that must NOT ship. Synthetic values — not the real secrets.
    (root / ".env").write_text("SECRET_KEY=synthetic-not-a-real-key-000\n")
    (root / "ASANAK-CREDENTIALS.env").write_text(
        "ASANAK_PASSWORD=synthetic-not-real-000\nSECRET_KEY=synthetic-000\n")
    (root / "ADMIN_CREDENTIALS.txt").write_text("Password: synthetic-000\n")
    (root / "nested" / "deep" / ".env").write_text("SECRET_KEY=synthetic-000\n")
    (root / "nested" / "ASANAK-CREDENTIALS.env").write_text("ASANAK_API_KEY=x000\n")

    monkeypatch.setattr(mod, "ROOT", root)
    files = mod.collect(include_git=False)

    out = tmp_path / "handover.zip"
    with zipfile.ZipFile(out, "w") as z:
        for rel in files:
            z.write(root / rel, arcname=rel)

    members = set(zipfile.ZipFile(out).namelist())

    assert ".env.example" in members
    assert "app.py" in members
    assert "docs/guide.md" in members

    for forbidden in (".env", "ASANAK-CREDENTIALS.env", "ADMIN_CREDENTIALS.txt",
                      "nested/deep/.env", "nested/ASANAK-CREDENTIALS.env"):
        assert forbidden not in members, f"{forbidden} leaked into the archive"

    # And nothing whose basename looks like a credential file, however it got in.
    assert not [m for m in members if mod.is_credential_file(m)]
