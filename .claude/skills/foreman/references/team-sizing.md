# Team sizing

Read this for every mission, before creating anything.

## Answer five questions per task

Answer each from evidence: the ticket, the files it names, or a scout report. Never size
from the ticket label alone.

- **Uncertainty.** Can the work be scoped now? If not, start with a scout.
- **Separability.** Does it split into root causes or questions that touch different
  files?
- **Coupling.** Would parallel teams have to agree on a shared decision or a shared file?
- **Consequence.** What does a wrong result cost?
- **Verification cost.** What evidence would make acceptance trustworthy?

## Starting shapes

| Task shape                                                                    | Teams            | Expansion planned                   |
| ----------------------------------------------------------------------------- | ---------------- | ----------------------------------- |
| One root cause in one workspace                                               | 1                | none                                |
| Several root causes in different files                                        | 1 per root cause | none; dependent PRs land as a stack |
| Scope unclear after reading the ticket and named files                        | 0 at first       | scout, then size again              |
| Authorization, sync write path, billing, migrations                           | 1 per root cause | final reviewer                      |
| Symptom known, cause unknown                                                  | 1 debugging team | none                                |
| One research decision                                                         | 1                | none                                |
| Several independent research questions for one decision                       | 1 per question   | synthesis critic                    |
| Copy text or other change with no runtime behavior, arriving with other tasks | 1                | none                                |

A planned role is recorded in the ledger at sizing time and started when its trigger
point arrives. A final reviewer starts after the team reviewer's `ACCEPTED` and passing
gates, not before.

For a change with no runtime behavior, the reviewer's oracle may be an observable check
instead of a test file, such as a rendered-string assertion with a control. `SPEC.md`
names the check. It must still be red on the base and green on the patch.

A work unit is one root cause, one question, or one document. Per the `scoped-pr` skill,
one root cause is one PR. Dependent PRs land as a stack through `merge-stacks`.

Never group two tickets into one team because they look alike. Group them only when they
share one root cause, and record why in the ledger.

Never add a team to fill capacity.

## Several tasks in one mission

1. Read every ticket before starting any session.
2. Write the ledger: one row per work unit with its type, expected files, team, and wave.
3. Build the dependency map from two sources: what each issue says it blocks, and expected file
   overlap between work units.
4. Put work units that share files, or that block one another, in separate waves. Run work
   units with disjoint files side by side.
5. Order waves: blockers first, then by priority, Urgent first.
6. When a wave's work unit is accepted, re-check the next wave's expected files against
   what actually changed before starting it.

## Limits

- **Active workers.** At most four sessions working at the same moment, excluding Foreman.
  A session that has settled and is waiting for its counterpart does not count. The user
  may change the limit. When more work is ready than slots, queue it in the ledger.
- **Disk.** A worktree with installed dependencies takes about 2 GB. Before creating
  worktrees, run `df -h ~` and compare the free space with 2 GB times the number of
  worktrees planned. A full disk fails quietly and looks like a code failure. When space
  is short, start the teams that fit in wave order, queue the rest in the ledger, and tell
  the user the shortfall. Never remove a worktree that is not yet cleaned up to make room.
- **Heavy suites.** Integration suites from different teams share one database and one
  CPU. Run each one under the mission lock, with workers capped:
  `flock ~/foreman/<mission-id>/heavy.lock <scoped test command> --maxWorkers=2`.
- **Leader context.** Foreman reads ledger rows, envelopes, and report verdicts. It reads a
  full diff or document only to settle a dispute, a failed gate, or an acceptance check.

## Resizing

- After a scout report, rewrite the plan and tell the user what changed.
- A work unit that turns out to hold two root causes splits. The second becomes a new
  queued work unit.
- A team with nothing left in its queue is cleaned up once its evidence is preserved.
- Stop adding work once every requested outcome has its evidence. Do not add teams for
  unrequested polish.
