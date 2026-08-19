"""Migration 0004's backfill, run for real against a realistic pre-0004state.

WHY THIS EXISTS
---------------
`migrations/0004_dataset_position.sql` gives `dataset` an explicit `position`
so `/api/dataset` can stop ordering by `rowid` — a SQLite pseudo-column that
does not exist in PostgreSQL and made that endpoint 500 in production.

The order it restores was CURATED (overview, date, venue, hours, …) and existed
only as SQLite's insertion sequence. The migration therefore carries it as a
hand-written list, reconstructed once from the pre-migration database. That
list is the only surviving record of the order visitors read, and losing it
would be a silent content regression: no error, no log line, just a reshuffled
knowledge base.

The rest of the PostgreSQL suite applies every migration to an EMPTY schema, so
the backfill runs over zero rows and proves nothing. This module builds a real
pre-0004 state and runs the actual migration text over it.

It deliberately does NOT re-declare the expected positions as constants copied
out of the migration — that would only prove the file can be read twice. It
asserts the ORDER, which is the property that matters, and derives the
curated sequence from the migration file itself.
"""
import os
import re

import psycopg
import pytest

from .conftest import _retarget, dsn, MIGRATIONS_DIR


def _sql(name):
    with open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _curated_order_from_the_migration():
    """The ids in the order migration 0004 assigns them.

    Parsed from the file so the test cannot drift from the migration, and so a
    reordering of that list is detected rather than silently accepted.
    """
    body = _sql("0004_dataset_position.sql")
    block = re.search(r"FROM \(VALUES(.*?)\) AS v\(id, pos\)", body, re.S)
    assert block, "could not find the curated VALUES block in 0004"
    pairs = re.findall(r"\('([^']+)',\s*(\d+)\)", block.group(1))
    assert pairs, "curated VALUES block parsed empty"
    return [pid for pid, _pos in sorted(pairs, key=lambda p: int(p[1]))]


@pytest.fixture
def pre_0004():
    """A throwaway schema holding a realistic PRE-0004 dataset.

    Its own schema, not the suite's: this needs `dataset` WITHOUT `position`,
    which the session fixture has already migrated past.
    """
    schema = f"padyar_m0004_{os.getpid()}_{os.urandom(3).hex()}"
    with psycopg.connect(dsn(), autocommit=True) as adm:
        adm.execute(f'CREATE SCHEMA "{schema}"')
    try:
        opts = f"-c search_path={schema},public"
        with psycopg.connect(dsn(), options=opts) as conn:
            # The REAL 0001 text — `dataset` as it existed before 0004.
            conn.execute(_retarget(_sql("0001_initial.sql"), schema, schema))
            conn.commit()
            cols = [r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema=%s AND table_name='dataset'", (schema,)).fetchall()]
            assert "position" not in cols, "precondition: 0001 must predate `position`"
        yield schema, opts
    finally:
        with psycopg.connect(dsn(), autocommit=True) as adm:
            adm.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            left = adm.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname = %s",
                (schema,)).fetchone()[0]
            assert left == 0, "migration test schema survived teardown"


def _seed(conn, curated):
    """Insert rows so that insertion order, alphabetical order and curated
    order are all DIFFERENT — otherwise the test could pass by coincidence."""
    scrambled = list(reversed(curated))          # insertion != curated
    custom = ["zz-custom-b", "aa-custom-a"]      # alphabetical != insertion
    for pid in scrambled + custom:
        conn.execute("INSERT INTO dataset (id, title, text) VALUES (%s,%s,%s)",
                     (pid, f"T-{pid}", "X"))
    conn.commit()
    return custom


def _order(conn):
    """What the application's read query returns — the same expression as
    `app/routers/public.py`."""
    return [r[0] for r in conn.execute(
        "SELECT id FROM dataset ORDER BY COALESCE(position, 2147483647), id"
    ).fetchall()]


def test_the_fixture_really_is_scrambled_before_the_migration(pre_0004):
    """Guards the test itself: if insertion order already matched curated
    order, or alphabetical order did, everything below would pass for the
    wrong reason.

    `position` does not exist yet here — that is the point of the pre-0004
    state — so the check is on the seeded order and on what the only other
    available ordering (`id`) would give.
    """
    schema, opts = pre_0004
    curated = _curated_order_from_the_migration()
    with psycopg.connect(dsn(), options=opts) as conn:
        custom = _seed(conn, curated)
        by_id = [r[0] for r in conn.execute(
            "SELECT id FROM dataset ORDER BY id").fetchall()]

    assert len(curated) >= 3, "curated list too short to be a meaningful test"
    assert by_id != curated + custom, "alphabetical already equals curated order"
    assert list(reversed(curated)) != curated, "seed order equals curated order"


def test_the_migration_backfills_every_row(pre_0004):
    schema, opts = pre_0004
    curated = _curated_order_from_the_migration()
    with psycopg.connect(dsn(), options=opts) as conn:
        _seed(conn, curated)
        conn.execute(_retarget(_sql("0004_dataset_position.sql"), schema, schema))
        conn.commit()
        missing = conn.execute(
            "SELECT count(*) FROM dataset WHERE position IS NULL").fetchone()[0]
    assert missing == 0


def test_the_curated_order_is_restored(pre_0004):
    """The property that matters: after the migration, the knowledge base reads
    in the order it read before PostgreSQL — not alphabetically."""
    schema, opts = pre_0004
    curated = _curated_order_from_the_migration()
    with psycopg.connect(dsn(), options=opts) as conn:
        custom = _seed(conn, curated)
        conn.execute(_retarget(_sql("0004_dataset_position.sql"), schema, schema))
        conn.commit()
        after = _order(conn)

    assert after[:len(curated)] == curated, "curated order not restored"
    # Rows the curated list does not name land AFTER it, ordered by id.
    assert after[len(curated):] == sorted(custom)
    assert after != sorted(after), "order collapsed to alphabetical"


def test_positions_are_deterministic_across_two_independent_runs(pre_0004):
    """Two installs running this migration must end up with the same order."""
    schema, opts = pre_0004
    curated = _curated_order_from_the_migration()
    with psycopg.connect(dsn(), options=opts) as conn:
        _seed(conn, curated)
        conn.execute(_retarget(_sql("0004_dataset_position.sql"), schema, schema))
        conn.commit()
        first = conn.execute(
            "SELECT id, position FROM dataset ORDER BY id").fetchall()
        # Wipe positions and run it again from the same starting point.
        conn.execute("UPDATE dataset SET position = NULL")
        conn.commit()
        conn.execute(_retarget(_sql("0004_dataset_position.sql"), schema, schema))
        conn.commit()
        second = conn.execute(
            "SELECT id, position FROM dataset ORDER BY id").fetchall()
    assert [tuple(r) for r in first] == [tuple(r) for r in second]


def test_re_running_the_migration_does_not_corrupt_anything(pre_0004):
    """`apply_migrations.py` will not re-run it, but a hand-run must be safe:
    `ADD COLUMN IF NOT EXISTS`, and the backfill only fills NULLs."""
    schema, opts = pre_0004
    curated = _curated_order_from_the_migration()
    with psycopg.connect(dsn(), options=opts) as conn:
        _seed(conn, curated)
        sql = _retarget(_sql("0004_dataset_position.sql"), schema, schema)
        conn.execute(sql)
        conn.commit()
        once = _order(conn)
        conn.execute(sql)          # second application
        conn.commit()
        twice = _order(conn)
    assert once == twice


def test_a_row_added_after_the_migration_sorts_last(pre_0004):
    """The documented contract for a row with no explicit position: it lands at
    the end via the COALESCE fallback, never at the front."""
    schema, opts = pre_0004
    curated = _curated_order_from_the_migration()
    with psycopg.connect(dsn(), options=opts) as conn:
        _seed(conn, curated)
        conn.execute(_retarget(_sql("0004_dataset_position.sql"), schema, schema))
        conn.commit()
        conn.execute("INSERT INTO dataset (id, title, text) VALUES (%s,%s,%s)",
                     ("aaa-added-later", "T", "X"))   # alphabetically FIRST
        conn.commit()
        after = _order(conn)
    assert after[-1] == "aaa-added-later"
    assert after[0] == curated[0]


def test_a_row_added_with_an_explicit_position_sorts_where_it_is_told(pre_0004):
    """The spacing-by-ten exists so an entry can be slotted between two others."""
    schema, opts = pre_0004
    curated = _curated_order_from_the_migration()
    with psycopg.connect(dsn(), options=opts) as conn:
        _seed(conn, curated)
        conn.execute(_retarget(_sql("0004_dataset_position.sql"), schema, schema))
        conn.commit()
        conn.execute(
            "INSERT INTO dataset (id, title, text, position) VALUES (%s,%s,%s,%s)",
            ("zz-slotted", "T", "X", 15))            # between 10 and 20
        conn.commit()
        after = _order(conn)
    assert after[1] == "zz-slotted"
    assert after[0] == curated[0] and after[2] == curated[1]
