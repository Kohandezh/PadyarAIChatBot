---
name: gh-cli
description: Reference for the GitHub CLI (gh). Autoload when working with pull requests — viewing, listing, commenting, or diffing PRs.
---

# GitHub CLI (gh) — Pull Request Commands

Always prefer `gh` CLI over `curl` or web browser for PR operations. All commands support `-R, --repo <[HOST/]OWNER/REPO>` to target a different repository.

## gh pr view

```
gh pr view [<number> | <url> | <branch>] [flags]
```

Without an argument, displays the PR for the current branch.

| Flag              | Description                                      |
| ----------------- | ------------------------------------------------ |
| `-c, --comments`  | View pull request comments                       |
| `--json <fields>` | Output JSON with the specified fields            |
| `-q, --jq <expr>` | Filter JSON output using a jq expression         |
| `-t, --template`  | Format JSON output using a Go template           |
| `-w, --web`       | Open the pull request in a browser               |

**JSON fields:** additions, assignees, author, autoMergeRequest, baseRefName, baseRefOid, body, changedFiles, closed, closedAt, closingIssuesReferences, comments, commits, createdAt, deletions, files, fullDatabaseId, headRefName, headRefOid, headRepository, headRepositoryOwner, id, isCrossRepository, isDraft, labels, latestReviews, maintainerCanModify, mergeCommit, mergeStateStatus, mergeable, mergedAt, mergedBy, milestone, number, potentialMergeCommit, projectCards, projectItems, reactionGroups, reviewDecision, reviewRequests, reviews, state, statusCheckRollup, title, updatedAt, url

**Examples:**

```bash
gh pr view 21                              # View PR #21 in terminal
gh pr view 21 --json title,state,author    # Get specific fields as JSON
gh pr view 21 --comments                   # View PR with comments
gh pr view 21 --web                        # Open in browser
gh pr view --json number,title,headRefName  # PR for current branch
gh pr view 21 --json reviews --jq '.reviews[] | .author.login + ": " + .state'
```

## gh pr list

```
gh pr list [flags]
```

| Flag                    | Description                                        |
| ----------------------- | -------------------------------------------------- |
| `-a, --assignee <user>` | Filter by assignee                                 |
| `-A, --author <user>`   | Filter by author                                   |
| `-B, --base <branch>`   | Filter by base branch                              |
| `-d, --draft`           | Filter to draft PRs                                |
| `-H, --head <branch>`   | Filter by head branch                              |
| `-l, --label <names>`   | Filter by labels                                   |
| `-L, --limit <n>`       | Maximum number of PRs to fetch (default 30)        |
| `-s, --search <query>`  | Search PRs with query                              |
| `-S, --state <state>`   | Filter by state: open, closed, merged, all         |
| `--json <fields>`       | Output JSON with specified fields                  |
| `-w, --web`             | Open in browser                                    |

**Examples:**

```bash
gh pr list                                  # List open PRs
gh pr list --state all --limit 50           # All PRs, up to 50
gh pr list --author tm0h --state open       # Open PRs by specific author
gh pr list --label bug --json number,title  # PRs labeled "bug"
gh pr list --search "fix error"             # Search PR text
gh pr list --search "review-requested:@me" --json number,title,author
```

## gh pr diff

```
gh pr diff [<number> | <url> | <branch>] [flags]
```

| Flag              | Description                              |
| ----------------- | ---------------------------------------- |
| `--patch`         | Display raw patch output                 |
| `--name-only`     | Display only names of changed files      |
| `--color <when>`  | Use color: always, never, auto           |
| `-q, --jq <expr>` | Filter JSON output using a jq expression |

**Examples:**

```bash
gh pr diff 21                    # View diff for PR #21
gh pr diff 21 --name-only        # List only changed file names
gh pr diff 21 --patch            # Raw patch format
gh pr diff                       # Diff for PR on current branch
```

## gh pr comment

```
gh pr comment [<number>] [flags]
```

| Flag                  | Description                              |
| --------------------- | ---------------------------------------- |
| `-b, --body <text>`   | Supply comment body text                 |
| `-e, --editor`        | Open editor to compose comment           |
| `--edit-last`         | Edit the last comment by the auth user   |
| `-w, --web`           | Open in browser to add comment           |

**Examples:**

```bash
gh pr comment 21 -b "Looks good to me"           # Comment on PR #21
gh pr comment 21 --editor                        # Open editor for comment
gh pr comment 21 --edit-last                     # Edit your last comment
gh pr comment 21 -b "nit: typo in line 42"       # Leave a review comment
```
