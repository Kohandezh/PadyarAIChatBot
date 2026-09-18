# Research workflow: spikes and specs

Read this for every research team. The maker is the researcher. The checker is the
critic.

The researcher follows the repository pipeline skill that owns the artifact:
`writing-spikes` for a spike, `writing-specs` for a spec. Those skills and the templates
they name are the source of truth for the document's shape, lifecycle, and `INDEX.md`
rules. This file adds the team around them.

Plans are out of scope. Plans live in `docs/superpowers/plans/` and need the user's
authority.

## Gate before starting

A spec team starts only if the `writing-specs` gate passes. A feature spec needs a
Resolved spike, plus an Accepted RFC if the spike escalated. If the gate fails, Foreman
does not start a spec team. It turns the task into a spike work unit or reports the block
to the user.

## Contacts

The researcher and the critic contact Foreman only. Findings reach the researcher through
Foreman, in `briefs/turn-N-fix.md`. The document reaches the critic through Foreman, as a
bare diff.

## Contract additions

`SPEC.md` for a research team also holds:

- the one decision a spike settles, or the feature a spec defines, in the ticket's words
- the artifact path, `docs/features/<slug>/RESEARCH.md` for a spike or
  `docs/superpowers/specs/<slug>.md` for a spec, and its `docs/features/INDEX.md` row
- the furthest status the team may set: `In Review`. `Resolved` and `Approved` are the
  maintainer's call.
- evidence rules: a codebase claim cites `file:line` at the base; an external claim cites a
  primary source with its date
- forbidden: source code changes, issue or PR comments, and plan files under
  `docs/superpowers/plans/`

## Phase A: the critic writes the question checklist

Load `prompts/research-questions.md`. Submit its complete contents to the critic, followed
by the task block: the absolute `SPEC.md` path and the checklist path
`<team-dir>/questions/checklist.md`. From the ticket and `SPEC.md` alone, the critic
writes the questions the document must answer. For each question it records the evidence
that would settle it, and the finding that would change the recommendation. It reads code
to learn context, not to answer the questions. It must not read the researcher's worktree,
branch, pane, or handoffs.

## Phase B: researcher turns

1. The researcher writes the document in its branch worktree, per the pipeline skill.
2. It adds or updates the `INDEX.md` row in the same change.
3. Any diagram goes through the `diagrams` skill. Mermaid in markdown renders on GitHub
   with no build step, so there is nothing to run; the check is reading the diagram
   against the code.
4. It writes `handoffs/turn-N.md`: the document path, its load-bearing claims, and its open
   questions.
5. It sends `REVIEW_NEEDED` to Foreman.

## Phase C: the critic reviews the document

Create `REVIEW-INPUT.diff` from the research worktree's diff and place it in the clean
checker worktree. Load `prompts/research-critique.md`. Submit its complete contents,
followed by the task block: `SPEC.md`, the checklist path, and the report path under
`reviews/`.

The critic does not read the researcher's handoff. The document must carry its own
evidence. The critic:

- checks every checklist question is answered in the document or listed as open
- resolves every codebase claim against the base at its cited line
- checks each external claim that the recommendation depends on against its source
- flags inference stated as fact, and correlated sources presented as independent
- checks the recommendation follows from the evidence and treats alternatives fairly
- checks the pipeline skill's rules: template sections, `INDEX.md` row, and status

It writes its report and sends `ACCEPTED` or `CHANGES_REQUESTED` to Foreman.

## Fix loop

Foreman writes `briefs/turn-N-fix.md` with the findings to address. After each revision,
rebuild `REVIEW-INPUT.diff` and rerun phase C. Cap the loop at two rounds, then follow
**Escalation** in `SKILL.md`.

## Several questions for one decision

When a decision rests on independent questions, run one team per question. Each team's
deliverable is an evidence file in its team directory, not a repository document. A
synthesis critic reconciles the accepted evidence files. Then one researcher writes the
document, and its critic reviews it as above.

Give a claim to two teams when it is uncertain and would flip the decision.

## Delivery

Open a draft PR with the document and its `INDEX.md` row at `In Review`, using
`gh pr create --draft`. Report to the user: the recommendation, the open questions,
and the critic's verdict.
