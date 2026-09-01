# Testing Standard

Binding standard for test depth. Tests validate behavior and system
invariants, not implementation details.

For what exists in this repo today (pytest layout, the
CI-is-the-gate rule, async Playwright rules), see `CLAUDE.md` ->
"Testing" and `POSTGRES_TESTING.md`.

---

## Coverage Per Feature

Every feature considers, at minimum:

1. happy path
2. invalid input
3. authentication failure
4. authorization failure
5. missing resource
6. duplicate request
7. concurrent request
8. regression
9. backward compatibility

Do not only test the happy path.

## Security Boundaries

If a security boundary is involved, test the negative case explicitly:

```text
User A can access Conversation A.

User A cannot access Conversation B.

User B cannot modify Message A.
```

## Behavior Over Implementation

Prefer tests that express business behavior over tests that merely
verify implementation details. A test that must change for every
refactor is testing the wrong thing.

## Honest Verification

Never claim that a check passed unless it was actually run. Clearly
distinguish:

```text
PASSED
FAILED
NOT RUN
NOT APPLICABLE
```
