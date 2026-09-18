---
name: code-review
description: >
  Review submitted code changes like a senior engineer on this repo: Python/FastAPI correctness,
  authorization, SQL safety, PostgreSQL migrations, pytest coverage, Persian/RTL UI, and the
  simplicity bar, with severity-prioritized findings. Use whenever the user asks for a code review,
  a PR review, a security-focused review, or wants feedback on changes before merging.
---

# Code Review

Code review is the gate before any PR merges into this repo. Every scoped PR passes through a review after the `commit` skill and before merge. For stacked PRs, review each slice on its own, bottom slice first. The `merge-stacks` skill covers merge order.

This skill is the working method. The diff under review is the source of truth, not the broader codebase. **Scope the review to the PR's own root cause.** Unrelated code in the same files is out of scope unless the PR touched it.

The repo's own documents decide what "correct" means here, in this order:

- `docs/engineering/ENGINEERING_CONSTITUTION.md` (the binding principles)
- `docs/engineering/API_STANDARDS.md` (endpoints, resource binding, error shape, required tests)
- `docs/engineering/SECURITY.md` (auth target, current-state exceptions, rules for new work)
- `docs/engineering/DATABASE.md` (PostgreSQL is the schema authority, migration safety)
- `docs/engineering/TESTING.md` (what to test, and that CI is the merge signal)
- `CLAUDE.md` and `AGENTS.md` (module system, pipeline, thresholds, STOP CONDITIONS)

## The process

### 1. Establish the exact diff

Decide BASE and HEAD before reading a single file:

```bash
BASE_SHA=$(git merge-base origin/main HEAD)
HEAD_SHA=$(git rev-parse HEAD)
git diff --stat "$BASE_SHA".."$HEAD_SHA"
git diff "$BASE_SHA".."$HEAD_SHA"
```

The default branch is `main`. For a stacked PR, use the parent slice's tip as BASE instead of `origin/main`. `gh stack view` shows the chain and which branch sits below this one.

Review only what the diff shows. Do not change the working tree, the index, HEAD, or branch state during a review. Inspect with `git diff`, `git show`, and `git log` only.

### 2. Read the PR description

Understand the stated root cause before judging the code. A deviation from the stated intent is a finding. A change in the diff that the description never mentions is a yellow flag worth naming.

### 3. Check CI, do not re-run the suite

CI on GitHub is the pass/fail signal. Do not run the full pytest suite on this machine to judge a PR: 15 tests always fail here (they need a live PostgreSQL or network) and always pass on CI. Read the real result instead:

```bash
gh pr checks <number>          # per-job status for the PR
gh run list --branch <branch> --limit 1
```

The blocking jobs are `test`, `postgres-tests`, `secret-scan`, and `identity-guard`. `dependency-audit` is advisory (`continue-on-error: true`), so a finding there is a note, not a blocker. A red blocking job is a Critical finding by itself.

### 4. Assess against these disciplines

Work through each one. Every finding needs a file:line reference, what is wrong, why it matters, and how to fix it.

#### Authorization and access control

This is the most common source of security regressions here. The install runs on a shared booth browser, so **"the next person inherits the last person's state" is the default bug**. Check:

1. **Admin endpoints** carry `Depends(verify_admin)` from `app/auth/security.py`, either as a parameter (`_=Depends(verify_admin)`) or in the router's `dependencies=[...]`. A missing dependency is a security bug, not a style nit. Fail closed.
2. **Every endpoint authenticates and authorizes on its own.** Never infer authorization from possession of a resource id. A `/v/{code}` or `/edit/{token}` style link is a credential for that one resource, and nothing else.
3. **Resource binding.** The row that is authorized must be the row that is read or written. Authorizing on one identifier and persisting with another is a Critical finding (`docs/engineering/API_STANDARDS.md`, "Resource Binding").
4. **Chat endpoints keep the trio**: the HMAC chat token, the `Origin`/`Referer` allowlist, and the per-IP rate limit (`CHAT_RATE_LIMIT` per `CHAT_RATE_WINDOW`, default 20 per 60s). A change must not weaken or skip any of the three.
5. **Session lifetime.** Admin sessions slide on a 1-hour timeout. Visitor sessions have both an inactivity window and a hard cap. A change that lets a session outlive either bound on a kiosk is Critical.
6. **New auth mechanisms** need an architecture decision first (`docs/engineering/SECURITY.md`). A hand-rolled token or cookie scheme inside a route handler is a finding.

#### Input validation

- Request bodies are Pydantic models in `app/models.py`, not raw dicts pulled out of `await request.json()`.
- Query and path parameters are bounded and coerced where they reach the database (see the `limit`/`offset` clamps in `app/routers/conversations_admin.py` for the shape used here).
- Visitor text goes through `app/utils/normalizer.py` wherever the retrieval pipeline expects normalized Persian.
- Uploaded files are validated by magic bytes and size, not by the filename or the client's declared type.

#### SQL and database access

- **Parameterized SQL always.** The app writes `?` placeholders with a params tuple. `app/db/pg.py` translates them to `%s` for PostgreSQL. Any SQL built with an f-string, `%` formatting, or `+` on user input is a Critical finding.
- Production is **PostgreSQL 16** (schemas `app` and `observability`). SQLite is the test backend and a rollback artifact only. A review comment that says "SQLite" about production is wrong.
- Reads go through `app/db/queries.py` or the owning service, not ad hoc SQL spread across routers.
- **Unbounded collections must paginate.** A `SELECT` over a growing table (`chat_logs`, `conversations`, `sms_messages`) with no `LIMIT` is a finding, whatever the current row count is.
- **Migrations:** a schema change is a new numbered file in `migrations/`, applied by `scripts/apply_migrations.py`, and mirrored in `init_db()` in `app/db/connection.py` for the SQLite test backend. **An edit to an already-applied migration is always Critical.** The script stores a sha256 per file, prints `REFUSING TO CONTINUE`, exits 2, and aborts the next deploy. The fix is a new file using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
- There is no downgrade path. A destructive migration with no backup plan is Critical (`app/services/pg_backup.py` is the rollback route).

#### Secrets

- No API keys, passwords, or tokens in code, logs, responses, or templates. They come from the environment, and secrets at rest go through `app/services/secure_store.py` (Fernet `enc:` tokens).
- Nothing secret is printed, returned in an error body, or rendered into a template. The `secret-scan` CI job is blocking, but it is a net, not a substitute for reading the diff.

#### Architecture and the module rule

- **Every feature is a module** defined in `app/modules/registry.py`, optional (`is_core=False`) unless every customer needs it. A feature bolted onto an unrelated router is a finding.
- Layering holds: route wiring in `app/routers/`, business logic in `app/services/`, data access in `app/db/`. Logic that leaks into a route handler, or a router importing another router's internals, is a finding.
- **A business rule has one implementation.** The same validation or authorization copied into a second handler is a finding, even when both copies are correct today.
- Reuse what exists (`get_setting(key, default)`, `app/utils/normalizer.py`, the module registry, the theme partials, the `settings` table) instead of a parallel system.
- Watch for the repo's STOP CONDITIONS in the diff: a new special-case conditional, a flag added to paper over an abstraction, a bypassed validation, a second way to do something that already has one.

#### Tests

- New behavior needs a test that fails when the fix is removed (`docs/engineering/TESTING.md`). A new route handler with no test is a finding.
- Integration tests use FastAPI's `TestClient`. Security-sensitive changes test the denied path as well as the allowed one.
- Fixtures belong in the existing `tests/conftest.py`. A second top-level `conftest.py` is a finding.
- **Browser tests must use Playwright's async API**: `async def test_...` with `async_playwright()`. The sync `page`/`browser`/`context` fixtures are banned, and `tests/test_suite_isolation.py` fails the suite if one appears. One sync browser test once turned 15 failures into 141.
- A skipped or `xfail` test added by the PR without a written reason is a finding.
- Anything touching the schema or a constraint needs coverage in `tests/postgres/`, which runs against a real PostgreSQL 16 and is blocking.

#### User-facing changes: Persian, RTL, and the simplicity bar

The product rule outranks style: the app must be usable by anyone, from a child to an elderly person.

- Every screen is understandable in under 3 seconds, every action doable in under 3 clicks, and no jargon reaches user-facing text.
- Persian strings render correctly right to left, the Vazirmatn font is in use, and direction is not broken by a new wrapper element.
- New copy is Persian for visitors and admins alike. Mixed Persian and English in one sentence, or an untranslated technical term, is a finding.
- A settings toggle for something the code can decide by itself is a finding (`CLAUDE.md`, Simplicity Rules).
- A visible behavior change that has no browser test is a finding, per `docs/engineering/TESTING.md`.

#### Performance and resources

- The BM25 and embedding indexes are built once per reindex. Rebuilding an index, reloading the dataset, or re-reading a model inside a chat request is a Critical performance finding (`app/services/search.py`).
- AI calls have timeouts, run only on the fallback tiers, and degrade to a local answer or a rephrase prompt on failure. An eager or unbounded model call in the hot path is a finding.
- No leaked connections, no unclosed files, no timers or listeners left behind in `static/chat/core.js`.

#### Functionality and edge cases

- Does the code do what the description says?
- Are empty, `None`, and malformed values handled? Are error paths deliberate rather than a bare `except: pass` that hides a defect?
- Are there boundary conditions the code silently mishandles?

### 5. Assign severity

- **Critical**: security hole, data loss, auth bypass, an edited applied migration, SQL built from user input, a broken user path, a red blocking CI job. Must be fixed before merge.
- **Important**: missing test for new behavior, duplicated business rule, unbounded query, hand-rolled auth, unvalidated input, a feature that skipped the module registry. Should be fixed before merge.
- **Minor**: naming, a magic number, dead or commented-out code, a doc line now out of date. Note it. It does not block merge.

Not every issue is Critical. Over-severity destroys trust in the whole review.

### 6. Deliver the review

**Strengths.** What is done well, with file:line. Accurate praise makes the rest credible.

**Issues**, grouped Critical / Important / Minor. Each one:

- `file:line`
- what is wrong
- why it matters
- how to fix it (unless obvious)

When a finding is about **shape** rather than a line (a call path that should not exist, a layer boundary in the wrong place, a state machine missing a branch), a small `text` or `diff` sketch carries it better than a sentence. The `show-me` skill owns the form. GitHub renders these in a PR comment. At most one per finding, and only where prose is losing.

**Assessment**, one of:

- `Ready to merge`: no Critical or Important issues
- `Merge with fixes`: only Minor issues left after the Important ones are addressed
- `Do not merge`: at least one unresolved Critical

Add one or two sentences of technical rationale.

### 7. Act on feedback

- **Critical**: fix before any further work on the branch.
- **Important**: fix before opening the PR or marking it ready.
- **Minor**: fix alongside, or record it in `docs/features/<slug>/` or a GitHub issue.
- **Disagreement**: push back with code, a test, or a doc line as evidence. A clear counter-argument is valid. "I will do it later" is not.

## Going deeper

Escalate when the diff is large, touches auth, or changes the database:

- The `.claude/agents/code-review-specialist` agent runs this same checklist as a separate pass with its own context. Use it for a second opinion on a big diff.
- The `.claude/agents/security-vulnerability-scanner` agent for an auth, session, or upload change.
- `/security-review` before calling any access-control change done.
- `/code-review ultra` is the deepest pass (this skill plus both agents). It is **user-triggered only**. Do not invoke it on your own.

## The quality bar

A review is doing its job when it covers only the diff's root cause, every finding cites file:line, severity is calibrated (no inflated Criticals, no buried real ones), the authorization chain is checked on every endpoint the PR touches, the assessment is unambiguous, and the author can act on every finding without asking a follow-up question.

## References

- `docs/engineering/ENGINEERING_CONSTITUTION.md` (outcome over mechanism, root cause before compensation, minimum complexity)
- `docs/engineering/API_STANDARDS.md` (resource binding, error behavior, required endpoint tests)
- `docs/engineering/SECURITY.md` (auth target and current-state exceptions)
- `docs/engineering/DATABASE.md` (PostgreSQL authority, migration safety, integration tests)
- `docs/engineering/TESTING.md` (test selection, CI as the merge signal)
- `app/auth/security.py` (`verify_admin`, chat tokens, rate limits, brute-force counters)
- `app/db/queries.py` and `app/db/pg.py` (query layer and placeholder translation)
- `app/modules/registry.py` (the module list, core against optional)
- `../merge-stacks/SKILL.md` (landing stacked PRs after review passes)
