---
name: writing-specs
description: Use to author a feature spec in PadyarAIChatbot at docs/features/<slug>/SPEC.md, from docs/engineering/templates/SPEC.md. A spec says exactly what will ship, before any plan or code. Drives the whole loop: check the gate (is the approach already settled by a PRD, by docs/features/<slug>/RESEARCH.md, or by an existing pattern, and stop if it is not), fill all 13 template sections, give requirements stable ids (REQ/SEC/US/SC) on a large spec, fill the UX states that docs/engineering/UI_UX.md makes mandatory, self-review against references/spec-self-review.md, register the row in docs/features/INDEX.md, and take the spec Draft to Approved to Implemented. Reach for this when the user says "write a spec", "turn the research into a spec", "spec out this feature", or "what should the spec cover". Covers only the spec. The research before it belongs to writing-spikes, the plan and the code after it belong to implement.
---

# Writing Specs

A spec answers **exactly what will ship**: behaviour, inputs and outputs, state, edge cases, errors, permissions, data changes, rollout and acceptance. It is the contract between a settled approach and the code.

This skill is the **method**. The repo owns the spec's shape and rules. Read these as the source of truth rather than duplicating them:

- `docs/engineering/templates/SPEC.md`: the 13 sections to fill.
- `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md`: when a spec is needed at all, where it sits in the lifecycle, and its exit criteria.
- `docs/engineering/ENGINEERING_CONSTITUTION.md` plus `API_STANDARDS.md`, `SECURITY.md`, `DATABASE.md`, `TESTING.md`: the rules a spec may not spec its way around.
- `docs/engineering/UI_UX.md`: the mandatory gate for anything a person sees in a browser.
- `docs/features/INDEX.md`: the feature status index named in CLAUDE.md's Documentation Rules table.
- `CLAUDE.md`: the product rule that outranks style. Usable by a child or an elderly person, every screen understandable in 3 seconds, every action in 3 clicks, no jargon in user-facing text.

**Scope:** this skill produces one `SPEC.md` and takes it to Approved. It does not do the research before it (`writing-spikes`) and it does not plan or write the code after it (`implement`).

## What this repo does not have

This skill was adapted from a heavier process. These parts were dropped because the machinery is not here. Saying so plainly saves the next reader a search.

- **No RFC tier.** There is no `docs/rfc/`, no Accepted RFC to link, and no rule that a spec cannot leave Draft without one. A durable architectural decision goes into `docs/engineering/DECISIONS.md` as a new `ADR-NNN` entry (21 exist today). That is the only escalation tier here.
- **No issue tracker to link.** Work is tracked in `docs/features/<slug>/` plus GitHub issues and pull requests. A spec points at a file path, not at a ticket id.
- **No hard "resolved spike" gate.** `FEATURE_DEVELOPMENT_WORKFLOW.md` is deliberately adaptive. The spike phase is bracketed (optional), and the agent "must not create documents just to satisfy ceremony". So the research step is a judgement call, not a lock. Step 1 is how to make that call honestly, and it does still stop you when the approach is genuinely unsettled.
- **No separate quick or refactor spec template.** There is one `SPEC.md` template. Scale the depth to the change instead of switching template. (`docs/features/INDEX.md` does carry empty "Quick Features" and "Active Refactors" tables. Nothing has ever been filed in them and no template backs them. Ignore them.)
- **No separate design-approval workflow.** Review happens in the pull request, like every other change. CI on GitHub is the pass/fail gate for tests.

What survives, because it is worth keeping at any process weight: one spec per feature, requirement ids a test can cite, acceptance criteria you can observe, a self-review before anyone else reads it, and an explicit gate before the spec starts.

## Where specs live

- **`docs/features/<slug>/SPEC.md`** is the feature spec. That is what this skill writes. The folder holds one feature and may also carry `PRD.md` and `RESEARCH.md`.
- **`docs/superpowers/specs/` and `docs/superpowers/plans/`** hold a different thing: dated design and plan pairs for one-off repo-wide changes, written by the superpowers workflow (`2026-09-15-remove-inotex-from-source-design.md` and its plan). They are not feature specs. Do not put your spec there.

## The process

### 1. Check the gate, or say in the spec why it is skipped

A spec is written **from** a settled approach, not while choosing one. Before opening the template, answer two questions. Is the product outcome agreed? Is the technical approach decided on evidence?

| What you have | What to do |
| --- | --- |
| `docs/features/<slug>/PRD.md` | Read it. It settles why, who, and what outcome. Your spec makes that precise and cites it. |
| `docs/features/<slug>/RESEARCH.md`, complete, outcome `→ SPEC` | Read it in full (step 2) and link it in section 13. |
| Neither, but the change reuses a pattern already in the repo | Fine. Write one line in section 13 naming the pattern and the file it lives in. That line is the evidence. |
| Neither, and there is real uncertainty | **Stop.** Route to `writing-spikes`. |

Real uncertainty means an unfamiliar dependency, a performance or scale question, a risky integration, a compatibility risk, or behaviour in existing infrastructure nobody has checked. A spec that quietly picks an approach nobody investigated is where the expensive mistakes come from.

If a design question surfaces while you are writing the spec, that is the last row arriving late. Stop and go research it.

One more thing worth knowing. Only one feature folder has both a PRD and a SPEC (`exhibition-lead-capture`), and no folder has both a RESEARCH.md and a SPEC.md. So the common case here is a spec written from an existing pattern. That is allowed. It is not allowed silently.

### 2. Read the inputs in full

The approach is already decided. Do not reopen it. Read for:

- The **chosen approach** and the constraints it satisfies.
- **Trade-offs accepted.** These become the rationale in the spec.
- **Open questions** the research or the PRD deferred. Answer each one now, or park it explicitly in the spec.
- **Assumptions tagged unverified.** Verify them against the code before promoting one into a requirement.
- The **outcome** and any ADR it called for. If the research outcome was `→ ADR`, that ADR should already exist in `docs/engineering/DECISIONS.md` before the spec leans on it.

### 3. Ask clarifying questions, one at a time

Research answers **how**. A PRD answers **why**. A spec still needs **what**: behaviour, states, limits, acceptance. Find the gaps, then ask one question at a time, preferring multiple choice when the answer space is bounded:

- Which module owns this? A new feature is an optional module first (`is_core=False` in `app/modules/registry.py`), and the spec has to say what an install without it sees.
- What does the visitor see when it fails, when it is empty, and when it is slow? A kiosk visitor cannot read an error code.
- Does any of this survive between two people at the same booth browser? That is the kiosk threat model and it needs an explicit answer.
- What is explicitly out of scope, so it does not creep in at review?

Do not ask about the approach. That is settled. Do not ask several questions at once.

The product owner writes in Finglish, so expect the answers in Finglish. The spec itself goes in English or Persian, one language per document, matching its sibling docs.

### 4. Create the file

```bash
mkdir -p docs/features/<slug>
cp docs/engineering/templates/SPEC.md docs/features/<slug>/SPEC.md
```

The slug is lowercase and hyphenated, describing the feature, not a date and not an issue number (`metrics-endpoint`, `pet-characters`, `critical-watchdog`). It matches the folder, and the `PRD.md` or `RESEARCH.md` beside it if there is one.

### 5. Fill every section

Fill all 13 sections the template defines. Do not leave placeholders and do not drop a section because it felt empty. Section numbers below are the template's.

**Header.** The template says `Status`, `Owner`, `Date`, with the lifecycle `Draft → Approved → Implemented`, plus `Superseded` for a spec a later one replaced.

The five newest specs use a different header: a `| Field | Value |` table with Created, Updated, Status, Domain, Author, Sources. The template and the shipped specs disagree and the repo never resolved it. **This skill picks the field table.** Its Status, Domain, Created and Updated line up one to one with the row you add to `docs/features/INDEX.md` in step 7, and its Sources row is where the gate evidence from step 1 naturally goes. Keep the template's four status values. That is a convention this skill is setting, not one it found.

**1. Purpose.** One to three sentences. What exact behaviour is being specified, and why it matters to a real user.

**2. Scope.** In scope is what this spec delivers. Out of scope is the explicit exclusions, phrased as exclusions ("this phase does not support X"). They are what stops a reviewer asking "why didn't you include X?". Point each one at a follow-up when there is one.

**3. Actors / Permissions.** Who can do this, and which door they come through. Name the real mechanism: `verify_admin` for an admin endpoint, the chat trio (HMAC chat token, Origin/Referer allowlist, per-IP rate limit) for a visitor endpoint, and for the leads module the specific one of its three doors. Constitution rule: every endpoint authenticates and authorizes independently, and never infers authorization from possession of a resource id.

**4. User / System Flow.** The ordered path from trigger to result. This is where a reader most needs to see shape rather than read about it. See "Diagrams" below.

**5. Behavior.** Inputs, outputs, state transitions. Be testable. "The system should handle errors gracefully" is not a requirement. "A request without a visitor session cookie returns 401 with a Persian body and sets no cookie" is.

**6. API / Contract.** Endpoints, request and response shapes, status codes. Follow `docs/engineering/API_STANDARDS.md`. Any endpoint returning an unbounded collection must define its paging. That is a constitution rule, not a nice-to-have.

**7. Data Model / Persistence.** Tables, fields, indexes, lifecycle. A schema change lands in **two** places: a new numbered file in `migrations/` for PostgreSQL, and a mirror in `init_db()` in `app/db/connection.py` for the SQLite test backend. Name both. Say whether the migration is additive. There is no downgrade path, so rollback means restoring a backup.

**8. Error / Edge Cases.** Duplicate request, concurrent request, timeout, partial failure, missing resource. Name the visitor-facing wording for each, because "a Persian sentence a stranger understands" is part of the contract in this product.

**9. Security / Privacy.** Never empty. An empty security section is a red flag at review. Carry over whatever the research found, and answer the kiosk question explicitly: what does the next person at this browser inherit? "Nothing, the session is cleared on X" is a fine answer. Silence is not.

**10. UX States.** The template lists ten: default, loading, success, empty, error, disabled, permission denied, long content / overflow, mobile / narrow viewport, accessibility. `docs/engineering/UI_UX.md` makes this a mandatory gate for any browser change, so fill every line for a user-facing feature. "N/A, this endpoint has no UI" is a valid answer for a backend-only spec. A blank line is not.

**11. Compatibility / Rollout.** Who depends on the current behaviour, and what breaks. Name the existing callers you actually checked. Say whether the module is core or optional, and what an install without it sees.

**12. Acceptance Criteria.** A checklist of observable outcomes. Each one is binary and something a pytest could check. "The export finishes within 30 seconds for a 10,000-row dataset" is observable. "Exports are reasonably fast" is not. Most shipped specs here also carry a short **Tests** section naming the test files that will prove it. Add one after section 12 when it helps.

**13. Related Artifacts.** PRD, Spike, ADR, Plan. Put the step 1 gate evidence here: the research path, or the one line naming the reused pattern.

Three specs (`critical-watchdog`, `release-process`, `freshness-schedule`) add a "Wiring: reader/writer pairs" section listing what writes a value and what reads it. That serves constitution rule 5: a capability not wired to a real production caller is incomplete, and reader/writer pairs must close in the same change. Copy it when your feature has a pair that could drift.

**A warning about the shipped specs.** Most of them do not follow this template. They use their own headings (Scenario, What shipped, Wiring, Tests) because they were written after the code shipped, as a record, not before it as a contract. Read them to calibrate depth and tone. Do not copy their structure. Follow the template.

#### Requirement ids

On a large spec, give requirements stable ids so a reviewer, a test and a pull request can all point at the same line.

| Prefix | For |
| --- | --- |
| `REQ-NNN` | a functional requirement |
| `SEC-NNN` | a security requirement |
| `US-NNN` | a user story |
| `SC-NNN` | a success criterion, binary pass or fail |

Use "must" for non-negotiable and "should" only when something is genuinely optional. Every requirement is a testable statement.

Only one spec in the repo uses ids: `docs/features/exhibition-lead-capture/SPEC.md`, with 86 REQ, 36 SEC, 16 SC and 15 US. It groups its REQ ids by area, each group a separately mergeable piece of work. Copy that grouping for a large feature. It pads REQ, SEC and SC to three digits but writes `US-1`. **Pad all four to three digits**, a convention this skill is setting so the ids sort.

For a small spec the ids are overhead. `pet-characters` and `brand-dynamic-shell` are both under 70 lines and use none. Skip them and keep the prose testable. Do not pad a small spec into a large one, and never half-apply ids. Some requirements numbered and some not is worse than none.

#### Diagrams

Invoke the `diagrams` skill where a section has shape rather than prose. It decides whether a diagram earns its place, picks the type, draws it from the code rather than from memory, and checks it reads before commit.

- **4. User / System Flow**: a `flowchart` of the change surface, or a `sequenceDiagram` when the flow crosses three or more participants (browser, router, service, model, database).
- **5. Behavior**: a `stateDiagram-v2` when an entity gains a lifecycle.
- **7. Data Model**: an `erDiagram` when the spec adds or reshapes tables.

Proposed components must look different from shipped ones (dashed), so a reviewer never has to guess which half already exists. `docs/` currently holds zero Mermaid blocks, so there is no local prior art to copy. The `diagrams` skill carries the recipes.

### 6. Self-review before anyone else reads it

Read `references/spec-self-review.md` for the full checklist. The four gates:

1. **Placeholder scan.** No "TBD", no empty template section, no vague requirement.
2. **Internal consistency.** The flow matches the behaviour, the acceptance criteria trace back to requirements, the out-of-scope list does not contradict the in-scope list.
3. **Scope check.** The spec is deliverable as a realistic set of scoped pull requests, each one root cause. If it clearly spans two independent areas with separate lifecycles, split it.
4. **Ambiguity check.** Every requirement can be read only one way by an implementer who has not seen the research.

Fix every finding inline. Do not hand over a spec with known gaps.

### 7. Register it in INDEX.md

Add or update the feature's row in the **Active Features** table of `docs/features/INDEX.md`, in the **same commit** as the spec. Columns are `Slug | Spec Status | Plan Status | Domain | Created | Updated`.

- `Spec Status` tracks the design and takes the spec's own status: `Draft`, `Approved`, `Implemented`.
- `Plan Status` tracks the work. The repo never wrote down what its values mean. The ones in use are `Not started`, `In review` and `Ready`. A newly written spec with no plan yet is `Not started`.
- `Domain` is the area the feature belongs to. Existing values: `chat`, `admin`, `leads`, `registration`, `infrastructure`, `branding`, `search`, `content`, `tts`, `companion`. Reuse one rather than inventing an eleventh.

Be honest. Constitution rule 7 says documentation must describe reality, and that current state, target state and planned work must stay distinguishable. A row saying `Implemented` for something that is not implemented is worse than no row. Note that the index already lists more features than there are folders under `docs/features/`, so a row does not prove a spec exists.

### 8. Draft to Approved to Implemented

**Approved** means the reviewer agrees this is what should ship. Review happens in the pull request. Apply the change requests, re-run the self-review, then set Status to `Approved` and update the INDEX row in the same commit.

**No implementation begins until Approved.** That is the point of the gate. After that, hand off to `implement`, or write a plan from `docs/engineering/templates/PLAN.md` into `docs/features/<slug>/PLAN.md` first when the work is cross-layer.

**Implemented** is set when the code has shipped, not when the pull request opened.

If reality diverged from the spec during implementation, update the spec. A spec describing a feature that was built differently is read as current by the next agent, and it will be wrong.

## The quality bar

A spec is doing its job when the approach behind it is settled and its source is named in section 13 (a PRD, a research doc, or one line saying which existing pattern it reuses), every requirement is testable and on a large spec carries a stable id, the security section answers the kiosk question, the UX states are filled for anything a person sees, the data-model section names both the migration and the `init_db()` mirror, the acceptance criteria are observable, the work is a reviewable set of pull requests, and the INDEX row tells the truth about where it is.

The first thing a reviewer should be able to do is read Purpose and Scope and immediately know what is being built and what is deliberately not.

## References

- `references/spec-self-review.md`: the full self-review checklist. Read at step 6.
- The `diagrams` skill: the flow, state and schema diagrams. Invoked at step 5.
- `docs/engineering/templates/SPEC.md`: the 13 sections. `PRD.md` and `PLAN.md` sit either side of it.
- `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md`: whether a spec is warranted, and its exit criteria.
- `docs/engineering/UI_UX.md`: the mandatory gate and the review checklist for user-facing work.
- `docs/features/exhibition-lead-capture/SPEC.md`: the only large spec, with ids grouped into mergeable pieces, and the only feature with a PRD beside it. `docs/features/critical-watchdog/SPEC.md` (273 lines) and `docs/features/release-process/SPEC.md` (74 lines) are mid-sized. `docs/features/pet-characters/SPEC.md` (64 lines) is a small one. Calibrate depth against these, not structure.
