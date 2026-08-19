---
name: scoped-pr
description: Use when taking on bug fixes, addressing reported issues, or any change that could span multiple root causes — and again before creating a branch, commit, or pull request. Keeps each PR scoped to one root cause (not one file, not one ticket), branching off and targeting the repo's main branch (main-noor) with git + the gh CLI.
---

# Scoped PR

## Overview

One reviewable idea per PR. Scope every PR to a single **root cause** — not one file, not one ticket.

This repo's main branch is **`main-noor`** (not `main`). PRs are created with the `gh` CLI against `main-noor`. There is **no helper script** — use plain `git` and `gh pr create`.

Apply this at two moments:

1. **Early** — when you take on work that fixes bugs or addresses issues, _before_ writing code. Decide the PR boundaries up front.
2. **Backstop** — before you branch, commit, or open a PR, re-check the change still maps to one root cause.

> **Only branch, commit, push, or open a PR when the user explicitly asks.**

## Step 1 — Decide the boundary first

Before touching code, list the distinct root causes in the requested work. **One root cause = one PR.** If the task contains N independent causes, plan N branches and N PRs.

Decide with these tests:

- **Split when** the changes have different _causes_, fix different _symptoms_, or could be reverted independently. Ask: _"Could I revert fix A without affecting fix B?"_ If yes → separate PRs.
- **Keep together when** several files share _one_ cause (e.g. one bug touching the router, the service, and the db layer), or splitting would leave a non-building intermediate state.
- **Don't over-split.** Mechanical churn from a single action (one rename, one find-and-replace) and genuinely atomic changes stay one PR. Splitting an atomic change into five PRs is as wrong as bundling five causes into one.
- **The drive-by test.** _"Am I changing this because the task needs it, or because I'm already in the file?"_ The second is always a separate PR — no drive-by renames or "while I was here" refactors.

Remember the project's module principle: a new feature is normally one optional module (`app/modules/registry.py`) — router + service + optional admin page. That whole module is usually one root cause / one PR.

If the work is one cause, continue. If it is several, do the steps below once per cause, fully finishing one PR before starting the next.

## Step 2 — Start clean, off the latest main-noor

```bash
git fetch origin
git switch -c <branch-name> origin/main-noor
```

Branching from the latest `origin/main-noor` keeps unmerged work from a previous fix out of this PR. Make sure the tree is clean first (`git status`) so you don't pick up unrelated changes.

### Optional: isolate in a worktree

In-place branch switching is the default. **Only when you want isolation** (e.g. to keep the current checkout untouched, or to work several PRs in parallel):

- **Prefer the native `EnterWorktree` tool** — always use the harness's worktree tool over raw git when it exists. First detect existing isolation (`git rev-parse --git-dir` ≠ `--git-common-dir` ⇒ already in a worktree — don't nest).
- **Manual path:** create the worktree *outside* the repo root so it never clutters `git status` or nests a checkout inside the tracked tree: `git worktree add ../worktrees/<branch> -b <branch> origin/main-noor`, then `cd` in (or `EnterWorktree` its path) and continue.

### Big features → stack, don't bundle

A large feature is still one root cause per PR — slice it into a stack of dependent PRs. Branch each slice off the one below, and target the parent when opening the PR:

```bash
git switch -c feat/x-1-schema origin/main-noor
# …commit, open PR with --base main-noor…
git switch -c feat/x-2-api feat/x-1-schema
# …commit…
gh pr create --base feat/x-1-schema --title "feat(api): x endpoint" --fill
```

Each PR's diff then shows only its own slice. **Merge bottom-up — first slice first.** After merging slice A into `main-noor`, rebase the next slice onto the updated `main-noor` (`git rebase --onto origin/main-noor <tip-of-A> feat/x-2-api`) before opening/merging it.

## Step 3 — Fix it

Keep the diff confined to the one root cause. Follow the project's conventions (see `software-architecture` and `implement` skills). If a test suite exists for the area (pytest), add or update tests; otherwise verify via `python -m py_compile` and by running `python main.py`.

## Step 4 — Commit

Use the `commit` skill. It runs the mandatory `python -m py_compile` checks and writes a conventional-commit message ending with the `Co-Authored-By: Claude Opus 4.8` trailer. If it flags that the diff spans more than one root cause, stop and return to Step 1 — split before committing.

## Step 5 — Open the PR

Push the branch, then create the PR against `main-noor` with `gh`:

```bash
git push -u origin <branch-name>
gh pr create --base main-noor --title "<title>" --body "<body>"
```

- Use `--fill` to default title/body from the branch's commits, or supply `--title`/`--body` for richer context.
- `-F <file>` / `--body-file -` reads the body from a file or stdin.
- `--draft` opens a draft PR.

**End every PR body with the standard footer:**

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Example:

```bash
gh pr create --base main-noor \
  --title "fix(chat): downgrade non-actionable provider noise" \
  --body "$(cat <<'EOF'
Reclassify GapGPT 4xx responses as warnings, not exceptions.

Root cause: invalid_request errors were logged as hard failures,
masking genuine outages.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Step 6 — Next root cause

Return to Step 2 from a fresh `main-noor`-based branch. Never continue an independent fix on the previous fix's branch.

## Sizing budget

Soft, not a gate: if a PR exceeds ~400 lines or more than one root cause, justify it in the description or split it. Treat "can this be split?" as a normal question, not a failure.
