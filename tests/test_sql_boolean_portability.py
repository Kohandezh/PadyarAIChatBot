"""Static guard: a BOOLEAN column is never compared to an integer literal.

WHY STATIC AND NOT A POSTGRESQL TEST
------------------------------------
This suite runs on SQLite by design (`tests/conftest.py` pins
`DB_BACKEND=sqlite`), and on SQLite `used = 1` is not a bug. TRUE is just an
alias for 1, so the statement is correct and the test passes. Production is
PostgreSQL 16, which has no `boolean = integer` operator and answers with
`UndefinedFunction`. That gap is how `update_profile()` shipped a
`WHERE ... AND used = 1` that turned every `POST /api/auth/profile` into a 500.

`tests/postgres/` closes the gap by talking to a real server, and
`tests/postgres/test_otp_profile.py` pins that specific bug there. But that
directory is opt-in (`RUN_POSTGRES_TESTS=1`) and skips on a machine with no
PostgreSQL, so it catches this only for whoever remembers to run it. This check
needs no server, runs on every default `pytest`, and covers the whole of `app/`
rather than the code paths a test happens to exercise.

WHY THE SCHEMA IS REPLAYED RATHER THAN GREPPED
----------------------------------------------
An earlier version of this file grepped `migrations/*.sql` for
`name BOOLEAN` at the start of a line. That only ever sees CREATE TABLE, and it
was wrong in both directions: it still reported `is_duplicate`, which migration
0006 DROPs, and it would not have seen a BOOLEAN column added by
`ALTER TABLE ... ADD COLUMN` at all. Columns in this project arrive exactly that
way (0006 adds five of them), so the blind spot covered the normal case.

So the migrations are applied IN ORDER to an in-memory model of the schema:
CREATE TABLE, ALTER ADD COLUMN, ALTER DROP COLUMN and DROP TABLE all move it.
A guard whose picture of the schema is wrong is a guard that will eventually be
wrong about something that matters.

`tests/postgres/test_schema_model.py` checks this model against a real migrated
database, so the two cannot drift apart silently.
"""
import ast
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")
APP_DIR = os.path.join(REPO_ROOT, "app")

# Constraint clauses share the comma-separated body with real columns.
_NOT_A_COLUMN = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT",
                 "EXCLUDE", "LIKE", "DEFERRABLE"}

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)\s*\(", re.I)
_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.\"]+)\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\"]+)\s+([\w]+)", re.I)
_DROP_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.\"]+)\s+"
    r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?([\w\"]+)", re.I)
_DROP_TABLE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.\"]+)", re.I)


def _name(raw: str) -> str:
    """`app."Foo"` -> `foo`. Schema prefixes are dropped: this model is keyed on
    bare table names, and the migrations use exactly one schema per table."""
    return raw.replace('"', "").split(".")[-1].lower()


def _balanced(text: str, open_paren: int) -> str:
    """Body of the parenthesised group whose `(` sits at `open_paren`."""
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
    raise ValueError("unbalanced parentheses in migration")


def _split_top_level(body: str):
    """Split on commas that are not inside parentheses, so `NUMERIC(12, 6)`
    and `CHECK (x IN ('a','b'))` stay in one piece."""
    depth, current = 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            yield "".join(current)
            current = []
        else:
            current.append(ch)
    if current:
        yield "".join(current)


def _columns_of(body: str) -> dict:
    columns = {}
    for part in _split_top_level(body):
        tokens = part.split()
        if len(tokens) < 2 or tokens[0].upper() in _NOT_A_COLUMN:
            continue
        # `NUMERIC(12,` -> `NUMERIC`; only the bare type name matters here.
        columns[_name(tokens[0])] = tokens[1].split("(")[0].upper()
    return columns


def schema_from(sql_texts) -> dict:
    """Replay migration texts in order. Returns `{table: {column: TYPE}}`."""
    tables = {}
    for raw in sql_texts:
        sql = _COMMENTS.sub(" ", raw)
        events = []
        for match in _CREATE_TABLE.finditer(sql):
            events.append((match.start(), "create", match))
        for match in _ADD_COLUMN.finditer(sql):
            events.append((match.start(), "add", match))
        for match in _DROP_COLUMN.finditer(sql):
            events.append((match.start(), "drop_col", match))
        for match in _DROP_TABLE.finditer(sql):
            events.append((match.start(), "drop_table", match))
        # Position order is statement order, which is what makes an ADD
        # followed by a later DROP of the same column come out right.
        for _pos, kind, match in sorted(events, key=lambda e: e[0]):
            table = _name(match.group(1))
            if kind == "create":
                body = _balanced(sql, match.end() - 1)
                tables.setdefault(table, {}).update(_columns_of(body))
            elif kind == "add":
                tables.setdefault(table, {})[_name(match.group(2))] = \
                    match.group(3).upper()
            elif kind == "drop_col":
                tables.get(table, {}).pop(_name(match.group(2)), None)
            elif kind == "drop_table":
                tables.pop(table, None)
    return tables


def migration_texts():
    for entry in sorted(os.listdir(MIGRATIONS_DIR)):
        if entry.endswith(".sql"):
            with open(os.path.join(MIGRATIONS_DIR, entry), encoding="utf-8") as fh:
                yield fh.read()


def boolean_columns() -> set:
    """Names of every column that is BOOLEAN in the schema the migrations build."""
    return {column
            for columns in schema_from(migration_texts()).values()
            for column, type_name in columns.items()
            if type_name == "BOOLEAN"}


# ── The scan ────────────────────────────────────────────────────────────

def _sql_strings(path: str):
    """Every string literal in a module, with the line it starts on.

    `ast` is used rather than a line-by-line grep for two reasons. It joins
    implicitly concatenated literals, which is how this codebase writes
    multi-line SQL and how the original bug hid: the offending `used = 1` sat
    on a different source line from the `UPDATE` that gave it meaning. And it
    ignores Python code and `#` comments, so an ordinary assignment such as
    `enabled = 1` is not mistaken for SQL.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):  # f-string: check the literal parts
            parts = "".join(v.value for v in node.values
                            if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if parts:
                yield node.lineno, parts


def _python_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _offence(columns):
    """`used = 1`, `enabled != 0`, `active <> 1`, and the reversed `1 = used`."""
    names = "|".join(sorted(columns))
    return re.compile(
        rf"\b(?:{names})\s*(?:=|!=|<>)\s*[01]\b"
        rf"|\b[01]\s*(?:=|!=|<>)\s*(?:{names})\b", re.I)


def test_no_boolean_column_is_compared_to_an_integer_literal():
    pattern = _offence(boolean_columns())
    offenders = []
    for path in _python_files(APP_DIR):
        for lineno, text in _sql_strings(path):
            found = pattern.search(text)
            if found:
                offenders.append(
                    f"{os.path.relpath(path, REPO_ROOT)}:{lineno}: {found.group(0)!r}")

    assert not offenders, (
        "BOOLEAN column compared to an integer literal. Valid SQLite, but "
        "PostgreSQL raises UndefinedFunction (no boolean = integer operator). "
        "Use TRUE/FALSE, or the bare column name:\n  " + "\n  ".join(offenders))


# ── The schema model ────────────────────────────────────────────────────

def test_the_real_migrations_produce_the_expected_boolean_columns():
    """Guard the guard: a parsing regression would make the scan vacuous."""
    columns = boolean_columns()
    assert "used" in columns          # otp_challenges, migration 0001
    assert "enabled" in columns       # ai_provider_instances, migration 0003
    assert "active" in columns        # lead_visitors, migration 0005
    assert len(columns) >= 8, columns


def test_a_dropped_column_is_not_reported():
    """0006 DROPs `company_leads.is_duplicate`. A guard that still believes in
    it has the wrong schema, and the same blindness hides live columns."""
    assert "is_duplicate" not in boolean_columns()


def test_a_boolean_added_by_alter_is_seen():
    """The blind spot that matters. New columns in this project arrive by
    ALTER, so a CREATE-TABLE-only scan misses the normal case."""
    schema = schema_from([
        "CREATE TABLE app.thing (id TEXT PRIMARY KEY);",
        "ALTER TABLE app.thing ADD COLUMN IF NOT EXISTS is_ready BOOLEAN NOT NULL DEFAULT FALSE;",
    ])
    assert schema["thing"]["is_ready"] == "BOOLEAN"


def test_order_decides_the_outcome():
    """Added then dropped is absent; dropped then re-added is present."""
    added_then_dropped = schema_from([
        "CREATE TABLE app.t (id TEXT);",
        "ALTER TABLE app.t ADD COLUMN flag BOOLEAN;"
        " ALTER TABLE app.t DROP COLUMN flag;",
    ])
    assert "flag" not in added_then_dropped["t"]

    dropped_then_added = schema_from([
        "CREATE TABLE app.t (id TEXT, flag BOOLEAN);",
        "ALTER TABLE app.t DROP COLUMN flag;"
        " ALTER TABLE app.t ADD COLUMN flag BOOLEAN;",
    ])
    assert dropped_then_added["t"]["flag"] == "BOOLEAN"


def test_dropping_a_table_takes_its_columns_with_it():
    schema = schema_from([
        "CREATE TABLE app.gone (id TEXT, flag BOOLEAN);",
        "DROP TABLE IF EXISTS app.gone;",
    ])
    assert "gone" not in schema


def test_constraints_and_parenthesised_types_do_not_become_columns():
    schema = schema_from([
        "CREATE TABLE app.t ("
        " id TEXT PRIMARY KEY,"
        " cost NUMERIC(12, 6),"
        " flag BOOLEAN NOT NULL DEFAULT FALSE,"
        " status TEXT CHECK (status IN ('a', 'b')),"
        " UNIQUE (id, status),"
        " FOREIGN KEY (id) REFERENCES app.other(id)"
        ");",
    ])
    assert schema["t"] == {"id": "TEXT", "cost": "NUMERIC",
                           "flag": "BOOLEAN", "status": "TEXT"}


def test_a_comment_mentioning_a_drop_does_not_move_the_schema():
    """0006's header prose talks about what it destroys. Prose is not DDL."""
    schema = schema_from([
        "CREATE TABLE app.t (id TEXT, flag BOOLEAN);",
        "-- DROP TABLE app.t and ALTER TABLE app.t DROP COLUMN flag are described\n"
        "/* DROP TABLE app.t */\n"
        "SELECT 1;",
    ])
    assert schema["t"]["flag"] == "BOOLEAN"


# ── The matcher ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "UPDATE otp_challenges SET job = ? WHERE id = ? AND used = 1",
    "SELECT id FROM ai_provider_instances WHERE enabled = 1",
    "SELECT 1 FROM ai_provider_models WHERE supports_tools <> 0",
    "SELECT 1 FROM ai_provider_instances WHERE has_secret != 1",
])
def test_the_check_would_have_caught_the_original_bug(sql):
    """A guard nobody has seen fail is a guard nobody trusts."""
    assert _offence(boolean_columns()).search(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE otp_challenges SET job = ? WHERE id = ? AND used = TRUE",
    "SELECT id FROM ai_provider_instances WHERE enabled",
    "UPDATE otp_challenges SET attempts = 0 WHERE id = ?",   # INTEGER column
    "UPDATE ai_circuit_state SET failure_count = 0 WHERE id = ?",
])
def test_the_check_does_not_fire_on_portable_or_integer_sql(sql):
    assert _offence(boolean_columns()).search(sql) is None
