# Herdr topology

Read this before creating any Herdr resource.

## Never change the user's view

Pass `--no-focus` to every `herdr worktree create`, `herdr worktree open`,
`herdr tab create`, and `herdr agent start`. Never run `herdr tab focus`, `herdr agent
focus`, or another focus command. A new worktree appears in the sidebar without being
opened in front of the user.

## Names

| Thing             | Pattern                       | Example                             |
| ----------------- | ----------------------------- | ----------------------------------- |
| Mission id        | `<yyyymmdd>-<slug>`           | `20260913-sync-batch`               |
| Task slug         | issue number, or a short noun | `issue142`, `otpexpiry`             |
| Team id           | `<task-slug>-t<N>`            | `eng2911-t1`                        |
| Agent name        | `<team-id>-<role>`            | `eng2911-t1-impl`, `eng2911-t1-rev` |
| Maker workspace   | team id                       | `eng2911-t1`                        |
| Checker workspace | `<team-id>-check`             | `eng2911-t1-check`                  |
| Tab               | team id                       | `eng2911-t1`                        |

Role suffixes: `impl`, `rev`, `inv`, `ver`, `trace`, `res`, `crit`, `scout`, `final`,
`check2`, `integ`. A name is stable for the session's life. When a session changes role,
it keeps its name and the ledger records the new role. A task without an issue number uses a
short slug of its own. Agent names
must match `[a-z][a-z0-9_-]{0,31}` and be unique among live agents.

## Checkouts per task type

| Type           | Maker checkout                                | Checker checkout                      |
| -------------- | --------------------------------------------- | ------------------------------------- |
| Implementation | branch worktree                               | clean detached worktree at the base   |
| Debugging      | branch worktree, where the failing test lands | clean detached worktree at the base   |
| Tracing        | detached worktree at the base                 | the same worktree, read-only for both |
| Research       | branch worktree, where the document lands     | clean detached worktree at the base   |

Every team gets its own checkouts. Two teams never share a worktree: one team's test run
would see the other team's edits.

## Layout

```text
Herdr sidebar
├── leader pane (Foreman, any workspace or tab)
├── <team-id> workspace, maker worktree
│   └── <team-id> tab
│       ├── maker pane, persistent
│       └── checker pane, cwd in the clean worktree, persistent
├── <team-id>-check workspace, clean checker worktree
└── one more pair of workspaces per team
```

Maker and checker panes persist for the team's whole life. Keep the same panes and
interactive sessions across every turn.

## Create the worktrees

Create the maker's branch worktree. Name the branch per the `scoped-pr` skill:

```bash
herdr worktree create --cwd <repo> --branch <branch> --base <base> --path <maker-path> --label <team-id> --no-focus --trust-repository
```

Create the detached clean checker worktree from the same base:

```bash
herdr worktree create --cwd <repo> --base <base> --path <checker-path> --label <team-id>-check --no-focus --trust-repository
```

If a required worktree already exists, load it into the sidebar instead:

```bash
herdr worktree open --path <existing-path> --label <team-id> --no-focus --trust-repository
```

Never create a second worktree for the same path or branch. Never use raw
`git worktree add` or an IDE command. Record every workspace ID from the responses. Verify
the paths with `herdr worktree list`. Each entry must have an `open_workspace_id`.

If `herdr worktree create` fails with
`could not lock config file .git/config: File exists`, the agent sandbox is blocking git's
config lock. Git may have created the branch already. Confirm it still points at the base
with `git rev-parse <branch> <base>`, delete it with `git branch -D <branch>`, and rerun
the create outside the sandbox. Never delete `.git/config.lock`.

## Create the tab and panes

A new worktree's workspace has an initial tab. Rename it to the team id:

```bash
herdr tab rename <tab-id> <team-id>
```

For an existing workspace, create a tab instead:

```bash
herdr tab create --workspace <maker-workspace-id> --cwd <maker-path> --label <team-id> --no-focus
```

Do not pre-split the tab. On the installed CLI `herdr agent start` has no `--pane`: it
makes its own pane from `--tab` plus `--split`, and it is the only call that can put
`CLAUDE_CONFIG_DIR` into the session's environment. Start the maker in the tab, then start
the checker with `--split right`, each with its own `--cwd`. The exact commands are in
[agents-and-models.md](agents-and-models.md).

If the tab is narrow, use `--split down` for the checker. For tracing, give the checker
`<maker-path>` as its `--cwd` too.

Read each pane id back after start and record it in `ledger.md`, because submitting a
brief needs it:

```bash
herdr agent get <maker-name>
herdr agent get <checker-name>
```

Name the panes once you have the ids:

```bash
herdr pane rename <maker-pane-id> <maker-role>
herdr pane rename <checker-pane-id> <checker-role>
```

## Brief the sessions

Write each prompt block to `briefs/` first. Then send a short prompt that points at it:

```bash
herdr agent send <agent-name> "Read <absolute-brief-path>, acknowledge with the envelope, then do only that assignment."
herdr pane send-keys <agent-pane-id> Enter
```

`herdr agent send` writes literal text and does not submit it. The Enter is a second call,
against the pane id, not the agent name.

Do not pass a one-shot prompt as a native agent argument. Start the normal session first.
If an agent becomes blocked, inspect `herdr agent get` and `herdr agent read` before
sending input. A settled `idle` state is not a handoff. Confirm the expected artifact
exists before starting a dependent turn.
