# Database Standard

Binding standard for schema and data-access work. In this repo the
database is PostgreSQL 16 in production (SQLite only as the test
backend); schema changes go through `migrations/*.sql` applied by
`scripts/apply_migrations.py`.

---

## Data Integrity

Do not rely exclusively on application code for data integrity. Where
appropriate, enforce invariants using:

- foreign keys
- unique constraints
- check constraints
- indexes
- transactions

Use transactions where multiple writes must remain atomic.

## Migration Safety

Before modifying the schema, understand:

- existing data
- migration safety
- existing consumers
- rollback requirements
- backfill requirements

Never casually perform destructive migrations.

**In this repo:** never edit a migration that has already been applied —
`apply_migrations.py` stores a sha256 of every file and refuses to
continue on checksum mismatch (deploy step 4 aborts). Add a new numbered
file, use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and leave the old
one alone. There is no downgrade path: rolling back means restoring a
backup (`app/services/pg_backup.py`). Mirror test-suite needs in the
SQLite DDL (`app/db/connection.py`).

## Query Performance

Avoid obviously inefficient implementations:

- N+1 queries
- unbounded database reads
- loading entire collections into memory
- repeated expensive computation
- unnecessary serialization
- blocking work inside async request paths
- missing indexes
- excessive external API calls

Any collection endpoint must consider pagination. Do not introduce an
endpoint that assumes the dataset will remain small.
