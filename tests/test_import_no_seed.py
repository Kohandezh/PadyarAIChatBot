"""An import must import, never seed.

Measured on the live elecomp install (2026-08-31): running
scripts/import-content.py against a customer database with the default env
squeezed the INOTEX starter dataset (31 `dataset` rows, 193 `questions`,
74 synonyms) AND a default-credential admin row into a database whose owner
never asked for any of it. The content is cross-customer pollution; the
admin row is an account with an auto-generated password nobody can audit.

The fix is two pins inside the script itself, so the protection travels
with the tool and not with the operator's memory:
`SEED_DEFAULT_CONTENT=false`, and `_seed_admin` disabled around init_db().
These tests fail if either pin is removed.
"""
import importlib.util
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
IMPORTER = ROOT / "scripts" / "import-content.py"


@pytest.fixture
def importer():
    """scripts/import-content.py loaded by path — its name has a hyphen, so
    it cannot be imported the normal way."""
    spec = importlib.util.spec_from_file_location("_import_content", IMPORTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _faq_book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["پرسش", "پاسخ"])
    ws.append(["پرسش نمونه؟", "پاسخ نمونه."])
    wb.save(path)
    return str(path)


def _run_apply(importer, monkeypatch, tmp_path):
    """Run the importer's own main() the way the operator does, on a
    throwaway database, with the environment an operator actually has —
    SEED_DEFAULT_CONTENT unset, no admin credentials."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "noseed.db"))
    monkeypatch.delenv("SEED_DEFAULT_CONTENT", raising=False)
    monkeypatch.setattr(importer.sys, "argv", [
        "import-content.py", "--faq", _faq_book(tmp_path / "faq.xlsx"),
        "--apply"])
    assert importer.main() == 0


def test_the_import_leaves_no_starter_dataset_behind(importer, monkeypatch,
                                                     tmp_path):
    _run_apply(importer, monkeypatch, tmp_path)

    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    ids = [r["id"] for r in conn.execute("SELECT id FROM dataset").fetchall()]
    synonyms = conn.execute("SELECT COUNT(*) c FROM synonyms").fetchone()["c"]
    conn.close()

    # Only what the workbook asked for — nothing from app/default_content.
    assert ids == ["faq-01"]
    assert synonyms == 0


def test_the_import_creates_no_admin_account(importer, monkeypatch, tmp_path):
    _run_apply(importer, monkeypatch, tmp_path)

    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    admins = conn.execute("SELECT COUNT(*) c FROM admins").fetchone()["c"]
    conn.close()

    # An admin row with an auto-generated password is an account on the
    # target install that nobody asked for and nobody can audit.
    assert admins == 0
