---
name: herdr
description: "Control Herdr, a terminal multiplexer for coding agents. Use only when the user explicitly mentions Herdr or asks to use Herdr to inspect or control panes, tabs, workspaces, commands, or another agent. Do not use merely because a task could benefit from a background terminal, delegation, or parallel work. Requires HERDR_ENV=1."
---

# Herdr

Herdr organizes terminals into workspaces, tabs, and panes, recognizes coding agents running inside panes, and exposes the current session through the `herdr` CLI.

Before issuing any control command, verify that this agent is running inside a Herdr-managed pane:

```bash
test "${HERDR_ENV:-}" = 1
```

If the check fails, say that you are not running inside Herdr and stop. Do not inspect or control the focused Herdr session from outside Herdr.

When the check passes, the `herdr` binary in `PATH` talks to the current session. Use it to inspect neighboring work, create terminal layout, start agents and commands, read output, and wait for state changes.

## Learn the current CLI

The installed binary is the authority for command syntax. Start with:

```bash
herdr --help
```

Then print the relevant command group by running the group without a subcommand:

```bash
herdr agent
herdr pane
herdr workspace
herdr tab
herdr worktree
herdr terminal
herdr notification
herdr integration
herdr session
herdr machine
```

Do not run bare `herdr` for discovery; it launches or attaches the TUI. Do not probe a mutating nested command by omitting arguments. Commands such as `herdr workspace create` are valid with defaults and will execute.

Most control commands return JSON. Read identifiers and state from those responses instead of predicting them.

## Understand layout, panes, and agents

Choose the primitive that matches the job:

- Workspace, tab, and pane topology organize terminal locations.
- Pane commands control raw terminals, shells, tests, servers, input, and output.
- Agent commands control the recognized coding agent currently occupying a pane.

A pane exists whether or not it contains an agent. `agent start` requires an existing available shell pane and never creates, splits, or moves layout. Use pane commands for ordinary processes. Use agent commands when Herdr must validate agent identity or interpret `idle`, `working`, `blocked`, `done`, and `unknown` lifecycle states.

Agent commands accept either a unique live agent name or the pane ID currently hosting that agent. They do not accept terminal IDs or bare agent-kind labels. Names must match `[a-z][a-z0-9_-]{0,31}` and be unique among live agents. A name follows the current pane occupant and is cleared when that agent exits, is released, or is replaced.

`idle` and `done` both mean the agent is ready for input. The CLI/API uses the server's seen state to distinguish them; explicit focus commands mark the target seen, while reads do not. Each TUI client tracks viewed completions independently, so its Done badge can differ from the CLI or another client's badge. `blocked` means Herdr recognized an approval or question UI. `unknown` means an agent is present but Herdr cannot classify it confidently; it does not prove completion.

## Use IDs and caller context

Public IDs are opaque stable handles:

- workspace: `w1`
- tab: `w1:t1`
- pane: `w1:p1`

Closed tab and pane IDs are not reused. A pane moved into another workspace receives a new workspace-qualified pane ID. After `pane move`, continue with `.result.move_result.pane.pane_id` or the live agent name. The old value is reported as `.result.move_result.previous_pane_id`; only the moved process's inherited caller context keeps resolving that old ID, so do not use it as a general agent target.

Herdr injects the caller's context into each managed pane:

```bash
printf '%s\n' "$HERDR_WORKSPACE_ID" "$HERDR_TAB_ID" "$HERDR_PANE_ID"
```

Prefer `--current` when a pane command should target the calling pane. Omitting a target may use the UI-focused pane, which can belong to the user or another client.

Discover live state with:

```bash
herdr workspace list
herdr tab list --workspace "$HERDR_WORKSPACE_ID"
herdr pane current --current
herdr pane list --workspace "$HERDR_WORKSPACE_ID"
herdr agent list
```

Creation responses expose the IDs to use next. `workspace create` returns `.result.workspace`, `.result.tab`, and `.result.root_pane`. `tab create` returns `.result.tab` and `.result.root_pane`. `pane split` returns the new pane as `.result.pane`.

IDs and live agent names are scoped to one server. Two saved SSH machines can both have `w1:p1` or an agent named `reviewer`. Selecting a machine in the TUI does not retarget commands running in your pane: they still use the inherited session and socket context. Run remote control commands on the intended host with its explicit session, and rediscover IDs there.

`herdr machine list` lists saved connection profiles, not a cross-machine pane inventory; add `--json` for scripts. Only add, remove, enable, or disable profiles when the user asks. Removing a profile disconnects the client but does not stop remote sessions. Adding a machine uses the remote default session unless `--remote-session` is explicitly supplied. Setup asks before stopping an incompatible server and defaults to No; do not approve replacement without the user's consent. Experimental handoff is not part of `machine add`.

## Start and coordinate an agent

Default to a sibling pane in the current tab and the current working directory. Do not create a workspace, tab, worktree, or different cwd unless the user explicitly requests that topology or location.

Honor a direction requested by the user. Otherwise inspect the caller pane:

```bash
herdr pane layout --pane "$HERDR_PANE_ID"
```

Split a wide pane to the right and a narrow or tall pane down. Avoid repeated same-direction splits that create unusably narrow columns or short rows. Keep the user's focus in the calling pane.

Do not pre-split for an agent. `herdr agent start` makes its own pane from `--split`, and
it is the only call that can put environment variables into the session. Pre-splitting is
for running an ordinary command in another pane, which the section below covers.

`herdr agent start` has no `--kind` and no `--pane`. The program to run is the argv after
`--`, and the pane comes from `--tab` or `--workspace` plus `--split`. Verify the flags
before a run:

```bash
herdr agent start --help
```

In this repository every agent is Claude Code on the `claudepersonal` configuration.
`claudepersonal` is a shell alias (`CLAUDE_CONFIG_DIR=~/.claude-personal claude`), and an
alias does not survive a direct spawn, so pass the variable with `--env` and an absolute
path:

```bash
herdr agent start reviewer --tab <tab-id> --cwd "$PWD" --split right --no-focus \
  --env CLAUDE_CONFIG_DIR="$HOME/.claude-personal" \
  -- claude --model opus --effort high
```

That env is not optional. Herdr's Claude hook lives in `~/.claude-personal/hooks/`, so a
session started without it never reports its state and every later `agent wait` blocks
until it times out. That failure looks like a hung agent, not a misconfiguration. Confirm
the integration first:

```bash
herdr integration status | grep '^claude:'
```

Read the new pane id back and keep it. Submitting a prompt needs it:

```bash
herdr agent get reviewer
```

Submit work in two calls. `agent send` writes literal text and does not press Enter:

```bash
herdr agent send reviewer "Review the current diff and report only actionable findings."
herdr pane send-keys <agent-pane-id> Enter
```

Then wait on the state. `agent wait` takes `--status`, not `--until`:

```bash
herdr agent wait reviewer --status idle --timeout 120000
```

A settled `idle` is not proof of a finished handoff. It only means the agent stopped. Read
the output and confirm the expected artifact exists before starting a dependent turn.

Wait for `blocked` when you expect the agent to ask for input:

```bash
herdr agent wait reviewer --status blocked --timeout 120000
```

An agent sitting at an approval or question dialog will not act on new text. Inspect it
and ask the user before answering the dialog.

Interactive UI keys go through the pane, not the agent:

```bash
herdr pane send-keys <agent-pane-id> esc
herdr pane send-keys <agent-pane-id> ctrl+c
```

Herdr validates all keys before writing any bytes. Read the result through the resolved agent:

```bash
herdr agent get reviewer
herdr agent read reviewer --source recent-unwrapped --lines 120
```

If a wait fails or returns `blocked`, inspect `agent get` and `agent read` before deciding what input to send. A timeout or stalled response does not prove the prompt was never delivered; do not blindly submit it again. Use the pane surface only when raw terminal control is intentional.

## Run an ordinary command in another pane

Create a sibling pane with the same geometry rule, preserve the caller's working directory, and keep user focus unchanged:

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus
```

Read the new pane ID from `.result.pane.pane_id`, then run and inspect the command:

```bash
herdr pane run <returned-pane-id> ".venv/bin/python -m pytest tests/test_smoke.py -q"
herdr wait output <returned-pane-id> --match "passed" --timeout 120000
herdr pane read <returned-pane-id> --source recent-unwrapped --lines 120
```

`pane run` atomically sends command text and Enter. The waiter is `herdr wait output`, not
`herdr pane wait-output`. It searches the selected snapshot immediately, so output that
already exists can match. `--match <text>` is a literal substring; add the `--regex` flag
to read that same `--match` value as a regular expression. Omitting `--timeout` allows an
indefinite wait.

Use the read source that matches the task:

- `visible`: the currently rendered viewport.
- `recent`: recent rendered output, including soft wraps.
- `recent-unwrapped`: recent output with soft wraps joined; prefer it for logs and transcripts.

Use `--format ansi` when colors and terminal styling are evidence. Otherwise use text.

`--lines` asks Herdr for more rows from the pane's available screen and host scrollback. If increasing it does not reveal more of a completed response, the pane is probably running the agent on the terminal's alternate screen. Rows that leave the alternate screen do not enter Herdr's host scrollback, so a larger line count cannot recover them.

After that failed read, ask the agent to write its complete response as Markdown in a temporary directory and reply only with the file path, then read the file directly. Use this only as a fallback; do not request file output in the initial prompt.

## Safety and coordination rules

- Use `--no-focus` for background work unless the user asked to switch context.
- Use `--current`, an explicit pane ID, or a unique agent name. Do not rely on another client's focused pane.
- Parse IDs from JSON responses. Do not derive them from sidebar order or examples.
- Do not close workspaces, tabs, panes, or sessions you did not create unless the user explicitly asked. `workspace close --group` closes the primary workspace and its linked worktree workspaces; never add it merely to bypass `workspace_group_close_required`.
- Use `--trust-repository` only after the user has verified the repository. It grants per-request Git trust; it is not a routine retry for a failed worktree command.
- Client and server versions can differ after an update. Check `herdr status` before relying on new server features. A missing method is not permission to stop or upgrade a server.
- Never run `herdr server stop` from an active session unless the user explicitly intends to stop the server and its pane processes.
- Never kill the main Herdr process. Use named test sessions for experiments that need an isolated server.
- CLI server errors are JSON on stderr with exit status 1. CLI syntax errors exit with status 2.
