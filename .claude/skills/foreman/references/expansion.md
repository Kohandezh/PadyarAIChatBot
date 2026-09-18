# Expansion

Expansion adds a session beyond a team's maker and checker. Only Foreman adds sessions. A
team asks with `EXPAND_REQUEST` and names the trigger. Foreman also expands on its own
when a trigger fires.

## Triggers

Add a role only when its trigger is observable. A feeling that more review would help is
not a trigger.

| Trigger                                                                       | Role to add                     |
| ----------------------------------------------------------------------------- | ------------------------------- |
| Scope is unclear after reading the ticket and the files it names              | scout                           |
| Maker and checker still disagree after the two-round cap                      | second checker                  |
| The change touches authorization, the sync write path, billing, or migrations | final reviewer                  |
| The diff spans two or more of `app/routers/`, `app/services/`, `app/db/`, `migrations/` | final reviewer        |
| The work of two or more teams must land in one PR                             | integrator, then final reviewer |
| Two root causes both survive a debugging turn with evidence                   | second investigator             |
| Two or more research teams feed one decision                                  | synthesis critic                |
| A checker times out or produces no report twice                               | a fresh checker replacing it    |

An integrator is rare in this repository. Each root cause is its own PR, and dependent
PRs land as a stack. Add an integrator only when the user asks for one PR, or when the
work units cannot build apart.

## Roles

| Role                | Job                                                      | Sees                                                                                       | Must not                                        |
| ------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| Scout               | Maps files, dependencies, and risks so Foreman can size  | the ticket and the repository at the base                                                  | edit anything                                   |
| Second checker      | Gives an independent verdict on a disputed work unit     | `SPEC.md` and the deliverable: the diff, the diagnosis handoff, the trace, or the document | read any review report before its own verdict   |
| Final reviewer      | Checks the finished work end to end before the PR        | `SPEC.md`, the final diff or document, and gate output                                     | edit anything, or read team handoffs or reviews |
| Integrator          | Combines accepted work units that must land together     | the accepted work units only                                                               | change accepted behavior                        |
| Second investigator | Tests the competing root-cause hypothesis                | `SPEC.md`, the symptom, and the hypothesis it is assigned                                  | read the first investigator's handoff           |
| Synthesis critic    | Reconciles several research teams into one evidence base | every research team's accepted report                                                      | do primary research                             |

A second checker or final reviewer loads the team type's review prompt, with a task block
that says it did not write the blind first artifact. On an implementation team, it also
gets the oracle's red-to-green record.

A final reviewer starts after the team reviewer's `ACCEPTED` and passing gates. If it
sends `CHANGES_REQUESTED`, Foreman opens one fix turn. The team reviewer and then the final
reviewer check the revision. A second `CHANGES_REQUESTED` from the final reviewer goes to
**Escalation** in `SKILL.md`. A scout writes a report with: files
likely to change, work units it sees, dependencies, and open risks.

## Adding a session

1. Record the trigger and the new role in the ledger.
2. Check free disk per [team-sizing.md](team-sizing.md). Create the checkout the role
   needs, with `--no-focus`. Checkers and final reviewers get
   a clean detached worktree at the task base. A scout uses a detached worktree at the
   base. An integrator gets a new branch worktree.
3. Create its pane in a new tab labeled `<team-id>-<role>`, with `--no-focus`.
4. Start the session per [agents-and-models.md](agents-and-models.md). Checking roles use
   the reviewer defaults. Making roles use the implementer defaults.
5. Name it `<team-id>-<role>`. Send the new name to every session that may contact it.
6. It writes its report to `~/foreman/<mission-id>/<team-id>/<role>/`.

## Removing a session

When an expansion role's report is written and Foreman has accepted it, ask the session
to exit and close the pane and tab this mission created for it. Its worktree follows the
cleanup rules in `SKILL.md`.
