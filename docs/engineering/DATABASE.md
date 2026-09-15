# Database Standard

## Ownership

PostgreSQL is the production schema authority. SQLite exists for testing/rollback compatibility where already established.

## Migrations

- Every schema change must be versioned.
- Migrations must be safe to apply in the supported deployment model.
- Avoid destructive migrations without explicit migration/rollback planning.
- Keep application queries and schema changes in the same feature change when the feature requires both.
- Do not document a table or column that is not actually present.

## Data Integrity

Database constraints are a final safety boundary, not a replacement for application authorization.

When an operation identifies a row by an operation/resource id, the authorization decision and write must refer to the same row.

## Tests

Schema changes require tests at the level where the real failure would occur. Prefer real database integration tests for authorization, constraints and transactional behavior where mocks could hide the defect.
