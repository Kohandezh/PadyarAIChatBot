"""The REAL deploy/05-create-databases.sh keeps installs out of each other's
databases (work unit F, docs/features/db-maturity/RESEARCH.md 5.8, ADR-028).

One server runs several installs on ONE PostgreSQL cluster: one role and one
database (plus `<database>_drill`) each. PostgreSQL grants CONNECT on every new
database to PUBLIC, so before this unit install A's role could open install
B's database; only the schema permissions stopped it from reading. 05 now
revokes CONNECT from PUBLIC and grants it to the install's own role, for both
databases of the install.

This file runs the script itself, as root, in a throwaway postgres:16
container, for two installs, and then a second time (re-running 05 on an
existing install must be safe). It proves:

  * deny: no install role can connect to the other install's database or its
    drill database; allow: each connects to its own two;
  * each app role still creates, writes and reads in its own `app` and
    `observability` schemas;
  * the databases' ACLs hold no PUBLIC entry, only the install's role;
  * the script makes no role besides the install roles, and grants them no
    predefined role (no pg_read_all_data);
  * the manual path for an install made before this change (the two SQL
    lines in deploy/05-connect-isolation.sql) closes a database that still
    lets PUBLIC connect;
  * the new 05, run over installs the OLD 05 made, closes them too. Every
    other test here starts from databases the new 05 created, so a 05 that
    isolated only the databases it creates would pass them all. The old
    script is 05-create-databases-01183a6.sh next to this file, byte for
    byte `git show 01183a6:deploy/05-create-databases.sh` (CI checks out one
    commit, so the test cannot ask git for it). It runs in its own container.

The container has every tool 05 uses except `sudo`, so a two-line shim maps
`sudo -u postgres ...` (the only way 05 calls it) to the image's own gosu.
Needs Docker; without it the module skips. Containers are named
DEPLOY05_TEST_CONTAINER_PREFIX-* (default padyar-deploy05-test) and removed
with `docker rm -fv`.
"""
import contextlib
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.postgres.conftest import REPO_ROOT as _ROOT

REPO_ROOT = Path(_ROOT)
OLD_05 = Path(__file__).with_name("05-create-databases-01183a6.sh")

PREFIX = os.environ.get("DEPLOY05_TEST_CONTAINER_PREFIX", "padyar-deploy05-test")
IMAGE = "postgres:16"
SUDO_SHIM = ('#!/bin/sh\n'
             '# test shim: 05 only ever runs `sudo -u <user> <command...>`\n'
             '[ "$1" = "-u" ] && { user="$2"; shift 2; exec gosu "$user" "$@"; }\n'
             'exec "$@"\n')
INSTALLS = ("alpha", "beta")
DENY = [("alpha", "padyar_beta"), ("alpha", "padyar_beta_drill"),
        ("beta", "padyar_alpha"), ("beta", "padyar_alpha_drill")]


def _docker(*argv, **kwargs):
    return subprocess.run(["docker", *argv], capture_output=True, text=True,
                          timeout=300, **kwargs)


def _docker_ready() -> bool:
    return bool(shutil.which("docker")) and _docker("info").returncode == 0


class Cluster:
    def __init__(self, name: str, port: int):
        self.name, self.port = name, port

    def sql(self, query: str, db: str = "postgres") -> str:
        """Run SQL as the postgres superuser inside the container."""
        done = _docker("exec", self.name, "psql", "-U", "postgres", "-d", db,
                       "-v", "ON_ERROR_STOP=1", "-tAc", query)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def run_05(self, *slugs: str, script: str = "/deploy/05-create-databases.sh") -> str:
        done = _docker("exec", "-u", "root", self.name, "bash", script, *slugs)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    def host_url(self, container_url: str) -> str:
        """05 prints URLs for 127.0.0.1:5432 inside the server; the test
        reaches the same server through the container's published port."""
        return container_url.replace("@127.0.0.1:5432/", f"@127.0.0.1:{self.port}/")


@contextlib.contextmanager
def _started_cluster():
    if not _docker_ready():
        pytest.skip("Docker is not available: this test runs the real 05 in a container")
    name = f"{PREFIX}-{secrets.token_hex(3)}"
    started = _docker("run", "-d", "--name", name, "-p", "127.0.0.1::5432",
                      "-e", f"POSTGRES_PASSWORD={secrets.token_hex(12)}",
                      "-v", f"{REPO_ROOT / 'deploy'}:/deploy:ro", IMAGE)
    assert started.returncode == 0, started.stderr
    try:
        shim = _docker("exec", "-i", "-u", "root", name, "sh", "-c",
                       "cat > /usr/local/bin/sudo && chmod 755 /usr/local/bin/sudo",
                       input=SUDO_SHIM)
        assert shim.returncode == 0, shim.stderr
        deadline = time.time() + 90
        while time.time() < deadline:
            # Over TCP: the image first starts a short init server on the socket only.
            ready = _docker("exec", name, "pg_isready", "-q", "-h", "127.0.0.1", "-U", "postgres")
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("the PostgreSQL container did not come up within 90 s")
        port = int(_docker("port", name, "5432").stdout.strip().rsplit(":", 1)[-1])
        yield Cluster(name, port)
    finally:
        _docker("rm", "-fv", name)


@pytest.fixture(scope="module")
def cluster():
    with _started_cluster() as started:
        yield started


def _urls(output: str) -> dict:
    """{slug: DATABASE_URL} from 05's credentials banner."""
    found = dict(re.findall(r"^ (\S+)  DATABASE_URL=(\S+)$", output, re.MULTILINE))
    assert set(found) == set(INSTALLS), output
    return found


@pytest.fixture(scope="module")
def installs(cluster):
    """05 run twice for the same two installs. The second run resets the
    passwords (unchanged behaviour), so its URLs are the ones that work."""
    first = cluster.run_05(*INSTALLS)
    second = cluster.run_05(*INSTALLS)
    urls = {slug: cluster.host_url(url) for slug, url in _urls(second).items()}
    return {"cluster": cluster, "first": first, "second": second, "urls": urls}


def _connect(url: str, db: str):
    import psycopg
    return psycopg.connect(re.sub(r"/[^/]+$", f"/{db}", url), connect_timeout=10,
                           autocommit=True)


def _db(slug: str) -> str:
    return f"padyar_{slug}"


def test_the_real_script_runs_twice_on_the_same_installs(installs):
    assert "already exists" not in installs["first"]
    for slug in INSTALLS:
        assert f"database {_db(slug)} already exists" in installs["second"]
        assert f"database {_db(slug)}_drill already exists" in installs["second"]


@pytest.mark.parametrize("who,target", DENY)
def test_no_install_role_can_connect_to_another_installs_databases(installs, who, target):
    import psycopg
    with pytest.raises(psycopg.OperationalError) as refused:
        _connect(installs["urls"][who], target).close()
    assert "permission denied for database" in str(refused.value)


@pytest.mark.parametrize("who,target", [
    ("alpha", "padyar_alpha"), ("alpha", "padyar_alpha_drill"),
    ("beta", "padyar_beta"), ("beta", "padyar_beta_drill"),
])
def test_each_install_role_connects_to_its_own_two_databases(installs, who, target):
    with _connect(installs["urls"][who], target) as conn:
        assert conn.execute("SELECT current_database(), current_user").fetchone() == \
            (target, _db(who))


@pytest.mark.parametrize("slug", INSTALLS)
def test_each_app_role_still_uses_its_own_schemas(installs, slug):
    with _connect(installs["urls"][slug], _db(slug)) as conn:
        for schema in ("app", "observability"):
            table = f"{schema}.isolation_probe_{secrets.token_hex(3)}"
            conn.execute(f"CREATE TABLE {table} (id int PRIMARY KEY, note text)")
            conn.execute(f"INSERT INTO {table} VALUES (1, 'یک')")
            assert conn.execute(f"SELECT note FROM {table}").fetchone() == ("یک",)
            conn.execute(f"DROP TABLE {table}")


@pytest.mark.parametrize("db", ["padyar_alpha", "padyar_alpha_drill",
                                "padyar_beta", "padyar_beta_drill"])
def test_only_the_install_role_may_connect(installs, db):
    """PUBLIC (the `=...` ACL entry) has no CONNECT (`c`); the owning role
    has it. PUBLIC keeps TEMPORARY (`T`), which needs a connection first."""
    role = db.removesuffix("_drill")
    acl = installs["cluster"].sql(f"SELECT datacl::text FROM pg_database WHERE datname = '{db}'")
    entries = acl.strip("{}").split(",")
    assert not any(e.startswith("=") and "c" in e.split("/")[0] for e in entries), acl
    assert any(e.startswith(f"{role}=") and "c" in e.split("=")[1].split("/")[0]
               for e in entries), acl
    for other in ("padyar_alpha", "padyar_beta"):
        if other != role:
            assert installs["cluster"].sql(
                f"SELECT has_database_privilege('{other}', '{db}', 'CONNECT')") == "f"


def test_the_script_makes_no_other_role_and_grants_no_predefined_role(installs):
    cluster = installs["cluster"]
    roles = cluster.sql("SELECT string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles"
                        " WHERE rolname !~ '^pg_' AND rolname <> 'postgres'")
    assert roles == "padyar_alpha,padyar_beta"
    memberships = cluster.sql(
        "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member"
        " WHERE r.rolname IN ('padyar_alpha', 'padyar_beta')")
    assert memberships == "0", "an install role was given another role (pg_read_all_data?)"


def test_the_two_sql_lines_close_an_install_made_before_this_change(installs):
    """An install created by an older 05 still lets PUBLIC connect. The
    documented manual fix is deploy/05-connect-isolation.sql, run as the
    postgres superuser once per database. Before it, another install's role
    gets in; after it, only the install's own role does."""
    import psycopg
    cluster = installs["cluster"]
    password = secrets.token_hex(12)
    cluster.sql(f"CREATE ROLE padyar_gamma WITH LOGIN PASSWORD '{password}'")
    cluster.sql("CREATE DATABASE padyar_gamma OWNER padyar_gamma ENCODING 'UTF8'"
                " TEMPLATE template0")
    try:
        alpha = installs["urls"]["alpha"]
        _connect(alpha, "padyar_gamma").close()   # the old hole, still open here

        applied = _docker("exec", "-i", cluster.name, "psql", "-U", "postgres",
                          "-v", "ON_ERROR_STOP=1", "-v", "db=padyar_gamma",
                          "-v", "role=padyar_gamma",
                          input=(REPO_ROOT / "deploy" / "05-connect-isolation.sql").read_text())
        assert applied.returncode == 0, applied.stderr

        with pytest.raises(psycopg.OperationalError) as refused:
            _connect(alpha, "padyar_gamma").close()
        assert "permission denied for database" in str(refused.value)
        gamma = re.sub(r"//padyar_alpha:[^@]+@", f"//padyar_gamma:{password}@", alpha)
        with _connect(gamma, "padyar_gamma") as conn:
            assert conn.execute("SELECT current_user").fetchone() == ("padyar_gamma",)
    finally:
        cluster.sql("DROP DATABASE IF EXISTS padyar_gamma WITH (FORCE)")
        cluster.sql("DROP ROLE IF EXISTS padyar_gamma")


@pytest.fixture(scope="module")
def upgraded():
    """Two installs made by the OLD 05, then the new 05 run over them, in a
    container of their own. Which cross-install connections worked in between
    is recorded, to show the hole was really open before the new 05 ran."""
    import psycopg
    with _started_cluster() as cluster:
        copied = _docker("cp", str(OLD_05), f"{cluster.name}:/old-05.sh")
        assert copied.returncode == 0, copied.stderr
        old = _urls(cluster.run_05(*INSTALLS, script="/old-05.sh"))
        open_before = set()
        for who, target in DENY:
            try:
                _connect(cluster.host_url(old[who]), target).close()
                open_before.add((who, target))
            except psycopg.OperationalError:
                pass
        new = cluster.run_05(*INSTALLS)
        yield {"new": new, "open_before": open_before,
               "urls": {slug: cluster.host_url(url) for slug, url in _urls(new).items()}}


def test_the_new_script_ran_over_databases_the_old_script_made(upgraded):
    for slug in INSTALLS:
        assert f"database {_db(slug)} already exists" in upgraded["new"]
        assert f"database {_db(slug)}_drill already exists" in upgraded["new"]


@pytest.mark.parametrize("who,target", DENY)
def test_the_new_script_closes_installs_the_old_script_made(upgraded, who, target):
    import psycopg
    assert (who, target) in upgraded["open_before"], "the old 05 should leave this open"
    with pytest.raises(psycopg.OperationalError) as refused:
        _connect(upgraded["urls"][who], target).close()
    assert "permission denied for database" in str(refused.value)
