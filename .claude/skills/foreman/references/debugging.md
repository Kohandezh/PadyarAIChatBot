# Debugging and tracing workflow

Two task types share this file.

- **Debugging.** A symptom is known and the cause is not. The maker is the investigator.
  The deliverable is a diagnosis and a test that fails for the reported reason.
- **Tracing.** A question about how the code behaves, with no defect assumed. The maker is
  the tracer. The deliverable is a trace map.

The checker is the verifier in both. Both makers follow the repository's
`systematic-debugging` skill. Neither maker fixes anything.

## Contacts

The maker and the verifier contact Foreman only. Findings reach the maker through
Foreman, in `briefs/turn-N-fix.md`. The maker's work reaches the verifier through Foreman.

## Debugging

### Contract additions

`SPEC.md` for a debugging team also holds:

- the symptom exactly as reported, with reproduction steps, environment, and first-seen
  date when known
- the definition of done: the first point where observed behavior departs from intended
  behavior, named at `file:line`
- the test tier the failing test belongs to, usually integration
- the one file the investigator may create: the failing test. No other source change.

### Phase A: the verifier writes a prediction record

Load `prompts/debug-prediction.md`. Submit its complete contents to the verifier, followed
by the task block: the task type `debugging`, the absolute `SPEC.md` path, and the record
path `<team-dir>/predictions/record.md`. The verifier reads the symptom and the base
source. It lists candidate causes, and for each one, the observation that would confirm it
and the observation that would rule it out. It must not read the investigator's worktree,
pane, or handoffs.

Foreman archives the record before authorizing the investigator.

### Phase B: investigator turns

1. The investigator reproduces the symptom in its branch worktree. If it cannot, it sends
   `BLOCKED` with everything it tried.
2. It traces the symptom to its root cause.
3. It writes a test at the contract's tier. The test fails on the base, and the failure
   matches the reported symptom, not a fixture error, a typo, or an unrelated exception.
4. It writes `handoffs/turn-N.md`:
   - the causal chain as numbered hops, each with `file:line` and the evidence for it
   - the failing test path, the exact command, and its real failure output
   - the candidate causes it ruled out, and how
5. It sends `REVIEW_NEEDED` to Foreman.

### Phase C: the verifier checks the diagnosis

Copy only the failing test into the clean checker worktree. Load
`prompts/debug-verification.md`. Submit its complete contents, followed by the task block:
the task type, `SPEC.md`, the handoff path, the copied test path, the exact test command,
and the report path under `reviews/`.

The handoff is the deliverable here, so the verifier reads it. The verifier:

- runs the failing test on the base and confirms the failure matches the symptom
- resolves every hop against the base source
- compares the diagnosis with its prediction record, and names every candidate cause the
  investigator never ruled out
- tries to break the diagnosis: an input with the symptom but without the named cause, or
  the cause without the symptom

It writes its report and sends `ACCEPTED` or `CHANGES_REQUESTED` to Foreman. Foreman
removes the copied test from the clean worktree after the verdict.

### Fix loop

Same as implementation: Foreman writes `briefs/turn-N-fix.md`, and the loop caps at two
rounds. After each revision, copy the revised test again and rerun phase C.

Pick expansion roles per [expansion.md](expansion.md):

- At any turn, if a competing cause has its own evidence, such as a command output or a
  resolved hop, add a second investigator on that cause.
- At the cap, if no competing cause has evidence, add a second checker.

### After acceptance

- **The task asked for a fix.** Open an implementation work unit in the next wave. Its
  `SPEC.md` takes the accepted root cause as context and the symptom's absence as an
  acceptance criterion. The investigator may continue as the implementer, keeping its
  agent name. Start a fresh reviewer: the verifier has read the diagnosis handoff, so it
  cannot write a blind oracle. Close the verifier once its evidence is preserved. Record
  the role changes in the ledger. The investigator's failing test is the implementer's
  starting test, not the oracle.
- **The task asked only for a diagnosis.** Stop. Report the root cause, the chain, and the
  failing test. Draft any issue comment for the user to approve. Never post it.

## Tracing

### Contract additions

- the question, in the requester's words
- the entry points where the trace starts
- the boundary where the trace stops
- the output shape: numbered hops, each with `file:line` and one sentence
- no file changes by either role

### Phase A: the verifier writes a coverage checklist

Load `prompts/debug-prediction.md` with the task type `tracing` and the record path
`<team-dir>/predictions/checklist.md`. From the question and the entry points only, the
verifier lists what a complete trace must cover: every branch that changes the outcome,
error and early-return paths, asynchronous or cross-process hops, and any authorization
decision. It does not trace.

### Phase B: the tracer's turn

The tracer writes `handoffs/trace.md`: numbered hops, each with `file:line`, one
sentence, and the condition that selects that branch. It marks every hop as executed, with
the command and real output, or as read from source. It sends `REVIEW_NEEDED` to
Foreman.

### Phase C: the verifier checks the trace

Load `prompts/debug-verification.md` with the task type `tracing`. The verifier resolves
every hop against the base: the cited line does what the hop claims. A line that only
mentions a name is not a hop. The hop is the line that reads, calls, or guards it. It
closes every checklist item or names it as missing, and runs the decisive hop when a
command can execute it.

### Fix loop

Foreman writes `briefs/turn-N-fix.md` with the findings to address. After each revision,
rerun phase C. Cap the loop at two rounds. At the cap, add a second checker per
[expansion.md](expansion.md), then follow **Escalation** in `SKILL.md`.

### Delivery

The trace map stays in the team directory. If the user wants it in the repository, open a
research work unit for a handbook page.
