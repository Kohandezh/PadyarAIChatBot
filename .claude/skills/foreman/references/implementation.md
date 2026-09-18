# Implementation workflow

Read this for every implementation team: bug fixes, security fixes, features, and
refactors. The maker is the implementer. The checker is the reviewer.

The implementer changes source. The reviewer never changes source. The reviewer writes its
acceptance oracle from the specification before it sees any implementation diff.

## Contacts

The implementer and the reviewer contact Foreman only. Review findings reach the
implementer through Foreman. The patch reaches the reviewer through Foreman, as a bare
diff. This keeps the oracle away from the implementer and the implementer's reasoning away
from the reviewer.

## Phase A: the reviewer writes the oracle first

Load `prompts/oracle.md`. Submit its complete contents to the reviewer, followed by the
task block: the absolute `SPEC.md` path, the oracle test path, the absolute phase-record
path under `<team-dir>/oracle/`, and any task-specific test command from the contract. Do
not tell the reviewer to resolve paths relative to its checkout. It may edit only its
oracle test and its phase record. It must not inspect the maker worktree, implementation
branch, diff, implementer pane, or implementer handoffs.

Run the oracle against the clean base. Record which cases fail and which baseline or
allow-control cases pass. An oracle that is entirely green does not prove the defect.
Send it back to the reviewer before implementation proceeds. Archive the test under
`oracle/`.

## Phase B: implementer turns

After the oracle exists, authorize turn 1. Each turn works the same way:

1. The implementer works in the maker worktree, test-first per the repository's rules.
2. It writes `handoffs/turn-N.md`: what changed, the root cause, and every verification
   command with its real output.
3. It sends `REVIEW_NEEDED` to Foreman, in the envelope.
4. It starts no other turn until Foreman authorizes the next action.

The implementer must not read the oracle, reviewer pane, reviewer report, or clean
worktree.

## Run the oracle against the patch

Copy only the oracle test or check into the maker worktree. Run it with the exact scoped command
from the contract, under the mission's heavy-suite lock when it is an integration suite.
Record which cases changed from red on the base to green on the patch. Remove the copied
oracle after the run. Restore any touched configuration and verify its hash.

## Phase C: the reviewer inspects the diff

Create `REVIEW-INPUT.diff` from the maker worktree's uncommitted diff, built with git, and
place it in the clean checker worktree. Do not include handoffs, terminal output, commit
messages, or an explanation of the implementation.

Load `prompts/review.md`. Submit its complete contents to the existing reviewer session,
followed by the task block: the absolute `SPEC.md` path, the absolute report path under
`reviews/`, and the oracle's red-to-green record. The reviewer writes its report and sends
`ACCEPTED` or `CHANGES_REQUESTED` to Foreman. The report file is the record; terminal
history is not.

## Fix loop

Foreman reads the report and decides which findings the implementer must address. It
writes them into `briefs/turn-N-fix.md`, without oracle contents, and authorizes the fix
turn with that brief. Re-run the oracle and the blind diff review after each revision.
Keep the same Herdr tab, panes, and interactive sessions.

Cap the loop at two review rounds. If material findings remain, follow **Escalation** in
`SKILL.md`.
