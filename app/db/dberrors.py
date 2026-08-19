"""Backend-neutral recognition of database constraint failures.

WHY THIS EXISTS
---------------
`app/routers/dataset.py` caught `sqlite3.IntegrityError` to turn a duplicate
dataset id into a clean 4xx. That worked for years and then stopped meaning
anything: production moved to PostgreSQL, where psycopg raises
`psycopg.errors.UniqueViolation` — which is not a `sqlite3` exception and so
sailed straight past the handler. An operator adding an entry whose id already
existed got a 500 and a traceback instead of "این شناسه قبلاً وجود دارد".

The test suite could not see it, because `tests/conftest.py` pins
`DB_BACKEND=sqlite`.

WHY A HELPER AND NOT `if DB_BACKEND == "postgres"` IN THE ROUTER
----------------------------------------------------------------
Because that branch would then have to be repeated at every INSERT in the
application, and each copy is a chance to forget one — which is precisely the
failure being fixed. The backend question is answered once, here.

This is deliberately NOT an ORM and not a new abstraction layer. It is one
predicate over exception types, which is the smallest thing that removes the
backend from the caller's vocabulary.

WHAT IT DOES NOT DO
-------------------
It does not swallow anything. Callers ask "was this a unique violation?" and
re-raise if the answer is no, so an unexpected database fault still surfaces
as a 500 with its traceback rather than being mislabelled a duplicate.
"""
import sqlite3

# psycopg is only installed where PostgreSQL is used. Import defensively so a
# SQLite-only install (tests, the rollback path) does not fail at import.
try:  # pragma: no cover - trivial import guard
    from psycopg import errors as _pg_errors
except Exception:  # noqa: BLE001
    _pg_errors = None


class DuplicateKey(Exception):
    """A row already exists with that key. Backend-neutral."""


def is_unique_violation(exc: BaseException) -> bool:
    """True when `exc` is a UNIQUE / PRIMARY KEY constraint failure.

    Recognises both backends:

    * PostgreSQL — `psycopg.errors.UniqueViolation`, matched by TYPE. psycopg
      maps SQLSTATE 23505 to that class, so this needs no string matching.
    * SQLite — `sqlite3.IntegrityError` covers UNIQUE, NOT NULL, CHECK and
      FOREIGN KEY alike, so the message has to be inspected to avoid reporting
      a NOT NULL failure as a duplicate. SQLite's wording is stable and
      documented: "UNIQUE constraint failed: …".
    """
    if _pg_errors is not None and isinstance(exc, _pg_errors.UniqueViolation):
        return True
    if isinstance(exc, sqlite3.IntegrityError):
        text = str(exc).lower()
        return "unique constraint failed" in text or "not unique" in text
    return False


def is_not_null_violation(exc: BaseException) -> bool:
    """True for a NOT NULL failure. Kept alongside so a caller that wants to
    distinguish the two constraint classes does not have to reach for
    backend-specific types either."""
    if _pg_errors is not None and isinstance(exc, _pg_errors.NotNullViolation):
        return True
    if isinstance(exc, sqlite3.IntegrityError):
        return "not null constraint failed" in str(exc).lower()
    return False
