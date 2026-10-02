# Database Standard

## Ownership

PostgreSQL is the production schema authority. SQLite exists for testing/rollback compatibility where already established.

## Migrations

- Every schema change must be versioned.
- Migrations must be safe to apply in the supported deployment model.
- Avoid destructive migrations without explicit migration/rollback planning.
- Keep application queries and schema changes in the same feature change when the feature requires both.
- Do not document a table or column that is not actually present.

### Destructive migrations: expand, then contract

A deploy rolls back CODE only. It never rolls the database back
(`deploy/padyar-deploy.sh`, step 6). So a migration must never remove
something that the previous release still uses.

- Dropping a column or table ships in its own LATER deploy.
- Before that, an earlier deploy must already run code that no longer reads or
  writes the column or table, and that code must have been stable in production.
- Then a one-deploy code rollback never crosses a drop. The old code still finds
  everything it needs.
- A `git revert` of a deploy that contained a drop does not bring the data back.
  It needs the pre-deploy dump taken in step 1 of `padyar-deploy.sh`. Restore it
  from the admin panel (Infrastructure > Backups).
- Examples of drops already applied: `migrations/0006_lead_status.sql` (column
  `is_duplicate`, table `edit_sessions`), `0013_companies.sql` (table
  `company_profiles`), `0028_drop_pwa_leftovers.sql` (two columns).
  `0029` deletes one settings row (the retired search setting).

## Data Integrity

Database constraints are a final safety boundary, not a replacement for application authorization.

When an operation identifies a row by an operation/resource id, the authorization decision and write must refer to the same row.

## Tests

Schema changes require tests at the level where the real failure would occur. Prefer real database integration tests for authorization, constraints and transactional behavior where mocks could hide the defect.
