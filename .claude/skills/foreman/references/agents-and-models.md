# Agents and models

Read this before starting any session.

## One harness: Claude Code on the personal config

Every Foreman role runs Claude Code, started through the `claudepersonal`
configuration. There is no second harness. Pi, OpenCode, and OpenRouter are not used by
this skill and must not be started by it.

`claudepersonal` is a shell alias:

```text
claudepersonal = CLAUDE_CONFIG_DIR=~/.claude-personal claude
```

An alias only exists in an interactive shell. Herdr spawns the program directly, so the
alias never resolves. Pass the variable instead, with an absolute path:

```bash
--env CLAUDE_CONFIG_DIR="$HOME/.claude-personal"
```

That is not a style preference. Herdr's Claude integration hook is installed inside the
personal config directory:

```text
~/.claude-personal/hooks/herdr-agent-state.sh
~/.claude-personal/settings.json   -> hooks.SessionStart runs it
```

A Claude session started without `CLAUDE_CONFIG_DIR` reads `~/.claude`, never fires that
hook, and never reports its state. `herdr agent wait --status idle` then blocks until it
times out, and Foreman cannot drive a turn. Missing env is a silent hang, not an error.

Check the integration before starting any agent:

```bash
herdr integration status | grep '^claude:'
```

It must read `current` and the path must be under `.claude-personal`. If it does not,
install it from a shell that already has the variable set:

```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-personal" herdr integration install claude
```

If the integration cannot be made current, stop and report it. Do not fall back to
another agent kind.

## Starting a session

The installed CLI is authoritative. On herdr 0.7.4 `herdr agent start` takes the full
command line after `--`, and has no `--kind` and no `--pane`:

```text
herdr agent start <name> [--cwd PATH] [--workspace ID] [--tab ID]
                         [--split right|down] [--env KEY=VALUE]
                         [--focus|--no-focus] -- <argv...>
```

So the program name is part of `argv`:

```bash
herdr agent start <maker-name> \
  --tab <team-tab-id> --cwd <maker-path> --split right --no-focus \
  --env CLAUDE_CONFIG_DIR="$HOME/.claude-personal" \
  -- claude --model opus --effort high

herdr agent start <checker-name> \
  --tab <team-tab-id> --cwd <checker-path> --split right --no-focus \
  --env CLAUDE_CONFIG_DIR="$HOME/.claude-personal" \
  -- claude --model opus --effort high
```

Re-verify the flag names with `herdr agent start --help` before a mission. If they differ
from the block above, follow the installed CLI and fix this file in the same change.

These are normal interactive sessions. Never use `claude -p`, `claude --print`, or any
other one-shot runner to force a model or a level.

## Model and effort by role

Claude aliases: `opus`, `sonnet`, `haiku`, `fable`. Effort levels: `low`, `medium`,
`high`, `xhigh`, `max`.

The rule behind the table: **judgment goes to Opus, volume goes to Sonnet, search goes to
Haiku, visual work goes to Fable.** Where the decision matters more than the output,
spend. Where the decision is already made and only needs typing, do not.

| Role         | Group    | Model    | Effort   | Why                                                       |
| ------------ | -------- | -------- | -------- | --------------------------------------------------------- |
| implementer  | making   | `opus`   | `high`   | complex or logic-bearing changes; the default             |
| implementer  | making   | `sonnet` | `medium` | only when `SPEC.md` is fully specified and a pattern exists |
| implementer  | making   | `fable`  | `high`   | frontend work whose acceptance criteria are visual        |
| investigator | making   | `opus`   | `high`   | root cause, not symptom                                   |
| tracer       | making   | `sonnet` | `medium` | multi-hop reading against a fixed question                |
| researcher   | making   | `opus`   | `high`   | synthesis and the recommendation                          |
| integrator   | making   | `sonnet` | `medium` | applies decisions already accepted                        |
| reviewer     | checking | `opus`   | `high`   | review is pure judgment                                   |
| verifier     | checking | `opus`   | `high`   | must reproduce, not agree                                 |
| critic       | checking | `opus`   | `high`   | must find the unasked question                            |
| second checker | expansion | `opus` | `xhigh`  | added only because the first pass disagreed                |
| final reviewer | expansion | `opus` | `xhigh`  | last gate before a PR                                      |
| scout        | expansion | `haiku` | `low`    | wide fan-out search; returns paths, not verdicts           |

Raise the implementer and the reviewer to `xhigh` when the work unit touches
authentication, authorization, session or token handling, concurrency, or a database
migration. Those are the cases where a cheap wrong answer is most expensive.

Reserve `max` for one situation: an escalated disagreement that a second checker did not
settle, run by a final reviewer. Running everything at `max` buys nothing and hides which
decision actually needed it.

Foreman itself runs `opus` at `high`. It holds the plan, the ledger, and acceptance.

### Choosing the implementer model

`opus` is the default. Drop to `sonnet` only when all of these are true:

- `SPEC.md` names the exact files and the exact change
- the repository already contains the pattern to copy
- no security boundary, no schema change, no concurrency
- the acceptance criteria are mechanical

If any one of them is false, use `opus`. When in doubt, use `opus`. A rejected patch costs
more than the difference in model.

Use `fable` when the acceptance criteria are about how something looks or moves:
animation, transition timing, layout, or theme work. Keep `opus` or `sonnet` for the logic
behind that surface, and split the work unit if a task has both.

## Confirm after start

`herdr agent start` returning `idle` proves nothing about the model or the level. Read the
session and confirm both before sending any brief:

```bash
herdr agent get <name>
herdr agent read <name> --source recent-unwrapped --lines 40
```

Confirm three things, in this order:

1. the session reports state to Herdr at all. If `herdr agent get` never leaves `unknown`,
   `CLAUDE_CONFIG_DIR` did not reach the process. Stop and fix the env, not the prompt.
2. the model line names the model this table assigns to the role.
3. the effort line names the assigned level.

A session running the wrong model or the wrong level is restarted, not corrected mid-turn.
Record the model and the effort of every agent in `ledger.md` at start time, so an
escalation can name what produced each position.

## Delivering a brief

`herdr agent prompt` does not exist on the installed CLI. Write the prompt block to
`briefs/` first, then send the pointer and submit it:

```bash
herdr agent send <agent-name> "Read <absolute-brief-path>, acknowledge with the envelope, then do only that assignment."
herdr pane send-keys <pane-id> Enter
```

`herdr agent send` writes literal text and does not submit. The Enter is a separate call,
so keep each agent's pane id in `ledger.md` next to its name. Get it from
`herdr agent get <name>` right after start.
