# Engineering Constitution

This repository is maintained as a production-grade software system.

Everyone writing code here — human or AI — behaves as a senior software
engineer, not as a code-completion engine.

The goal is not the smallest patch that makes the current request pass.
The goal is the smallest **correct, maintainable, secure, scalable,
system-consistent change**.

This constitution holds the non-negotiable principles. The topic standards
below hold the detail:

| Topic            | File                                   |
| ---------------- | -------------------------------------- |
| API design       | `docs/engineering/API_STANDARDS.md`    |
| Security         | `docs/engineering/SECURITY.md`         |
| Database         | `docs/engineering/DATABASE.md`         |
| Testing          | `docs/engineering/TESTING.md`          |

(`docs/engineering/ARCHITECTURE.md` describes the *current* system
architecture — it is a map, not a standard. `SECURITY_MODEL.md` likewise
describes the current security model.)

The workflow that enforces this constitution (Understand → Architecture →
Implement → Test → Self Review → Verify) lives in `CLAUDE.md` under
"Required Workflow".

---

## 1. Root Cause Over Local Fixes

Never automatically implement a workaround just because it makes the issue
disappear. Prefer fixing the underlying abstraction when reasonably
possible.

Bad approach:

```text
Problem → add another conditional
Problem → add another flag
Problem → add another header
Problem → add another special case
```

Preferred approach:

```text
Problem
  ↓
Understand why the architecture requires the workaround
  ↓
Fix the underlying abstraction
  ↓
Implement the feature using the corrected abstraction
```

Do not accumulate: special cases, client-specific branches, duplicated
business logic, one-off flags, endpoint-specific hacks, duplicated
validation, duplicated authorization logic.

## 2. Repository Consistency

Before creating a new pattern, search the repository. If an existing
pattern is sound, reuse it. New patterns require justification.
Consistency across the codebase is a feature.

## 3. API-First

The API is independent of browser-specific behavior and usable by web,
PWA, native mobile, and future clients. Authentication is explicit
(Bearer). Resource identity is explicit. Authorization is server-side,
per operation. Possession of a resource ID is never proof of
authorization. Detail: `API_STANDARDS.md`.

## 4. No Hidden State

Important application state must not hide inside browser mechanisms
(cookies as API state, implicit frontend state, undocumented headers).
Important identifiers are explicit in API contracts.

## 5. Concurrency and Idempotency

Assume every endpoint can receive concurrent requests and that clients
retry. Design for duplicate requests, race conditions, transaction
boundaries, and lost updates. Retriable operations are evaluated for
idempotency.

## 6. Data Integrity

Database invariants are enforced at the database level where practical
(constraints, transactions), not only in application code. Schema changes
are migration-safe. Detail: `DATABASE.md`.

## 7. Performance Floors

No N+1 queries, no unbounded reads, no loading entire collections into
memory, no blocking work in async request paths. Every collection
endpoint considers pagination. Never assume the dataset stays small.

## 8. Error Handling

Errors are predictable, machine-readable, and safe to expose. Never leak
secrets, tokens, internal credentials, or another user's data.

## 9. Observability

Production behavior must be diagnosable: request ID, route, duration,
status, relevant resource IDs, error classification. Never log access
tokens, passwords, or secrets.

## 10. Backward Compatibility

Before changing an existing API, schema, or shared abstraction, determine
who consumes it (clients, tests, integrations), whether migration is
required, and whether old and new can coexist temporarily. No breaking
consumers without a deliberate migration strategy.

---

## Engineering Judgment

The user's requested implementation approach is not necessarily the
correct architectural approach.

You are allowed to reject the requested implementation approach when it
conflicts with the architecture, security, maintainability, scalability,
or established patterns of the repository.

Preserve the user's intended outcome, not necessarily their proposed
implementation.

If the requested approach is inferior, explain why and implement the
better approach when the intended outcome is clear.

## Avoid Over-Engineering

Do not over-engineer. Senior engineering does not mean maximum
abstraction, maximum architecture, or maximum code.

Prefer the simplest design that correctly satisfies:

- current requirements
- known architectural constraints
- security requirements
- expected scale
- maintainability
- consistency with the existing codebase

Do not introduce abstractions for hypothetical future requirements unless
there is a strong architectural reason. Do not add unnecessary interfaces,
abstraction layers, speculative extension points, premature design
patterns, or infrastructure for hypothetical use cases.

A simple solution is preferred when it is genuinely sufficient. However,
do not choose a superficially simple local workaround when a small
additional amount of design would produce a substantially cleaner
system-wide solution.

The goal is: **minimum necessary complexity, not minimum lines of code.**

---

## Self-Review

After implementation, do not immediately declare success. Review the
complete diff as if reviewing another engineer's pull request:

- Did I solve the root problem, or introduce a workaround?
- Did I duplicate logic or introduce a new pattern unnecessarily?
- Is authorization correct? Race conditions? Retries? Malicious input?
  100x data growth?
- Is the API consistent with the rest of the system? The errors? The
  migrations? The tests?

Fix issues found during this review.

## Definition of Done

A non-trivial task is not complete until the applicable items have been
considered:

- [ ] Architecture reviewed; existing patterns searched; root cause identified
- [ ] Design considered before implementation
- [ ] API contract, authentication, and authorization reviewed
- [ ] Validation and error handling implemented
- [ ] Concurrency and idempotency considered
- [ ] Database integrity, performance, and migration safety considered
- [ ] Security reviewed
- [ ] Tests added/updated; regression and backward compatibility considered
- [ ] Diff self-reviewed
- [ ] Typecheck/lint/tests run where applicable

## Final Principle

When choosing between (A) a small local workaround and (B) a slightly
larger change producing a cleaner system-wide abstraction, prefer B when
the additional complexity is justified.

Do not optimize for fewer changed lines. Optimize for:

**correctness + consistency + security + maintainability + scalability.**

---

## Target State vs Current State

This constitution is the **binding target** for new API work. Some
mechanisms in this codebase predate the standard and are documented
exceptions, not precedents:

- **Admin sessions** use cookie sessions (`admin_sessions` table) rather
  than Bearer tokens.
- **The public chat endpoint** uses an HMAC-signed token injected into the
  page, plus Origin/Referer validation — not an Authorization header.
- **Visitor sign-in** (registration module) and **company edit invites**
  (leads module) use httpOnly cookies with server-side session rows.

New endpoints follow the standard. These legacy mechanisms change only
through a deliberate migration, not by gradual drift.

A skill, doc, or agent instruction that contradicts the code is a bug in
that instruction — or an unstated exception like the ones above must be
written down here. Keep this list current.
