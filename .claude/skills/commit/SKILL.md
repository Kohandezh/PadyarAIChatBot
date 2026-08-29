---
name: commit
description: Use when creating git commits. Generates conventional commit messages, runs pre-commit checks, and suggests splitting large changes.
---

# Commit

## Overview

Create well-formatted commits with conventional commit messages. Automatically runs pre-commit checks, analyzes changes, and suggests splitting large changes into atomic commits.

**Only commit when the user explicitly asks.** Do not commit or push on your own initiative, even after finishing a change.

## What This Does

1. **Pre-commit checks** (mandatory, from CLAUDE.md):
   - Syntax-check the core entry points and every Python file you changed:
     ```bash
     python -m py_compile app/main.py
     python -m py_compile app/routers/chat.py
     # ...plus any other .py file touched by this change
     ```
   - If a test suite exists (pytest is the chosen framework), run the relevant tests:
     ```bash
     pytest
     ```
   - There is **no linter/formatter configured** in this repo (no black/ruff/eslint/prettier config). Don't invent one. If you think formatting matters, note that it isn't currently set up rather than running an arbitrary tool.

2. **Stages files**:
   - Checks `git status` for staged files
   - If 0 files staged, runs `git add` for the relevant modified/new files

3. **Analyzes changes**:
   - Performs `git diff` to understand changes
   - Detects if multiple distinct logical changes are present
   - Suggests breaking into smaller commits if appropriate

4. **Creates commit message**:
   - Uses conventional commit format
   - Ends with the project's co-author trailer (see below)

## Conventional Commit Format

```
<type>: <description>

[optional body]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

The repo's history mixes styles, but **conventional commits are the target**. Every commit should end with the trailer line:

```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

**Types:**

- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc)
- `refactor`: Code changes that neither fix bugs nor add features
- `perf`: Performance improvements
- `test`: Adding or fixing tests
- `chore`: Changes to the build process, tools, etc

**Guidelines:**

- Use present tense, imperative mood ("add feature" not "added feature")
- Keep first line under 72 characters
- Reference issues in footer if applicable

## Guidelines for Splitting Commits

Split commits based on:

1. **Different concerns**: Changes to unrelated parts of the codebase
2. **Different types**: Mixing features, fixes, refactoring
3. **File patterns**: Source code vs documentation vs tests
4. **Logical grouping**: Changes easier to understand separately
5. **Size**: Very large changes clearer if broken down

If the diff spans more than one root cause, this is a PR-boundary problem, not just a commit-splitting one: stop and use the `scoped-pr` skill to split the work into separate branches/PRs before committing.

## Examples

Good commit messages:

- `feat: add voice transcription module`
- `fix: resolve BM25 similarity mismatch on empty query`
- `docs: update onboarding setup for ENABLED_MODULES`
- `refactor: simplify Persian normalizer synonym expansion`
- `test: add pytest coverage for admin auth`
- `perf: cache the embedding index between chat requests`

Split commits example:

- First: `feat: add synonym CRUD router and service`
- Second: `docs: document synonym module in services README`
- Third: `test: add integration tests for synonym endpoints`

> Note: the repository's default branch for PRs is **`main`**. `.github/workflows/ci.yml` also triggers on `main-noor`, but only `main` is the default branch and only `main` deploys. Compare diffs and base branches against `main`.
