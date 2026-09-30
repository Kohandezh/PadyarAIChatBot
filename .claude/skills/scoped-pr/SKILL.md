---
name: scoped-pr
description: Use when taking on bug fixes, addressing reported issues, or any change that could span multiple root causes, and again before creating a branch, commit, or pull request. Keeps each PR scoped to one root cause (not one file, not one ticket), and drives the branch, commit, and `gh pr create --draft` flow this repo uses.
---

# Scoped PR

## Overview

One reviewable idea per PR. Scope every PR to a single **root cause**, not one file and not one ticket.

Apply this at two moments:

1. **Early**, when you take on work that fixes a bug or addresses an issue, *before* writing code. Decide the PR boundaries up front.
2. **Backstop**, before you branch, commit, or open a PR. Re-check that the change still maps to one root cause.

The reasoning behind it is in `docs/engineering/ENGINEERING_CONSTITUTION.md` (root cause before compensation, minimum necessary complexity) and the STOP CONDITIONS section of `CLAUDE.md`.

## Step 1: decide the boundary first

Before touching code, list the distinct root causes in the requested work. **One root cause equals one PR.** If the task holds N independent causes, plan N branches and N PRs.

Decide with these tests:

- **Split when** the changes have different *causes*, fix different *symptoms*, or could be reverted independently. Ask: "Could I revert fix A without affecting fix B?" If yes, separate PRs.
- **Keep together when** several files share *one* cause (one bug touching the router, the service, and the template), or when splitting would leave a state that does not run.
- **Do not over-split.** Mechanical churn from a single action (one rename across the repo, one docs sweep) stays one PR. Splitting an atomic change into five PRs is as wrong as bundling five causes into one.
- **The drive-by test.** "Am I changing this because the task needs it, or because I am already in the file?" The second is always a separate PR. No drive-by renames, no "while I was here" refactors.

If the work is one cause, continue. If it is several, run the steps below once per cause, finishing one PR before starting the next.

## Step 2: start clean, off the latest main

The default branch is `main`. Branch from the freshly fetched remote tip so unmerged work from a previous fix never leaks into this PR:

```bash
git status --short                     # must be clean; stash or commit first
git fetch origin
git switch -c fix/<short-name> origin/main
```

Branch names follow the type prefix of the commit: `fix/`, `feat/`, `chore/`, `docs/`, `refactor/`.

### Optional: isolate in a worktree

Switching branches in place is the default. Use a worktree **only when you want isolation** (to keep the current checkout untouched, or to work on several PRs at once).

- Prefer the harness's `EnterWorktree` tool over raw git when it is available.
- Check you are not already isolated first: if `git rev-parse --git-dir` differs from `git rev-parse --git-common-dir`, you are already in a worktree. Do not nest.
- Manual path:

  ```bash
  git fetch origin
  git worktree add ../padyar-<branch> -b fix/<short-name> origin/main
  ```

  A worktree does not need a clean tree, because it leaves the current checkout alone.

### Big features: stack, do not bundle

A large feature is still one root cause per PR. Slice it into a stack of dependent PRs with **GitHub's native stacked PRs via `gh stack`** (`gh extension install github/gh-stack`), not hand-set base branches. GitHub tracks the chain, shows each PR's position, and merges the stack atomically.

```bash
gh stack init feat/x-1-migration    # bottom slice, off main
# …commit…
gh stack add feat/x-2-service       # next slice, branched off the one below
# …commit…
gh stack submit                     # push all branches, open the linked PRs
```

`gh stack submit` opens an editor per new PR (Ctrl+S submits all). `--auto` uses generated titles, `--open` creates them ready for review instead of draft. Re-run `submit` whenever you add commits or slices. Move around with `gh stack view` / `down` / `up` / `top`. To fix a lower slice: `gh stack down`, commit, `gh stack rebase --upstack`, `gh stack submit`.

Each PR's diff then shows only its own slice, and `gh stack merge` lands them bottom-up. Full merge and rebase procedure: the [merge-stacks](../merge-stacks/SKILL.md) skill.

**Titles.** GitHub's stack UI already shows each PR's position, so an `[n/N]` marker is optional. Add one only if the user asks. Do state the dependency in the body (`Stacked on #128, merge after it`), and keep any marker out of commit messages, because commits get squashed on merge.

## Step 3: fix it, test first

Write the failing test before the fix (red, then green). The test must fail when the fix is removed, per `docs/engineering/TESTING.md`. A security-sensitive change tests the denied path too, not only the allowed one. A browser-visible change needs a Playwright test using the **async** API.

Keep the diff confined to the one root cause. If the fix needs a new capability, it goes in as a module in `app/modules/registry.py` (optional first), per `CLAUDE.md`.

## Step 4: check and commit

Local pre-commit check is the compile only:

```bash
python -m py_compile app/main.py app/routers/chat.py
python -m py_compile <every .py file you changed>
```

**Do not run the full pytest suite locally as a gate.** CI on GitHub is the pass/fail signal, and 15 tests always fail on this machine while passing on CI.

Then use the `commit` skill. It writes the conventional message and the required `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` trailer. If it flags that the diff spans more than one root cause, stop and return to Step 1.

If you finished a feature, refresh the code graph before opening the PR:

```bash
graphify update .
```

## Step 5: open the PR as a draft

There is no `scripts/pr.sh` in this repo. Use `gh` directly:

```bash
git push -u origin HEAD
gh pr create --draft --base main \
  -t "fix(chat): keep the offer list pickable after a theme switch" \
  -F /tmp/pr-body.md
```

Useful flags: `-t/--title`, `-b/--body`, `-F/--body-file <path>` (`-` reads stdin), `-d/--draft`, `-f/--fill` (title and body from the commits), `-r/--reviewer`, `-l/--label`.

Open as a **draft** and mark it ready once CI is green and the review is done:

```bash
gh pr checks --watch
gh pr ready
```

### PR body

Every PR body uses the six headings of `.github/pull_request_template.md`, in
this order, spelled exactly like this:

`## Root cause`, `## Design doc`, `## Tests`, `## Security`, `## AI assistance`, `## Human review`.

The `pr-governance` workflow runs `scripts/check_pr_governance.py` on every PR.
When the PR changes `app/` or `migrations/`, the first five sections must have
text outside HTML comments, or the check shows a red X. The rules and the
reason the check is advisory: `docs/features/review-governance/SPEC.md` and
ADR-022. The review process: `docs/engineering/REVIEW_PROCESS.md`.

- **Root cause:** what was broken and why, not only what changed.
- **Design doc:** the path of a doc under `docs/` that exists in this branch,
  or one line `No design needed: <reason>` with a reason of at least 15
  characters.
- **Tests:** the test that proves it, and that it fails without the fix. Say
  what you ran and what you did not run.
- **Security:** who can reach the change, and the denied path.
- **AI assistance:** what the agent wrote and what the agent itself verified.
- **Human review:** an agent leaves this section **empty**. Never tick a box,
  never write a reviewer name, never write that a human reviewed anything. Only
  the human reviewer fills it.

Check the body before you open the PR:

```bash
git -c core.quotePath=false diff --no-renames --name-only main...HEAD > /tmp/changed.txt
python scripts/check_pr_governance.py --body-file /tmp/pr-body.md --changed-files /tmp/changed.txt
```

Example:

```
## Root cause
The chat token is minted per theme render, so switching themes invalidated
the token the open page was still holding.

## Design doc
No design needed: one-line lifetime fix, the token design is unchanged.

## Tests
New test tests/test_chat_token.py::test_token_survives_theme_switch fails on
main and passes here. Full suite: CI.

## Security
The token is still validated in app/auth/security.py. An expired token is
still rejected (tested).

## AI assistance
An agent wrote the fix and the test, ran py_compile and the new test file.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## Human review
```

Put the `🤖 Generated with` trailer line at the end of `## AI assistance`,
before `## Human review`. `## Human review` is the last heading and stays
completely empty in a body an agent writes.

**Visuals in the body.** When the description has to explain a *shape* (a call path that moved, a file that split, a state machine that gained a branch), a sketch is shorter than the paragraph it replaces. The `show-me` skill owns the form, and a `diff` block showing a call tree before and after is the usual fit. GitHub renders `diff`, `text`, and `mermaid` blocks in a PR body. At most one per PR, and it replaces prose rather than adding to it.

## Step 6: next root cause

Return to Step 2 from a fresh `main`-based branch. Never continue an independent fix on the previous fix's branch.

## Sizing budget

Soft, not a gate. If a PR passes roughly 400 lines or more than one root cause, justify it in the description or split it. Treat "can this be split?" as a normal question, not a failure.
