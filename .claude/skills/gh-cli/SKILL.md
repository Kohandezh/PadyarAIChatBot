---
name: gh-cli
description: Reference for the GitHub CLI (gh) as this repo uses it. Autoload when working with pull requests (creating, viewing, listing, commenting, diffing), when checking CI runs, or when filing an issue.
---

# GitHub CLI (gh)

Prefer `gh` over `curl` or a browser for GitHub work. Every command accepts `-R, --repo <[HOST/]OWNER/REPO>` to target another repository.

The default branch here is `main`. This repo has no `scripts/pr.sh`. PRs are created with `gh pr create` directly.

CI on GitHub is the pass/fail signal for this repo, so `gh pr checks` and `gh run` are part of the normal loop, not a special case.

## gh pr create

```
gh pr create [flags]
```

Open PRs as drafts, then mark them ready once CI is green.

| Flag                | Description                                        |
| ------------------- | -------------------------------------------------- |
| `-t, --title <s>`   | PR title                                            |
| `-b, --body <s>`    | PR body text                                        |
| `-F, --body-file`   | Read body from a file (`-` for stdin)              |
| `-d, --draft`       | Open as a draft                                     |
| `-B, --base <br>`   | Base branch (default: the repo default branch)     |
| `-f, --fill`        | Title and body from the commit messages            |
| `-r, --reviewer`    | Request review from a user or team                 |
| `-l, --label <n>`   | Add labels                                          |
| `--dry-run`         | Print what would be created instead of creating it |
| `-w, --web`         | Finish in the browser                               |

**Examples:**

```bash
git push -u origin HEAD
gh pr create --draft --base main -t "fix(chat): keep the offer list pickable" -F /tmp/pr-body.md
gh pr create --draft --fill                 # title/body from commits
gh pr ready                                  # flip the current branch's PR out of draft
```

End every PR body with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## gh pr view

```
gh pr view [<number> | <url> | <branch>] [flags]
```

Without an argument, shows the PR for the current branch.

| Flag              | Description                              |
| ----------------- | ---------------------------------------- |
| `-c, --comments`  | View pull request comments               |
| `--json <fields>` | Output JSON with the specified fields    |
| `-q, --jq <expr>` | Filter JSON output with a jq expression  |
| `-t, --template`  | Format JSON output with a Go template    |
| `-w, --web`       | Open the pull request in a browser       |

**JSON fields:** additions, assignees, author, autoMergeRequest, baseRefName, baseRefOid, body, changedFiles, closed, closedAt, closingIssuesReferences, comments, commits, createdAt, deletions, files, fullDatabaseId, headRefName, headRefOid, headRepository, headRepositoryOwner, id, isCrossRepository, isDraft, labels, latestReviews, maintainerCanModify, mergeCommit, mergeStateStatus, mergeable, mergedAt, mergedBy, milestone, number, potentialMergeCommit, projectCards, projectItems, reactionGroups, reviewDecision, reviewRequests, reviews, state, statusCheckRollup, title, updatedAt, url

**Examples:**

```bash
gh pr view 21                              # View PR #21 in the terminal
gh pr view 21 --json title,state,author    # Specific fields as JSON
gh pr view 21 --comments                   # PR with its comments
gh pr view --json number,title,headRefName # PR for the current branch
gh pr view 21 --json reviews --jq '.reviews[] | .author.login + ": " + .state'
```

## gh pr list

```
gh pr list [flags]
```

| Flag                    | Description                                 |
| ----------------------- | ------------------------------------------- |
| `-a, --assignee <user>` | Filter by assignee                          |
| `-A, --author <user>`   | Filter by author                            |
| `-B, --base <branch>`   | Filter by base branch                       |
| `-d, --draft`           | Filter to draft PRs                         |
| `-H, --head <branch>`   | Filter by head branch                       |
| `-l, --label <names>`   | Filter by labels                            |
| `-L, --limit <n>`       | Maximum number of PRs to fetch (default 30) |
| `-s, --search <query>`  | Search PRs with a query                     |
| `-S, --state <state>`   | Filter by state: open, closed, merged, all  |
| `--json <fields>`       | Output JSON with the specified fields       |
| `-w, --web`             | Open in the browser                         |

**Examples:**

```bash
gh pr list                                  # Open PRs
gh pr list --state all --limit 50           # All PRs, up to 50
gh pr list --author @me --state open        # My open PRs
gh pr list --base main --json number,title,isDraft
gh pr list --search "review-requested:@me" --json number,title,author
```

## gh pr diff

```
gh pr diff [<number> | <url> | <branch>] [flags]
```

| Flag              | Description                              |
| ----------------- | ---------------------------------------- |
| `--patch`         | Raw patch output                         |
| `--name-only`     | Only the names of changed files          |
| `--color <when>`  | always, never, auto                      |

**Examples:**

```bash
gh pr diff 21                    # Diff for PR #21
gh pr diff 21 --name-only        # Changed file names only
gh pr diff 21 --patch            # Raw patch
gh pr diff                       # Diff for the current branch's PR
```

## gh pr comment

```
gh pr comment [<number>] [flags]
```

| Flag                | Description                                |
| ------------------- | ------------------------------------------ |
| `-b, --body <text>` | Comment body text                          |
| `-F, --body-file`   | Read body from a file (`-` for stdin)      |
| `-e, --editor`      | Open an editor to compose                  |
| `--edit-last`       | Edit your last comment                     |
| `-w, --web`         | Open in the browser to add a comment       |

**Examples:**

```bash
gh pr comment 21 -b "Looks good to me"
gh pr comment 21 -F /tmp/review.md          # long review body from a file
gh pr comment 21 --edit-last
```

## gh pr checks and gh run (CI)

CI is the merge gate. Read it instead of re-running the suite on this machine, where 15 tests always fail for environment reasons.

| Command                                  | Description                                     |
| ---------------------------------------- | ----------------------------------------------- |
| `gh pr checks [<number>]`                | Per-job status for a PR (`--watch`, `--required`, `--fail-fast`) |
| `gh run list --branch <b> --limit 1`     | Latest run for a branch                          |
| `gh run watch [<run-id>]`                | Follow a run until it finishes                   |
| `gh run view <run-id> --log-failed`      | Only the failing steps' logs                     |
| `gh run cancel <run-id>`                 | Cancel a run (use on intermediate stack slices)  |
| `gh workflow run CI --ref main`          | Manual dispatch of the CI workflow               |

Blocking jobs: `test`, `postgres-tests`, `secret-scan`, `identity-guard`. `dependency-audit` is advisory (`continue-on-error`). `deploy` runs only on a push to `main` and ships to production.

## gh pr merge

```
gh pr merge [<number>] [flags]
```

| Flag                   | Description                                        |
| ---------------------- | -------------------------------------------------- |
| `-s, --squash`         | Squash and merge (the method used here)            |
| `-d, --delete-branch`  | Delete the local and remote branch after merging   |
| `--auto`               | Merge once the requirements are met                |
| `--admin`              | Merge past requirements (do not use without asking)|

For a stack, use `gh stack merge` instead, which lands the slices bottom-up and atomically.

## gh issue create

This repo tracks work in GitHub Issues and `docs/features/<slug>/`. There is no Linear.

| Flag                | Description                            |
| ------------------- | -------------------------------------- |
| `-t, --title <s>`   | Issue title                            |
| `-b, --body <s>`    | Issue body                             |
| `-F, --body-file`   | Read body from a file (`-` for stdin)  |
| `-l, --label <n>`   | Add labels                             |
| `-a, --assignee`    | Assign (`@me` for yourself)            |

```bash
gh issue create -t "Pagination missing on /admin/api/leads" \
  -b "Found during review of #131. app/routers/leads.py:210 returns every row." \
  -l bug
gh issue list --search "pagination in:title" --state open   # dedupe before filing
```

## gh stack (stacked PRs)

Extension: `gh extension install github/gh-stack`. Full procedure in the `merge-stacks` and `scoped-pr` skills.

| Command                         | Description                                                         |
| ------------------------------- | ------------------------------------------------------------------- |
| `gh stack init [branches...]`   | Start a stack (or adopt existing branches, bottom to top)           |
| `gh stack add [branch]`         | Add a branch on top of the stack                                    |
| `gh stack submit`               | Push branches, create or update the linked PRs (`--auto`, `--open`) |
| `gh stack view`                 | Show the chain and per-PR status (`--short`, `--json`)              |
| `gh stack sync --prune`         | Fetch, fast-forward trunk, cascade-rebase, push, drop merged        |
| `gh stack rebase`               | Cascading rebase (`--upstack`, `--downstack`, `--continue/--abort`) |
| `gh stack merge [n]`            | Atomic bottom-up merge up to a PR (`--yes --squash`)                |
| `gh stack checkout <n\|branch>` | Check out a stack by stack or PR number, URL, or branch             |
| `gh stack modify`               | Interactive TUI: drop, fold, insert, reorder, rename slices         |
| `gh stack push`                 | Push the active branches of the current stack                       |
| `gh stack down/up/top/bottom`   | Navigate between slices                                             |
| `gh stack link <a> <b> …`       | Link existing branches or PRs into a stack without local tracking   |
| `gh stack unstack [n]`          | Remove the stack on GitHub and locally (`--local` = local only)     |

**Examples:**

```bash
gh stack init feat/x-1-migration && gh stack add feat/x-2-service   # build
gh stack submit --open                                              # open PRs, ready for review
gh stack sync --prune                                               # after something merges
gh stack merge --yes --squash                                       # land the stack
```
