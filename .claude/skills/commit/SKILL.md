---
name: commit
description: Use when creating git commits in this repo. Runs the local pre-commit check (python -m py_compile), writes a conventional commit message with the required trailer, and suggests splitting a diff that spans more than one root cause.
---

# Commit

## Overview

Create clean commits with conventional commit messages. Run the local check, read the diff, split it when it holds more than one change, and add the trailer this repo requires.

Commit only when the user asks for it. If you are on `main` (the default branch), create a branch first. The `scoped-pr` skill owns branch naming and PR boundaries.

## What this does

1. **Pre-commit check.** There is no husky, no linter, and no formatter configured in this repo. The only local check is a syntax compile of the Python files you touched:

   ```bash
   python -m py_compile app/main.py app/routers/chat.py
   python -m py_compile <every .py file you changed>
   ```

   **Do not run the full pytest suite locally to gate a commit.** CI on GitHub is the pass/fail signal. This machine has 15 tests that always fail here (they need a live PostgreSQL or network) and always pass on CI, so a local red run tells you nothing. Run one test file while you are writing it if that helps, then push and read CI:

   ```bash
   gh run list --branch <branch> --limit 1
   gh run watch
   ```

2. **Stage the files.** Check `git status`. If nothing is staged, stage the modified and new files that belong to this change. Never `git add .` blindly: untracked build output, `graphify-out/`, `.env`, and local scratch files must stay out. The `secret-scan` CI job is blocking and will fail the push if a credential lands in a tracked file.

3. **Read the diff.** Run `git diff --staged`. Decide whether it is one change or several.

4. **Write the message.** Conventional format, plus the required trailer.

## Conventional commit format

```
<type>: <description>

[optional body]

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

**Types:**

- `feat`: a new feature
- `fix`: a bug fix
- `docs`: documentation changes
- `style`: formatting only, no behavior change
- `refactor`: code change that neither fixes a bug nor adds a feature
- `perf`: performance improvement
- `test`: adding or fixing tests
- `chore`: build process, tooling, dependencies

**Guidelines:**

- Present tense, imperative mood ("add feature", not "added feature").
- First line under 72 characters.
- Explain the why in the body when the change is not obvious from the subject. The what is already in the diff.
- No em dashes. Use a period, a comma, or parentheses.
- End the message with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`. This repo requires that trailer on every commit you create.
- Reference a GitHub issue in the footer when one exists (`Refs #123`, or `Fixes #123` to close it on merge).
- For a PR in a stack, keep the stack position (`[2/3]`) out of the commit subject. It belongs in the **PR title** (see the `scoped-pr` skill). Commits get squashed on merge, so a per-commit marker is noise.

## When the change touches certain areas

- **A migration:** the commit includes the new numbered file in `migrations/` and the mirrored change in `init_db()` in `app/db/connection.py`. Never amend a commit to edit a migration that has already been applied. `scripts/apply_migrations.py` stores a sha256 per file and will refuse to continue, which aborts the next deploy.
- **A new dependency:** `pip install` it and record it in `requirements.txt` (or `requirements-dev.txt` for test-only packages) in the same commit.
- **A new feature:** it should already be a module in `app/modules/registry.py`. If it is not, stop and fix that before committing.
- **Docs that the change invalidates:** update them in the same commit. `CLAUDE.md` lists which file to touch for which kind of change.

## Guidelines for splitting commits

Split on:

1. **Different concerns.** Unrelated parts of the codebase.
2. **Different types.** A feature mixed with a refactor mixed with a fix.
3. **File patterns.** Source against docs against tests, when they are genuinely separate work.
4. **Logical grouping.** Changes that are easier to read apart than together.
5. **Size.** A very large change is clearer in steps.

If the diff spans more than one **root cause**, this is a PR boundary problem, not just a commit problem. Stop and use the `scoped-pr` skill to split the work into separate branches and PRs before committing.

## Examples

Good subjects:

- `feat: add booth lead export to the admin queue`
- `fix: keep the chat token valid across a theme switch`
- `docs: record the PostgreSQL migration rule in DATABASE.md`
- `refactor: move offer paging out of the chat router into answer.py`
- `test: cover the denied path on /admin/api/backups/restore`
- `perf: stop rebuilding the BM25 index on every chat request`

Full message:

```
fix: bind the edit session to the invite it was minted from

A contact could open /edit/<token>, then reuse the session cookie against
another company's row. The session now stores the company id from the
invite and every write checks it.

Refs #128

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

Split example:

- First: `feat: add the /api/leads/export endpoint`
- Second: `test: cover leads export auth and pagination`
- Third: `docs: document the leads export in the module table`
