# Coding Workflow Standard

Every task follows the same five phases. Do not skip ahead.

---

## PHASE 1 — UNDERSTAND

Read:

- relevant source files
- existing tests
- database schema
- related API endpoints
- existing patterns

Do not modify files.

---

## PHASE 2 — ARCHITECTURE

Explain:

1. Current architecture
2. Root cause of the problem
3. Existing pattern that should be reused
4. Proposed design
5. Security implications
6. Failure modes
7. Migration/backwards compatibility concerns
8. Tests required

Do not modify files.

---

## PHASE 3 — IMPLEMENT

Only after the architecture is clear:

- implement the change
- reuse existing abstractions
- avoid duplicated logic
- add/update tests

---

## PHASE 4 — SELF REVIEW

Review the diff as a senior engineer.

Ask:

- Would I approve this PR?
- Did I introduce a special case?
- Did I solve the root problem?
- Is this consistent with the rest of the codebase?
- What happens under concurrency?
- What happens with malicious input?
- What happens when the client retries?
- What happens when data grows 100x?

Fix issues found.

---

## PHASE 5 — VERIFY

Run:

- typecheck
- lint
- relevant tests
- full regression suite when appropriate

Report exactly what passed and what was not run.
