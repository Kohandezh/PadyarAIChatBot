# Testing against real PostgreSQL

`tests/conftest.py` pins `DB_BACKEND=sqlite`. That keeps the main suite fast and
hermetic, and it is why five production bugs shipped unseen: on SQLite they are
not bugs.

| Escaped bug | Invisible on SQLite because |
| --- | --- |
| `int` written to a BOOLEAN column | SQLite stores 0/1 in a "BOOLEAN" column happily |
| `WHERE enabled = 1` | valid SQLite; `operator does not exist: boolean = integer` on PostgreSQL |
| `json.loads()` on a JSONB value | SQLite returns TEXT; psycopg returns a parsed `dict` |
| `fromisoformat(row["expiry"])` | SQLite returns TEXT; PostgreSQL returns an aware `datetime` |
| `except sqlite3.IntegrityError` | psycopg raises `UniqueViolation`, which is not that type |

`tests/postgres/` closes the gap by talking to a real server.

## Running it

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/postgres -q
```

Without the flag — or with no reachable server — every test in the directory is
**skipped**, not failed. `pytest -q` on its own is unchanged.

`DATABASE_URL` selects the server; it defaults to the same DSN as `app/db/pg.py`
(`postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar`). The role
needs `CREATE` on that database — nothing more.

## Isolation

Nothing here touches the live `app` schema.

1. The session fixture creates two throwaway schemas, `padyar_test_<pid>_<rand>`
   and `..._obs`.
2. It applies the real `migrations/*.sql`, rewriting the hard-coded `app.` and
   `observability.` prefixes to point at those schemas. The migration TEXT is
   the real one — a hand-copied schema would prove nothing about production.
3. The connection pool in `app/db/pg.py` is swapped for one whose
   `search_path` is `<test>,<test_obs>,public`. **`app` is deliberately absent**,
   so a table the harness forgot to create fails loudly instead of quietly
   resolving to the operator's data.
4. Every table is `TRUNCATE ... RESTART IDENTITY CASCADE`d before each test.
5. Both schemas are `DROP ... CASCADE`d at the end, and teardown asserts they
   are gone from `pg_namespace`.

A session-scoped guard counts rows in the live `app.*` tables before and after
the whole run and fails if anything moved; `tests/postgres/test_isolation.py`
asserts the same thing where a reader can see it, and proves a write lands in
the test schema and not in `app.dataset`.

A separate test *database* would be stronger, but `padyar_app` has no CREATEDB
privilege and requiring a superuser DSN to run tests means nobody runs them.

## Files

| File | Covers |
| --- | --- |
| `conftest.py` | schema lifecycle, pool swap, truncation, admin `client` |
| `test_isolation.py` | the guarantees above |
| `test_bug_classes.py` | one test per escaped bug class, plus the aborted-transaction cascade |
| `test_dataset_api.py` | dataset CRUD over HTTP, duplicate → 409, `position` ordering, Persian ids |
| `test_ai_store.py` | provider instances, JSONB config, BOOLEAN columns, models, route targets |
| `test_stt_binding.py` | `stt.resolve()` uses the control-plane secret, not legacy `ai_api_key` |
| `test_admin_pages.py` | Settings → AI and AI Routing, page + API, authenticated |

## House rules

* **Never print a secret.** Assert against a sentinel the test itself chose, or
  against a length. Never log or repr a resolved key.
* **`is True`, not `== True`.** `1 == True` in Python, so equality passes
  against exactly the value these tests exist to reject.
* A test that needs a table the migrations do not create is a bug in the
  migrations, not a reason to create it in the fixture.
