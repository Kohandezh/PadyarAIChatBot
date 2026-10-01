---
name: merge-stacks
description: Use when landing, syncing, or rebasing a stack of dependent PRs in this repo, after a lower slice merges, when a stacked PR's diff is polluted by already-merged commits, or whenever the user says "rebase the next stack", "rebase the whole stack", or "merge the stack". Drives GitHub's native stacked PRs via `gh stack` (sync, rebase, merge), and triages CI findings (advisory ones to a GitHub issue, blocking ones stop the merge). Reach for this even when the user names one PR but it sits in a stack.
---

# Merge Stacks

## Overview

A **stack** is a chain of dependent PRs. `B` is based on `A`, `C` on `B`, so each PR's diff shows only its own slice. This repo uses **GitHub's native stacked pull requests** through the `gh stack` extension, not hand-rolled base branches and manual rebases. This skill is the procedure for landing and maintaining one. It complements [scoped-pr](../scoped-pr/SKILL.md), which *builds* the stack.

```
main ─◄─ A ─◄─ B ─◄─ C        PR A base: main, PR B base: A, PR C base: B
```

Prerequisite: `gh extension install github/gh-stack` (already installed if `gh stack view` runs). All slices must be branches in this repo. Cross-fork stacks are not supported.

**Merging to `main` does not deploy.** CI has no deploy job and runs nothing on the production server. After a stack lands, an operator deploys it by hand with `sudo /usr/local/bin/padyar-deploy <slug> <port> <sha>` (`deploy/README.md`). What lands on `main` is what the next deploy ships, so still treat Step 5 as a release gate, not a formality.

## Golden rules

- **Let `gh stack` own the chain.** Do not `git rebase --onto`, do not re-point a base with `gh pr edit`, do not force-push a slice by hand. Those fight the stack object GitHub tracks and desync local from remote.
- **Merge bottom-up.** `gh stack merge` enforces this: everything below the PR you choose is included, and the merge is atomic. If any PR cannot merge, none do.
- **Sync after anything lands.** `gh stack sync --prune` is the one command that reconciles local, remote, and PR state.
- **Rebase locally, not server-side.** GitHub's server-side rebase produces unsigned commits. `gh stack rebase` does not.
- **A migration slice is special.** If a lower slice adds a file in `migrations/`, never edit that file after it has been applied anywhere. `scripts/apply_migrations.py` stores a sha256 per file, prints `REFUSING TO CONTINUE`, and exits 2, which aborts the next deploy. Fix it with a new numbered file in a new slice.

## Step 1: read the current state before touching anything

```bash
gh stack view              # branches, PR numbers, per-slice status (merged, open, needs rebase)
gh stack view --json       # machine-readable, when you need to reason over it
```

A `needs rebase` flag on a slice means its history is no longer linear on its parent. That is Step 3. If you are not on the stack's branch, `gh stack checkout <stack-number | pr-number | pr-url | branch>` picks it up. It fetches and sets up local tracking for a stack you have never had checked out.

## Step 2: sync after anything merges

When a slice lands (or someone pushes to `main`), one command brings everything back in line: fetch, fast-forward `main`, cascade-rebase the remaining slices onto their updated parents, force-push with lease atomically, and refresh PR state.

```bash
gh stack sync --prune      # --prune also deletes local branches for merged PRs
```

Because merges are squashed, a merged slice lands on `main` as a new commit that does not match its branch history. `gh stack sync` handles that. A plain `git rebase origin/main` does not, which is why you do not run one.

`sync` never opens PRs. If it reports **"Branches synced"** instead of "Stack synced", the branches were rebased and pushed but no stack object was created or updated (for example fewer than two PRs exist yet). Use `gh stack submit` to open the missing PRs.

**Divergence:** if local and remote stacks disagree, `sync` asks whether to take the remote as the source of truth, delete the remote stack, or cancel. In a non-interactive shell it aborts without pushing. Surface that to the user rather than forcing it.

## Step 3: rebase when sync is not enough

`gh stack sync` restores every branch and bails out on a conflict, telling you to resolve it interactively:

```bash
gh stack rebase                 # full cascade: main → bottom → top
gh stack rebase --downstack     # only trunk → current branch
gh stack rebase --upstack       # only current branch → top (use after fixing a lower slice)
gh stack rebase --no-trunk      # inter-slice rebases only, do not pull or rebase on main
gh stack rebase --continue      # after resolving conflicts
gh stack rebase --abort         # restore every branch to its pre-rebase state
```

**Fixing a lower slice mid-stack:** `gh stack down`, edit, commit, `gh stack rebase --upstack`, `gh stack top`, `gh stack submit`.

**Restructuring** (drop, fold, insert, reorder, rename a slice) is `gh stack modify`, an interactive TUI applied with Ctrl+S, then `gh stack submit` to push the result. Never do this by deleting and recreating branches.

### Tell the user what a full-stack rebase costs

Rebasing slices that did not need it gives them fresh commit hashes, which re-triggers CI and resets review anchors. A stacked slice's diff is already clean against *its own base*, so prefer just-in-time (`gh stack sync` when something merges) over a speculative full rebase, unless the user asks for every diff to be reviewable right now.

### Cancel the CI runs a rebase triggers

A cascade rebase force-pushes several branches at once, and each one starts a full CI run. The `postgres-tests` job spins up a PostgreSQL 16 service container, so these are not cheap. Only the run on the slice about to merge matters. Cancel the rest and let CI finish on the one being landed:

```bash
gh run list --limit 10 --json databaseId,headBranch,status
gh run cancel <run-id>
```

## Step 4: merge

```bash
gh stack merge                    # interactive: how far up, which method, confirm
gh stack merge <pr-number>        # everything up to and including that PR
gh stack merge <stack-number>     # a stack you do not have checked out
gh stack merge --yes --squash     # non-interactive, squash (the method used here)
```

The merge is **atomic and all-or-nothing**. Every PR below your selection is included. GitHub evaluates branch protection and repo rules at merge time and reports failures back. Bypassing merge requirements is not supported for stacks. If `main` uses a merge queue, the stack is queued and lands when the queue processes it.

After a partial merge, go back to Step 2 (`gh stack sync --prune`).

Merging one PR at a time with `gh pr merge` still works but gives up atomicity and leaves you resyncing after each one. Prefer `gh stack merge`.

## Step 5: triage what CI and review found

A force-push re-runs CI on the slice. Sort the findings by whether they should stop the merge.

**These block the merge. Do not land the slice until they are fixed:**

- A red `test`, `postgres-tests`, `secret-scan`, or `identity-guard` job. All four are blocking, and `deploy` will not run without them.
- A new Critical finding from the `code-review` skill or the `code-review-specialist` agent: an authorization hole, a resource-binding bug, data loss, a crash on a visitor path, an edited migration that was already applied.
- A rebase replays old commits onto new code, so a finding that was dormant can become live. That is exactly the case this rule guards.

**These do not block. File them and move on:**

- A `dependency-audit` hit. The job is advisory (`continue-on-error: true`), so read it, then file it.
- Low-severity review nits, stale comments, minor defensive gaps, follow-up hardening.

File each one as a **GitHub issue**, or, if it belongs to a feature already being tracked, as a line in that feature's `docs/features/<slug>/RESEARCH.md`. **Search first so you do not duplicate**, since these findings often refer to something already filed:

```bash
gh issue list --search "<keyword> in:title" --state open
gh issue create -t "<short title>" \
  -b "Found while landing #<pr>. app/services/x.py:120. <the concrete fix>." \
  -l bug
```

Put the file:line, the concrete fix, and a link back to the PR in the body.

The line is blast radius and severity, not convenience: if it can lose data, cross a security boundary, or break a visitor path, it blocks. If it is a quality or clarity issue, it becomes an issue and the stack lands.

## Escape hatches

- **`gh stack link <branch-or-pr> …`** (bottom to top) turns existing branches or PRs into a GitHub stack without local tracking. Use it for a stack built by hand before this convention, or when another tool manages the branches. Pass a stack number first to append to an existing stack.
- **`gh stack unstack [<stack-number>]`** removes the stack on GitHub and locally (`--local` keeps the remote stack). PRs that are queued or have auto-merge enabled stay stacked.

## Quick checklist

1. `gh stack view`: read every slice's base, PR state, and rebase flags.
2. `gh stack sync --prune` after anything merges or `main` moves.
3. Conflicts or a lower-slice fix: `gh stack rebase` (`--upstack` / `--downstack`), `--continue` / `--abort`. Restructure with `gh stack modify` plus `submit`.
4. Cancel CI on intermediate slices. Let it finish on the one being landed.
5. Triage: blocking job red or a new Critical finding stops the merge. Advisory findings become a GitHub issue (dedupe first).
6. `gh stack merge --yes --squash`: atomic, bottom-up. Re-sync if you merged only part of the stack.
7. The merge does not deploy. Tell the operator the merged sha so they can run `padyar-deploy` on the server.
