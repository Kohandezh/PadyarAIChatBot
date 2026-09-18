# PostgreSQL integration tests (`tests/postgres/`)

You are probably here because the suite skipped and printed:

```
RUN_POSTGRES_TESTS is not set — real-PostgreSQL tests are opt-in
(see docs/engineering/POSTGRES_TESTING.md)
```

That message comes from `tests/postgres/conftest.py:79`. This file explains
what the suite is, how to run it, and why it is built the way it is.

Everything below was checked against `tests/postgres/conftest.py`,
`.github/workflows/ci.yml` and `docker-compose.yml` on 2026-09-19.

## Why this suite exists

`tests/conftest.py:24` pins `DB_BACKEND=sqlite` for the whole process. That
keeps the main suite fast and hermetic, and it means a developer needs no
database server to run it.

Production is PostgreSQL 16. So the default suite tests the app on a database
it never runs on.

Five classes of bug have already reached production through that gap. The list
is not invented here, it is the one in `tests/postgres/conftest.py:6-9`:

1. **int-for-boolean writes.** SQLite happily stores `0`/`1` in a column
   declared `BOOLEAN`. PostgreSQL rejects it.
2. **`enabled = 1` comparisons.** Valid SQLite. A 500 on PostgreSQL.
3. **`json.loads()` on an already-parsed JSONB value.** psycopg returns JSONB
   as a native Python `dict`. `json.loads(dict)` raises `TypeError`.
4. **TIMESTAMPTZ returned as a `datetime` but compared as a string.**
   `datetime.fromisoformat()` on an already-parsed datetime raises. This is the
   one that 500'd every admin request right after the PostgreSQL cutover.
5. **`sqlite3.IntegrityError` never matching psycopg's `UniqueViolation`.** An
   `except sqlite3.IntegrityError` block catches nothing on PostgreSQL, so a
   handled 409 becomes an unhandled 500.

None of these were visible to a SQLite test, because on SQLite they are not
bugs. `tests/postgres/test_bug_classes.py` has one narrow test per class. Each
fails against the pre-fix code and passes against the fix. The point is not
"PostgreSQL works", it is "this specific SQLite-ism is still gone".

There is a sixth thing the suite pins that is worth naming: on PostgreSQL a
failed statement **aborts the whole transaction** until you roll back, so one
handled 4xx can cascade into a run of 500s on later queries and on the next
connection out of the pool. See
`tests/postgres/test_bug_classes.py:205` and `:231`.

## Why it is opt-in

The suite only runs when `RUN_POSTGRES_TESTS=1` is set. Two things that
protects:

- **A developer with no PostgreSQL still gets a green run.** Without the flag
  these tests skip. They do not fail and they do not error.
- **A CI job that has not opted in still gets a green run.** Only the
  `postgres-tests` job sets the flag, so the plain `test` job is unaffected.

The gate is `_skip_reason()` (`tests/postgres/conftest.py:72-92`). It skips
for two separate reasons, and says which:

- the flag is not set, or
- the flag is set but the server at `DATABASE_URL` does not answer a
  `SELECT 1` within 5 seconds.

Both are skips, never errors.

There is one extra guard. `pytest_ignore_collect`
(`tests/postgres/conftest.py:104-119`) refuses to even **import** these modules
when `psycopg` is missing. `test_migration_0004_backfill.py` imports psycopg at
module level, so on a runner without the driver pytest would report a
collection **error**, which is a red build, before any skip marker was
consulted. That is exactly what once turned CI red.

That ignore is scoped to the missing driver and nothing else, on purpose.
Ignoring whenever `_skip_reason()` was non-empty would also swallow the
ordinary "you forgot the flag" case, and then the whole suite would silently
vanish from the report instead of announcing itself as skipped. A suite you
cannot see is a suite you forget to run.

## Running it locally

### 1. Start a PostgreSQL 16 server

Any PostgreSQL 16 server works. The tests only need a DSN that answers.

**A caveat about `docker-compose.yml`.** It does define a `db` service on
`postgres:16-alpine` with user `padyar_app`, password `padyar_local_dev` and
database `padyar`, which is where the default DSN comes from. But that service
publishes **no host port**:

```bash
grep -n "ports" docker-compose.yml
# → only the padyar app service, "8000:8000". The db service has no ports block.
```

It is reachable from the `padyar` container as `db:5432` on the compose
network, and not from your machine. So `docker compose up -d db` alone does
**not** give you `127.0.0.1:5432`, and the suite will skip with "PostgreSQL
unreachable at DATABASE_URL".

The simplest fix is a throwaway container with the port mapped, using the same
image and credentials CI uses:

```bash
docker run --rm -d --name padyar-pg \
  -e POSTGRES_USER=padyar_app \
  -e POSTGRES_PASSWORD=padyar_local_dev \
  -e POSTGRES_DB=padyar \
  -p 5432:5432 \
  postgres:16-alpine

# wait until it answers
docker exec padyar-pg pg_isready -U padyar_app -d padyar
```

Alternatively add a `ports: ["5432:5432"]` block to the compose `db` service,
or point `DATABASE_URL` at any PostgreSQL 16 you already run. Nothing in the
suite cares which you pick.

### 2. Create the live schemas once

The suite assumes a dev-machine-shaped database: the `app` and `observability`
schemas exist and the migrations have been applied into them. This is what
`deploy/05-create-databases.sh` does on a real server, and what CI does in its
"Provision live schemas" step.

```bash
export DATABASE_URL=postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar
psql "$DATABASE_URL" -c 'CREATE SCHEMA IF NOT EXISTS app; CREATE SCHEMA IF NOT EXISTS observability;'
.venv/bin/python scripts/apply_migrations.py
```

If you already run the app locally against PostgreSQL, this is done.

### 3. Run the suite

```bash
RUN_POSTGRES_TESTS=1 \
DATABASE_URL=postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar \
  .venv/bin/python -m pytest tests/postgres -q
```

### Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `RUN_POSTGRES_TESTS` | Yes | The opt-in flag. Anything other than empty, `0`, `false` or `no` counts as on (`conftest.py:69`) |
| `DATABASE_URL` | No | The server to connect to. Defaults to `postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar` (`conftest.py:63-65`), the same default as `app/db/pg.py` and `scripts/apply_migrations.py` |

The DSN shape is a standard libpq URL:

```
postgresql://<user>:<password>@<host>:<port>/<database>
```

`psycopg[binary]` and `psycopg-pool` come from `requirements.txt`, not from
`requirements-dev.txt`. They are the production driver, not a test extra. If
they are missing the suite skips itself rather than failing.

### Counting the tests

Do not trust a number written in a document. Ask pytest:

```bash
.venv/bin/python -m pytest tests/postgres --collect-only -q | tail -2
```

It reported 122 on 2026-09-19. Other files in the repo still quote older
counts. The command is the source of truth.

## Isolation: what stops a test writing into your real data

This is the part to read before adding a test.

Your local database holds real content, a real provider instance, real admins
and real usage history. Nothing in this suite may touch it. Four mechanisms
enforce that.

### 1. Two throwaway schemas per session

`pg_schemas` (`conftest.py:161-194`) creates two schemas inside the configured
database, named from the process id plus random hex:

```
padyar_test_<pid>_<rand>
padyar_test_<pid>_<rand>_obs
```

It applies the **real** `migrations/*.sql` text into them, then drops both with
`CASCADE` at the end of the session. Teardown asserts against `pg_namespace`
that the schemas are gone, not merely emptied.

The migration files hard-code `app.` and `observability.` prefixes, because in
production there is exactly one of each. `_retarget()` (`conftest.py:139-153`)
rewrites those prefixes and strips `BEGIN`/`COMMIT` (each file is run inside a
transaction here, exactly as `scripts/apply_migrations.py` does it). Rewriting
the prefix is the only way to apply the real migration text somewhere safe, and
applying the real text is the whole point. A test against a hand-copied schema
proves nothing about production.

### 2. `app` is deliberately not on the test `search_path`

The search path is `<test_app_schema>,<test_obs_schema>,public`. The real `app`
schema is **not** on it.

So a table this harness forgot to create fails loudly with "relation does not
exist" instead of silently resolving to your live table. The failure mode is a
red test, not a corrupted database.

### 3. A session-wide before/after row-count guard

`_live_data_is_untouched` (`conftest.py:197-223`) is `autouse` and session
scoped. It counts rows in the live `app.*` tables before the session and again
after, and fails the session if anything moved:

```
app.dataset, app.questions, app.synonyms, app.settings, app.admins,
app.ai_provider_instances, app.ai_provider_models, app.ai_routes,
app.ai_route_targets
```

`chat_logs` and the observability tables are deliberately **not** in that list.
They would move on their own if you happen to have the app running, and a
guard that goes off for innocent reasons is a guard people learn to ignore.

`tests/postgres/test_isolation.py` asserts the same property where a reader can
see it, including a mid-run cross-check so a leak can be attributed to the test
that caused it.

### 4. Why schemas and not a separate database

A separate database would be stronger. It is not used because the `padyar_app`
role has no `CREATEDB` privilege, and requiring a superuser DSN just to run
tests would mean nobody runs them. Schema isolation needs only what the app's
own role already has. This is a deliberate trade, written down at
`conftest.py:30-32`.

## The fixtures

| Fixture | Scope | What you get |
|---|---|---|
| `pg_schemas` | session | `(app_schema, obs_schema)`, the two throwaway schema names. Creates, migrates and drops them |
| `_live_data_is_untouched` | session, autouse | The before/after row-count guard described above. You never request it directly, except in `test_isolation.py` where it is asserted on |
| `pg_pool` | session | Swaps `app.db.pg._pool` for a `ConnectionPool` bound to the test schemas, and restores the original afterwards |
| `_table_list` | session | The list of tables in the two test schemas, used by `pg_clean` |
| `pg_clean` | function, autouse | Truncates every test table, points `config.DB_BACKEND` at `postgres` for the test, and resets the AI store cache. Runs for every test in this directory |
| `conn` | function | A connection through the **real** adapter (`app/db/pg.py`). Use this by default |
| `raw` | function | A raw psycopg connection on the test schemas, bypassing the adapter |
| `client` | function | A `TestClient` with an authenticated admin session and a CSRF header, talking to the test schemas |

Some details that matter when you write a test.

**`pg_pool` patches a module global rather than reconfiguring the pool.**
`pool()` hard-codes `search_path=app,observability,public` as a connection
option, and it must, because that is what production needs. Swapping the module
global is the smallest seam that redirects the whole application, since every
call site goes through `get_db_connection()` → `pg.connect()` → `pool()`
(`conftest.py:232-237`).

**`pg_clean` uses TRUNCATE, not DELETE.** `TRUNCATE ... RESTART IDENTITY
CASCADE` restarts identity sequences, so a test that asserts on a generated id
is not silently coupled to how many rows ran before it. It then re-inserts the
two routable tasks (`chat`, `classify`) that migration 0003 seeds, because
TRUNCATE removed them and route tests need the foreign-key target to exist
(`conftest.py:291-296`).

**`pg_clean` also flips the backend.** The root conftest pins
`DB_BACKEND=sqlite` for the whole process. Every reader does a late
`from app.config import DB_BACKEND`, so patching the module attribute is enough,
and monkeypatch undoes it per test.

**Prefer `conn` over `raw`.** These tests are about what the application sees,
and the adapter is where placeholder translation, `Row` and `lastrowid`
emulation live. Reach for `raw` only when you must bypass the adapter, for
example to ask "what type did this column actually get?".

**`client` still redirects `DB_PATH`.** `init_db()` runs unconditionally at
startup and is SQLite-only, so without the redirect it would write into your
real `chat_history.db` (`conftest.py:344-353`).

**`client` writes an aware UTC expiry.** `admin_sessions.expiry` is
TIMESTAMPTZ, so PostgreSQL reads a naive datetime in its own timezone. On a
machine four hours behind UTC, a naive-local "now + 1 hour" landed three hours
in the past and every admin request 401'd with "Session expired" before the
test body ran (`conftest.py:372-378`). Copy the aware-UTC pattern if you write
a similar fixture.

## How CI runs it

The `postgres-tests` job in `.github/workflows/ci.yml:72-121` is **blocking**.
It has no `continue-on-error`, so a failure here fails the build. That is the
point: the five bug classes above run on every push and every pull request,
instead of only on a developer machine that happens to have a local server.

What the job does:

1. Starts a `postgres:16-alpine` **service container** with the same image and
   the same credentials as `docker-compose.yml`. It carries the same
   `pg_isready` health check, because without it the first connection races the
   container's `initdb` and the job fails on a bootstrap race rather than on a
   defect.
2. Sets `RUN_POSTGRES_TESTS: "1"` and
   `DATABASE_URL: postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar`
   for the whole job.
3. Installs `requirements.txt` and `requirements-dev.txt`.
4. Provisions the **live** schemas, the same way
   `deploy/05-create-databases.sh` does on a server: creates `app` and
   `observability`, then runs `python scripts/apply_migrations.py`. The service
   container starts empty, and the suite's isolation probes need a live
   `app.dataset` to prove the harness never writes there.
5. Runs `pytest tests/postgres -q`.

Check a run:

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

## Adding a test here

Put a test in `tests/postgres/` only when it needs behaviour SQLite cannot
reproduce: a real type (BOOLEAN, JSONB, TIMESTAMPTZ), a real error class
(`UniqueViolation`, `NotNullViolation`), real transaction abort semantics, real
identity columns, or real connection-pool behaviour. Everything else belongs in
the fast SQLite suite.

Rules to follow:

- Use `conn`, not `raw`, unless you are deliberately bypassing the adapter.
- Never write a schema-qualified `app.something` in a test. It will hit the
  live schema, and the session guard will fail the whole run.
- If you add a table, add it through a real migration in `migrations/`. The
  harness applies the real files, so a table that only exists in
  `app/db/connection.py` will not be there and you will get "relation does not
  exist".
- Keep each test narrow enough to name the bug class it pins.
