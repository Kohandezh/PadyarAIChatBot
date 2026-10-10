# Security Standard

## Authentication Target

New API authentication should use bearer-style authentication rather than introducing new browser-cookie authentication.

This is a target standard, not a statement that the current codebase is bearer-only.

## Current-State Exceptions

The current PadyarAIChatbot codebase contains established mechanisms including:
- admin cookie sessions;
- HMAC-signed chat tokens;
- visitor/session cookies.

These are current-state mechanisms. They should not be removed or rewritten solely because the target standard is different.

## Rules for New Work

- Do not introduce a new authentication mechanism without an architecture decision.
- Do not bypass an existing security boundary to make a feature easier to implement.
- Understand the full session/token lifecycle before modifying authentication.
- Security-sensitive changes require tests for both allowed and denied paths.
- Prefer server-side ownership of identity and authorization decisions.
- Never authorize one resource and write another resource.
- Treat identifiers used for authorization and identifiers used for persistence as an explicit binding that must be verified.

## Migration

A migration from current mechanisms to bearer-only authentication is separate work unless explicitly included in scope. Migration must define compatibility, rollout, revocation, client behavior and rollback.

## Database Isolation Between Installs

One server runs several installs on one PostgreSQL cluster. Each install has
its own role `padyar_<slug>`, its own database `padyar_<slug>`, and an empty
`padyar_<slug>_drill` for the nightly restore drill.

**Rule: only the install's own role may connect to its two databases.**
PostgreSQL gives `CONNECT` on every new database to `PUBLIC`. Without this
rule, any install's role can open another install's database, and only the
schema permissions stop it from reading. The rule is a second layer on top of
the schema permissions, and it needs no new role.

- `deploy/05-create-databases.sh` applies it to both databases of every
  install it handles, with `deploy/05-connect-isolation.sql`:
  `REVOKE CONNECT ... FROM PUBLIC` and `GRANT CONNECT ... TO padyar_<slug>`.
- The `postgres` superuser can still connect everywhere. The monitoring
  exporter connects to the `postgres` database only, so it is not affected.
- Never create a role with `pg_read_all_data`. It can read every table in
  every database the role may connect to, and on a shared cluster that
  includes other installs (`docs/features/db-maturity/RESEARCH.md`, section
  5.8). If a read-only role is ever needed for a real consumer, it gets
  `CONNECT` on one database and `SELECT` there only.
- Tests: `tests/postgres/test_deploy_05_isolation.py` runs the real 05 in a
  throwaway container; `tests/postgres/test_connect_isolation.py` checks the
  SQL file on the test server.

**Status: not applied on the production server yet.** Its databases were made
before this rule existed, so `PUBLIC` still has `CONNECT` on them. Applying it
is a server change and needs the product owner's approval.

### Applying it to an existing install

The safe path is the two statements, once per database, as the `postgres`
superuser. They change nothing else: no password, no data, no running
connection. The app keeps working, because its own role keeps `CONNECT`.

First check that no other role is connected to the install's databases. The
`postgres` superuser may always connect, so the query leaves it out. An empty
result is the expected one:

```bash
sudo -u postgres psql -tAc "SELECT DISTINCT usename, datname FROM pg_stat_activity
  WHERE datname IN ('padyar_<slug>', 'padyar_<slug>_drill')
  AND usename NOT IN ('padyar_<slug>', 'postgres')"
```

An install made before the restore drill has no `padyar_<slug>_drill`
database yet. Then leave that database out of the steps below. The next run of
05 creates it, with this rule already on.

Then, from the repository checkout:

```bash
for db in padyar_<slug> padyar_<slug>_drill; do
  sudo -u postgres psql -v ON_ERROR_STOP=1 -v db="$db" -v role=padyar_<slug> \
    < deploy/05-connect-isolation.sql
done
```

The file runs its two statements in one transaction. If a name is wrong, psql
prints the error and that database stays exactly as it was.

The same thing by hand, in `sudo -u postgres psql`, also in one transaction:

```sql
BEGIN;
REVOKE CONNECT ON DATABASE padyar_<slug> FROM PUBLIC;
GRANT CONNECT ON DATABASE padyar_<slug> TO padyar_<slug>;
REVOKE CONNECT ON DATABASE padyar_<slug>_drill FROM PUBLIC;
GRANT CONNECT ON DATABASE padyar_<slug>_drill TO padyar_<slug>;
COMMIT;
```

If psql answers `ROLLBACK` instead of `COMMIT`, a line failed and nothing
changed. Fix the name and run all of it again.

Check it with another install's role (`f` means it cannot connect):

```bash
sudo -u postgres psql -tAc \
  "SELECT has_database_privilege('padyar_<other-slug>', 'padyar_<slug>', 'CONNECT')"
```

**A session that was already open stays open.** The `REVOKE` stops new
connections only. It does not end a connection made before it. So run the
first query (the `pg_stat_activity` one) again. If it still returns a row,
that role connected before the change and can stay connected. Do one of these:

- If the role is another install's role (`padyar_<other-slug>`), restart that
  install: `sudo systemctl restart padyar-<other-slug>`. Its old connections
  close, and a new one to this database is refused.
- Otherwise, or if that install must not restart now, end those sessions as
  the `postgres` superuser:

```bash
sudo -u postgres psql -tAc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE datname IN ('padyar_<slug>', 'padyar_<slug>_drill')
  AND usename NOT IN ('padyar_<slug>', 'postgres')"
```

Then run the first query again. Now it must return nothing.

To undo it, if something unexpected needed that access:
`GRANT CONNECT ON DATABASE padyar_<slug> TO PUBLIC;` (and the same for the
drill database).

Re-running `deploy/05-create-databases.sh <slug>` also applies the rule, but
do not use it only for this: **05 also sets a new password for the install's
role.** The old `DATABASE_URL` stops working. If you do run it, put the new
`DATABASE_URL` it prints into `/opt/padyar-<slug>/.env` right away and restart
the app (`sudo systemctl restart padyar-<slug>`), as described in
`docs/engineering/DEPLOYMENT_RUNBOOK.md` ("Existing installs").
