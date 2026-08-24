"""Static guard: a BOOLEAN column never meets an integer.

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

THE THREE SHAPES THAT ARE CHECKED
---------------------------------
The integer does not have to be written next to the column. All three of these
raise on PostgreSQL and pass on SQLite:

    WHERE used = 1                          a literal comparison
    VALUES (?, ?, 1, ?)                     a literal in an INSERT
    SET active = ?     ... (1 if x else 0)  an integer BOUND as a parameter

The third is why this file reads the Python and not just the strings: the
integer and the column it lands in are in different arguments of the same call.
psycopg adapts a Python `int` to `integer`, and PostgreSQL answers
`column "active" is of type boolean but expression is of type integer`. The
first two are found by matching the SQL text; the third by pairing each `?` with
the column it binds to and looking at the matching element of the parameter
tuple.

WHAT THIS DOES NOT SEE
----------------------
The parameter check only reads a call whose SQL string AND parameter tuple are
both written out at the call site. That is a deliberate stop. SQL assembled at
runtime is the other common shape here (`app/services/ai/store.py` builds
`sets`/`params` side by side in a loop), and following a value from an
`append()` into a placeholder position needs real dataflow analysis. Guessing
instead would put false positives on correct code, and a guard that cries wolf
gets deleted by the third person who hits one.

So the parameter check is silent on:

  * SQL built at runtime (joined fragments, f-strings) or passed by variable
  * a parameter tuple passed by variable, built by comprehension, or splatted
  * a parameter whose value is a name, an attribute or any call other than
    `int()`. `flag` may well be an int, but only dataflow could say so
  * `executemany()`, whose parameters are a sequence of sequences
  * a placeholder in neither recognised position: `SET x = CASE WHEN ? ...`,
    a multi-row `VALUES (...), (...)` past the first row, a subquery
  * an integer that reaches the database through a wrapper rather than through
    `execute()` directly

A green run of this file is not proof that no integer reaches a boolean column.
It is proof that none does it in one of the three shapes above. The real
backstop is `tests/postgres/`, against a real server.
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


# ── Which column does a value land in? ──────────────────────────────────

_ASSIGNED = re.compile(r"\b(\w+)\s*(?:=|!=|<>)\s*(\?)")
# `[^()]` on both lists: a nested paren means the parse is off, and a match
# that stops early would pair values with the wrong columns.
_INSERT = re.compile(
    r"INSERT\s+INTO\s+[\w.\"]+\s*\(([^()]*)\)\s*VALUES\s*\(([^()]*)\)", re.I)
_VALUE_TOKEN = re.compile(r"[^,\s)]+")


def _blank_literals(sql: str) -> str:
    """Blank the inside of quoted strings, keeping every offset.

    A `?` inside Persian question text is not a parameter. `app/db/pg.py`
    skips those when it rewrites `?` to `%s`, and a scan that counted them
    would pair every later value with the wrong column.
    """
    out, quote = [], None
    for ch in sql:
        if quote:
            out.append(ch if ch == quote else " ")
            if ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            out.append(ch)
    return "".join(out)


def _value_bindings(blanked: str):
    """`(column, offset of the value)` for every value bound to a named column.

    Two shapes are read, both of which name the column outright:
    `SET flag = ?` / `WHERE used = ?`, and an INSERT column list paired
    positionally with its VALUES list. Anything else yields nothing, and
    nothing is never an offence.
    """
    for match in _ASSIGNED.finditer(blanked):
        yield _name(match.group(1)), match.start(2)
    for match in _INSERT.finditer(blanked):
        columns = [c.strip() for c in match.group(1).split(",")]
        values = list(_VALUE_TOKEN.finditer(match.group(2)))
        # Different lengths means the two lists were not read correctly, so
        # position tells us nothing. Drop the statement rather than guess.
        if len(columns) != len(values):
            continue
        for column, value in zip(columns, values):
            yield _name(column), match.start(2) + value.start()


def _placeholder_columns(sql: str) -> list:
    """The column each `?` binds to, in statement order, `None` where unknown.

    The list is as long as the statement has placeholders, which is what lets
    it be zipped against the parameter tuple at the call site.
    """
    blanked = _blank_literals(sql)
    index_of = {pos: i for i, pos in
                enumerate(i for i, ch in enumerate(blanked) if ch == "?")}
    columns = [None] * len(index_of)
    for column, offset in _value_bindings(blanked):
        if offset in index_of:
            columns[index_of[offset]] = column
    return columns


def _inserted_integers(sql: str, booleans: set):
    """Integer literals written straight into a BOOLEAN column by an INSERT.

    `VALUES (?, ?, ?, 1, ?)` is the same bug as `active = 1` with the `=` moved
    somewhere the comparison pattern cannot see it.
    """
    blanked = _blank_literals(sql)
    for column, offset in _value_bindings(blanked):
        token = _VALUE_TOKEN.match(blanked, offset)
        if column in booleans and token and token.group(0) in ("0", "1"):
            yield f"{column} <- {token.group(0)}"


# ── Integers bound as parameters ────────────────────────────────────────

def _integer_valued(node) -> bool:
    """True when the expression can only produce a Python int.

    `1`, `1 if x else 0`, `int(x)`. psycopg adapts each of those to `integer`.
    A name or an attribute is not judged: knowing what `flag` holds needs
    dataflow, and a guess there is a false positive waiting to happen.
    """
    if isinstance(node, ast.Constant):
        # `True` is an int to `isinstance`, and it is the correct value here.
        return isinstance(node.value, int) and not isinstance(node.value, bool)
    if isinstance(node, ast.IfExp):
        # Either branch is enough: the bad one still runs on its own days.
        return _integer_valued(node.body) or _integer_valued(node.orelse)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id == "int"
    return False


def _static_execute_calls(path: str):
    """`(lineno, sql, parameter elements)` for every `execute(sql, params)`
    whose SQL string and parameter tuple are both spelled out at the call site.

    Everything else is skipped on purpose. See WHAT THIS DOES NOT SEE.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.keywords or len(node.args) != 2:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "execute":
            continue
        sql, params = node.args
        if not (isinstance(sql, ast.Constant) and isinstance(sql.value, str)):
            continue
        if not isinstance(params, (ast.Tuple, ast.List)):
            continue
        if any(isinstance(element, ast.Starred) for element in params.elts):
            continue        # a splat destroys the positions this relies on
        yield node.lineno, sql.value, params.elts


def _bound_integer_offences(path: str, booleans: set):
    for lineno, sql, elements in _static_execute_calls(path):
        columns = _placeholder_columns(sql)
        # A mismatch means the SQL and the tuple are not the pair they look
        # like, so no position can be trusted.
        if len(columns) != len(elements):
            continue
        for column, element in zip(columns, elements):
            if column in booleans and _integer_valued(element):
                yield lineno, column


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


def test_no_boolean_column_is_given_an_integer_literal_by_an_insert():
    """`VALUES (?, ?, 1, ?)` is the same bug with the `=` taken away."""
    booleans = boolean_columns()
    offenders = []
    for path in _python_files(APP_DIR):
        for lineno, text in _sql_strings(path):
            for offence in _inserted_integers(text, booleans):
                offenders.append(
                    f"{os.path.relpath(path, REPO_ROOT)}:{lineno}: {offence}")

    assert not offenders, (
        "Integer literal INSERTed into a BOOLEAN column. PostgreSQL raises "
        "DatatypeMismatch: is of type boolean but expression is of type "
        "integer. Write TRUE/FALSE:\n  " + "\n  ".join(offenders))


def test_no_boolean_column_is_bound_to_an_integer_parameter():
    """The blind spot the SQL text cannot show: the integer is in the parameter
    tuple, the column name is in the query, and only the pair is wrong."""
    booleans = boolean_columns()
    offenders = []
    for path in _python_files(APP_DIR):
        for lineno, column in _bound_integer_offences(path, booleans):
            offenders.append(
                f"{os.path.relpath(path, REPO_ROOT)}:{lineno}: {column} = ?")

    assert not offenders, (
        "Integer bound to a BOOLEAN column. psycopg sends it as integer and "
        "PostgreSQL raises DatatypeMismatch; SQLite accepts it, which is why "
        "the rest of this suite stays green. Bind bool(...):\n  "
        + "\n  ".join(offenders))


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


def test_no_column_name_is_boolean_in_one_table_and_something_else_in_another():
    """Every check here keys on the bare column name, so `active` being BOOLEAN
    in one table and INTEGER in another would make `active = 0` look like a bug
    wherever it appeared. That is not true today. If this ever fails, the checks
    have to learn which table a statement touches before the new column lands.
    """
    types = {}
    for columns in schema_from(migration_texts()).values():
        for column, type_name in columns.items():
            types.setdefault(column, set()).add(type_name)
    clashing = {c: t for c, t in types.items() if "BOOLEAN" in t and len(t) > 1}
    assert not clashing, clashing


# ── Integers in an INSERT ───────────────────────────────────────────────

def test_an_integer_literal_in_a_values_list_is_caught():
    """`app/services/leads.py` created every visitor with `active` = 1. The
    comparison pattern cannot see it: there is no `=` next to the column."""
    sql = ("INSERT INTO lead_visitors (id, name, code, active, created_at)"
           " VALUES (?, ?, ?, 1, ?)")
    assert list(_inserted_integers(sql, boolean_columns())) == ["active <- 1"]


@pytest.mark.parametrize("sql", [
    # TRUE in the boolean position, which is the fix.
    "INSERT INTO lead_visitors (id, name, code, active, created_at)"
    " VALUES (?, ?, ?, TRUE, ?)",
    # 0 lands in `attempts`, an INTEGER column, where it belongs.
    "INSERT INTO otp_challenges (id, attempts, used) VALUES (?, 0, FALSE)",
    # Lists of different lengths: the parse is not trustworthy, so say nothing.
    "INSERT INTO lead_visitors (id, active) VALUES (?, ?, 1)",
])
def test_a_sound_insert_is_left_alone(sql):
    assert list(_inserted_integers(sql, boolean_columns())) == []


# ── Integers bound as parameters ────────────────────────────────────────

_BOUND_INT = '''
def revoke(conn, visitor_id, active):
    conn.execute("UPDATE lead_visitors SET active = ? WHERE id = ?",
                 (1 if active else 0, visitor_id))
'''

_BOUND_BOOL = '''
def revoke(conn, visitor_id, active):
    conn.execute("UPDATE lead_visitors SET active = ? WHERE id = ?",
                 (bool(active), visitor_id))
'''

_BOUND_INT_IN_INSERT = '''
def create(conn, visitor_id, active):
    conn.execute("INSERT INTO lead_visitors (id, active) VALUES (?, ?)",
                 (visitor_id, int(active)))
'''

# Whether `flag` holds an int cannot be answered without dataflow, so this
# is a MISS, and the docstring says so. Pinned here so the miss stays
# deliberate rather than becoming a surprise.
_BOUND_VIA_NAME = '''
def revoke(conn, visitor_id, flag):
    conn.execute("UPDATE lead_visitors SET active = ? WHERE id = ?",
                 (flag, visitor_id))
'''

# The runtime-assembled shape from app/services/ai/store.py. Also a miss.
_BOUND_DYNAMICALLY = '''
def update(conn, instance_id, enabled):
    sets, params = [], []
    sets.append("enabled = ?")
    params.append(1 if enabled else 0)
    conn.execute("UPDATE ai_provider_instances SET " + ", ".join(sets)
                 + " WHERE id = ?", params + [instance_id])
'''


def _offences_in(tmp_path, source: str) -> list:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    return list(_bound_integer_offences(str(path), boolean_columns()))


@pytest.mark.parametrize("source", [_BOUND_INT, _BOUND_INT_IN_INSERT])
def test_an_integer_bound_to_a_boolean_column_is_caught(tmp_path, source):
    """The shape that started this: `1 if active else 0` in the parameter
    tuple, `active = ?` in the SQL, and neither half wrong on its own."""
    assert [column for _lineno, column in _offences_in(tmp_path, source)] == ["active"]


def test_binding_bool_is_the_fix_and_passes(tmp_path):
    assert _offences_in(tmp_path, _BOUND_BOOL) == []


@pytest.mark.parametrize("source", [_BOUND_VIA_NAME, _BOUND_DYNAMICALLY])
def test_the_documented_blind_spots_stay_silent(tmp_path, source):
    """Not a pass mark. These are the cases WHAT THIS DOES NOT SEE lists, and
    they are here so nobody mistakes silence for coverage."""
    assert _offences_in(tmp_path, source) == []


@pytest.mark.parametrize("sql,expected", [
    ("UPDATE lead_visitors SET active = ? WHERE id = ?", ["active", "id"]),
    ("INSERT INTO lead_visitors (id, active) VALUES (?, ?)", ["id", "active"]),
    # A `?` in Persian text is not a parameter, and counting it would shift
    # every column after it by one.
    ("UPDATE dataset SET title = 'چطور؟' WHERE used = ?", ["used"]),
    ("UPDATE t SET x = CASE WHEN ? THEN 1 END", [None]),
])
def test_each_placeholder_is_matched_to_its_column(sql, expected):
    assert _placeholder_columns(sql) == expected
