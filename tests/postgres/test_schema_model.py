"""The static guard's model of the schema, checked against a real database.

`tests/test_sql_boolean_portability.py` decides which columns are BOOLEAN by
replaying `migrations/*.sql` in order. That model is only useful while it
matches what the migrations actually build, and it has already been wrong once:
a CREATE-TABLE-only scan reported `is_duplicate`, which migration 0006 DROPs,
and was blind to every column added by `ALTER TABLE ... ADD COLUMN`.

PostgreSQL is the authority on what those files produce. This asks it, and
fails if the model has drifted. Without this, the static guard could quietly
start protecting a schema that no longer exists.
"""
import pytest


def _model():
    """Boolean columns as `{table: {column, ...}}`, per the static guard."""
    from tests.test_sql_boolean_portability import schema_from, migration_texts

    out = {}
    for table, columns in schema_from(migration_texts()).items():
        names = {c for c, t in columns.items() if t == "BOOLEAN"}
        if names:
            out[table] = names
    return out


def _actual(raw, schemas):
    """Boolean columns as the migrated database reports them."""
    rows = raw.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = ANY(%s) AND data_type = 'boolean'",
        (list(schemas),)).fetchall()
    out = {}
    for table, column in rows:
        out.setdefault(table, set()).add(column)
    return out


def test_the_boolean_columns_match_the_migrated_database(raw, pg_schemas):
    assert _model() == _actual(raw, pg_schemas)


def test_a_column_dropped_by_a_migration_is_really_gone(raw, pg_schemas):
    """The specific drift that was there: 0006 DROPs `is_duplicate`, and the
    old guard still listed it."""
    from tests.test_sql_boolean_portability import boolean_columns

    present = {c for cols in _actual(raw, pg_schemas).values() for c in cols}
    assert "is_duplicate" not in present
    assert "is_duplicate" not in boolean_columns()


def test_the_model_is_not_trivially_empty(raw, pg_schemas):
    """A model that parsed nothing would match an empty query result and pass."""
    actual = _actual(raw, pg_schemas)
    assert actual, "no boolean columns found in the migrated database"
    assert "otp_challenges" in actual and "used" in actual["otp_challenges"]
