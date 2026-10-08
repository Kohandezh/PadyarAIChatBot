"""deploy/05-connect-isolation.sql, the SQL deploy/05 runs to keep installs
out of each other's databases (work unit F), on the server in DATABASE_URL.

tests/postgres/test_deploy_05_isolation.py runs the whole real 05 script in
its own container (it needs Docker). This file needs no Docker: it applies the
SAME SQL file the script applies, through psql as 05 does, to two throwaway
installs set up the way 05 sets them up (a NOSUPERUSER NOCREATEDB NOCREATEROLE
login role owning `<database>` and `<database>_drill`, with `app` and
`observability` schemas it owns). Then:

  * deny: install A's role cannot connect to B's database or B's drill
    database, and the reverse;
  * allow: each role connects to its own two databases and still creates,
    writes and reads in its own `app` and `observability` schemas;
  * the SQL is safe to run again: a second run changes no ACL.

It needs a superuser in DATABASE_URL (to create roles and databases, and to
change CONNECT on databases another role owns) and the `psql` client; without
either the module skips and says which. CI's postgres:16 service user is one.
"""
import secrets
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from tests.postgres.conftest import REPO_ROOT as _ROOT, dsn

REPO_ROOT = Path(_ROOT)

SQL_FILE = REPO_ROOT / "deploy" / "05-connect-isolation.sql"


def _superuser():
    import psycopg
    with psycopg.connect(dsn(), autocommit=True) as c:
        return c.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
                         ).fetchone()[0]


def _admin():
    import psycopg
    return psycopg.connect(dsn(), autocommit=True)


def _url(role: str, password: str, db: str) -> str:
    parts = urlparse(dsn())
    return parts._replace(netloc=f"{role}:{password}@{parts.hostname}:{parts.port or 5432}",
                          path="/" + db).geturl()


def _apply(db: str, role: str):
    """Exactly how deploy/05 applies it: psql, ON_ERROR_STOP, two variables,
    the file on stdin."""
    return subprocess.run(
        ["psql", dsn(), "-q", "-v", "ON_ERROR_STOP=1", "-v", f"db={db}", "-v", f"role={role}"],
        input=SQL_FILE.read_text(encoding="utf-8"), capture_output=True, text=True,
        timeout=60)


def _make_install(name: str, password: str) -> None:
    """The role, the two databases and the two schemas, as deploy/05 makes them."""
    from psycopg import sql
    with _admin() as c:
        c.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(name), sql.Literal(password)))
        c.execute(sql.SQL("ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
            sql.Identifier(name)))
        for db in (name, name + "_drill"):
            c.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0")
                      .format(sql.Identifier(db), sql.Identifier(name)))
    import psycopg
    parts = urlparse(dsn())
    with psycopg.connect(parts._replace(path="/" + name).geturl(), autocommit=True) as c:
        for schema in ("app", "observability"):
            c.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                sql.Identifier(schema), sql.Identifier(name)))


def _drop_install(name: str) -> None:
    from psycopg import sql
    with _admin() as c:
        for db in (name + "_drill", name):
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(db)))
        c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def _acl(db: str) -> str:
    with _admin() as c:
        return c.execute("SELECT datacl::text FROM pg_database WHERE datname = %s",
                         (db,)).fetchone()[0]


@pytest.fixture(scope="module")
def installs():
    if not shutil.which("psql"):
        pytest.skip("the psql client is not on PATH (deploy/05 applies the SQL with psql)")
    if not _superuser():
        pytest.skip("DATABASE_URL is not a superuser: creating roles and databases and "
                    "changing CONNECT on another role's database need one")
    tag = secrets.token_hex(3)
    made = {}
    try:
        for side in ("a", "b"):
            name, password = f"padyar_isot{tag}{side}", secrets.token_hex(12)
            _make_install(name, password)
            made[side] = SimpleNamespace(name=name, password=password)
        for side in made.values():
            for db in (side.name, side.name + "_drill"):
                done = _apply(db, side.name)
                assert done.returncode == 0, done.stderr
        yield made
    finally:
        for side in made.values():
            _drop_install(side.name)


def _connect(who, db: str):
    import psycopg
    return psycopg.connect(_url(who.name, who.password, db), connect_timeout=10,
                           autocommit=True)


@pytest.mark.parametrize("who,whose,suffix", [
    ("a", "b", ""), ("a", "b", "_drill"), ("b", "a", ""), ("b", "a", "_drill"),
])
def test_one_installs_role_cannot_connect_to_the_others_databases(installs, who, whose,
                                                                  suffix):
    import psycopg
    with pytest.raises(psycopg.OperationalError) as refused:
        _connect(installs[who], installs[whose].name + suffix).close()
    assert "permission denied for database" in str(refused.value)


@pytest.mark.parametrize("who,suffix", [("a", ""), ("a", "_drill"), ("b", ""), ("b", "_drill")])
def test_each_role_connects_to_its_own_databases(installs, who, suffix):
    db = installs[who].name + suffix
    with _connect(installs[who], db) as conn:
        assert conn.execute("SELECT current_database()").fetchone() == (db,)


@pytest.mark.parametrize("who", ["a", "b"])
def test_each_app_role_still_uses_its_own_schemas(installs, who):
    with _connect(installs[who], installs[who].name) as conn:
        for schema in ("app", "observability"):
            table = f"{schema}.isolation_probe"
            conn.execute(f"CREATE TABLE {table} (id int PRIMARY KEY, note text)")
            conn.execute(f"INSERT INTO {table} VALUES (1, 'یک')")
            assert conn.execute(f"SELECT note FROM {table}").fetchone() == ("یک",)
            conn.execute(f"DROP TABLE {table}")


def test_running_the_sql_again_changes_nothing(installs):
    a = installs["a"]
    before = {db: _acl(db) for db in (a.name, a.name + "_drill")}

    for db in before:
        done = _apply(db, a.name)
        assert done.returncode == 0, done.stderr

    assert {db: _acl(db) for db in before} == before
    for acl in before.values():
        # PUBLIC (`=...`) without CONNECT (`c`); its TEMPORARY needs a connection.
        assert not any(e.startswith("=") and "c" in e.split("/")[0]
                       for e in acl.strip("{}").split(",")), acl
