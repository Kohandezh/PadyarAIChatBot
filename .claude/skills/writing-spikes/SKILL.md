---
name: writing-spikes
description: Use when starting a non-trivial feature or facing a "which X should we use / how should we build Y" question in PadyarAIChatbot, where real uncertainty has to be settled with evidence before a spec or any code. Drives the research step: frame one question, gather evidence-based options (codebase verification first, then outside research), write it to docs/features/<slug>/RESEARCH.md from docs/engineering/templates/SPIKE.md, and route the outcome to a SPEC, an ADR in docs/engineering/DECISIONS.md, a PLAN, or no action. Reach for it whenever you would otherwise jump straight into designing or building, or when the user says "write a spike", "research an approach", "compare options", "should we use A or B", or "do a technical investigation". Covers only the research step, not the spec that follows.
---

# Writing Spikes

A spike answers **what we do not know yet**, with evidence, before anyone designs or builds. In this repo it lands in `docs/features/<slug>/RESEARCH.md`.

This skill is the **method** for producing a spike that a reviewer can trust, and for **routing its outcome**. The repo already owns the shape and the rules. Read these and treat them as the source of truth rather than duplicating them:

- `docs/engineering/templates/SPIKE.md`: the section structure to fill.
- `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md`: when a spike is needed at all, and its exit criteria.
- `docs/engineering/ENGINEERING_CONSTITUTION.md` plus the topic standards (`API_STANDARDS.md`, `SECURITY.md`, `DATABASE.md`, `TESTING.md`): the constraints any recommendation has to respect.
- `CLAUDE.md`: the product rule that outranks everything. The app must be usable by a child or an elderly person. A recommendation that adds a setting, a step, or a word of jargon to a visitor screen is fighting the product principle and has to say so.

**Scope:** this skill ends at a complete `RESEARCH.md` with a recommendation and one outcome. It does not write the spec and it does not write the ADR. It decides which comes next and hands off.

## First: does this need a spike at all?

`FEATURE_DEVELOPMENT_WORKFLOW.md` is explicit that process scales with risk, and that the agent must not create documents to satisfy ceremony. A spike is for **meaningful uncertainty**:

- an unfamiliar dependency or external API,
- performance or scale uncertainty,
- compatibility risk,
- unclear behaviour in existing infrastructure,
- a risky integration,
- feasibility that cannot be decided by reading the repository.

If reading the code answers the question, read the code and skip the spike. Say so in one line. A tiny bugfix, a copy change, or a feature that reuses an existing pattern goes straight to implementation.

## The process

### 1. Frame one question

One decision per spike. If you are tempted to answer two, split them. Write it as a specific question ("Which X fits this install for Y?", "How should we build Z?") and capture the sub-questions that surface while investigating.

List the **constraints the answer must respect**. In this repo those are usually:

- **Runtime**: Python 3.10+, FastAPI, one process per install, PostgreSQL 16 in production.
- **Install model**: a CMS installed per customer, not multi-tenant SaaS. Every new feature is an optional module first (`is_core=False` in `app/modules/registry.py`).
- **Cost and offline**: the local tiers (BM25, model2vec embeddings, intent head) must keep working with the AI provider switched off. An answer that only works online is a regression.
- **Security**: every endpoint authenticates and authorizes on its own. Any unbounded collection paginates.
- **Kiosk threat model**: the browser is shared at a booth. "The next person inherits the last person's state" is the default bug.
- **Product**: Persian, RTL, no jargon on a visitor screen, understandable in 3 seconds.

These are not throat-clearing. They become the axes you score options against and the reason a recommendation wins.

Set a **timebox**. The template has a field for it. A spike without one sprawls.

### 2. Ground in the real codebase first

Before looking outward, map what already exists and **verify that the integration points the feature would depend on actually exist**. Read the code, do not assume. A single unchecked "the data is not available here" can invert the whole design.

Note current behaviour, the concrete hook points, the schema, and the conventions, and cite file paths with line numbers. Start from:

- `app/modules/registry.py`: does a module already own this surface?
- `app/routers/` and `app/services/`: is there already a correct pattern for it?
- `app/db/connection.py` and `migrations/`: what does the schema really look like?
- `docs/engineering/DECISIONS.md`: has this already been decided? There are 21 ADRs there. Re-opening a settled decision without saying so is the fastest way to get a spike rejected.
- `docs/features/INDEX.md` and the sibling folders in `docs/features/`: has someone already researched this?

Use the `software-architecture` and `authorization` skills when the area is unfamiliar.

### 3. Research with evidence

Fan out independent investigations and **keep the conclusions, not the raw dumps**. Dispatch subagents for noisy search so your own context stays clean. Typical lanes, pick what fits:

- **Options landscape**: what is actually available, scored for **this** stack, not generically. Python packages have to work under FastAPI with no build step and go into `requirements.txt`.
- **Prior art**: how comparable products solve it. Reuse a proven pattern rather than inventing one.
- **Production readiness**: real limits, failure modes, security exposure, cost. Not just the happy path.
- **Codebase verification**: confirm the specific hooks, limits and data the design relies on.

Use the `context7` MCP for library and framework documentation rather than memory. Library APIs change and a spike built on a remembered API is a spike built on nothing.

Hold a high bar: **evidence over assertion**. A claim without a concrete source (docs, a benchmark you ran, a file path, a prototype) is not a finding. Check load-bearing claims adversarially before you build on them, and cite sources inline (URLs and `file.py:line`). That is what lets a reviewer trust the recommendation without redoing the work.

Where a measurement decides the question, **run it**. This repo has the tooling: `scripts/run_eval.py --golden <file>` for retrieval quality, `scripts/stress_chat.py` for load, `scripts/smoke_options.py` against a running install. A measured number beats an argument.

### 4. Write it

```bash
mkdir -p docs/features/<slug>
cp docs/engineering/templates/SPIKE.md docs/features/<slug>/RESEARCH.md
```

The slug is lowercase and hyphenated, describing the feature, not a date and not a ticket number (`companies-own-table`, `grounded-selection`, `text-to-speech`). It matches the folder, and later the `SPEC.md` next to it.

Fill every section the template defines. Four carry the weight:

- **Question and Why It Matters**: the one decision, and what is blocked until it is answered.
- **Investigation**: evidence, any experiment or prototype you ran, and the constraints you discovered. Throwaway code stays throwaway. Say so explicitly, because the template's "Production Impact" section exists for exactly that trap.
- **Findings**: what the investigation actually showed, cited. Fold the production-readiness limits and the prior art in here.
- **Decision / Recommendation**: the pick, **why it wins given the stated constraints**, and an explicit "what we trade off". Every choice loses something. Name each loss and its mitigation. Also state how hard the decision is to undo, because that feeds the ADR call in step 5.

Two shorter sections still carry real weight. **Alternatives Considered** must hold real alternatives, not a foregone conclusion dressed up. **Remaining Risks / Unknowns** is where an unverified assumption belongs. Tag each assumption verified-against-code or unverified, and never promote an unverified one into a requirement.

Security has no section of its own in the template. Put it in **Constraints Discovered** or **Remaining Risks** and always answer it, even when the answer is "no new trust boundary". It feeds the escalation call in step 5, and in a kiosk product it is rarely "none".

For a dense or jargon-heavy spike, a short glossary at the end pays for itself the first time someone new reads it.

**Language.** The repo holds research docs in both English and Persian, and both are fine. Pick one per document and stay in it. Keep code, commands, file paths and error text exactly as they are in either case.

#### Diagrams

Where a section has shape rather than prose, invoke the `diagrams` skill instead of describing the shape in a paragraph.

One is close to required: the **Decision** section wants a `flowchart TB` of the pick, the gates it depends on, and where each failed gate lands. A research doc whose recommendation can only be recovered by reading the whole file has not finished its job. Drop it only when there is no contingency, no fallback and no ordering constraint, and say so in a line.

The rest are defaults, not quotas. **Context** wants a `flowchart` of the current system with proposed nodes dashed. **Alternatives Considered** wants a `quadrantChart` once you are ranking more than about five candidates on two axes. Skip either without apology when there is nothing shaped to show. A decorative diagram is worse than none.

Note before you start: `docs/` currently holds **zero** Mermaid blocks, so there is no local prior art. The `diagrams` skill carries the recipes and the verification loop.

### 5. Choose the outcome

A spike ends with a recommendation and exactly **one** outcome:

| Outcome    | When                                                                                          | Where it goes next                          |
| ---------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `→ SPEC`   | behaviour needs to be made precise across files, layers, actors or states                      | `docs/features/<slug>/SPEC.md`, via `writing-specs` |
| `→ ADR`    | the recommendation is a durable architectural decision                                          | a new `ADR-NNN` in `docs/engineering/DECISIONS.md`, then usually a spec |
| `→ PLAN`   | the approach is clear and reuses an existing pattern, so a spec would add nothing              | a plan, then implementation (`implement`)   |
| `No action` | the investigation says do not build it, or the question dissolved                             | record why, and stop                        |

Record the outcome in the template's **Production Impact** and **Related Artifacts** sections. State clearly what, if anything, from the spike becomes production work.

When the outcome is, or might be, `→ ADR`, read `references/adr-escalation.md` for the bar, the concrete triggers, and what the ADR adds. Record the escalation and the questions the ADR must settle. Do **not** write the ADR here.

### 6. Close it out

Set the template's **Status** to `Complete` (or `Abandoned`, which is a real answer). Then:

- Add or update the feature's row in `docs/features/INDEX.md`. That file is the feature status index named in CLAUDE.md's Documentation Rules table. Be honest about what the columns mean: `Spec Status` tracks the design, `Plan Status` tracks the work. Research-only, nothing built yet, is `Draft` / `Not started`.
- List the concrete **follow-up** actions and the **open questions** the downstream author still has to answer.
- Hand off. `→ SPEC` continues with `writing-specs`. `→ PLAN` goes to `implement`. `→ ADR` gets the ADR written first.

**One thing to know about RESEARCH.md in this repo.** It plays two roles. Before the work it is the research gate. After the work it often becomes the feature's durable notes, and several of them (`grounded-selection`, `companies-own-table`, `text-to-speech`) read as "here is what shipped and why". If your spike becomes that record, keep it accurate as the code changes, or delete the parts that stopped being true. A stale research doc is read as current by the next agent.

## The quality bar

A spike is doing its job when it answers **one** clear question, its findings are **evidence-backed and verified against the real code** rather than remembered, it presents **genuine alternatives**, the recommendation states **what it trades off**, a reader can name the pick and the risk it carries from the Decision section alone, throwaway prototype code is clearly separated from production work, and it ends with the **right outcome**, escalated to an ADR when the stakes warrant it.

## References

- `references/adr-escalation.md`: when a spike's recommendation needs an ADR. Read at the outcome step.
- The `diagrams` skill: the Decision diagram and any Context or Alternatives visual.
- `docs/engineering/templates/SPIKE.md`: the section structure.
- `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md`: whether a spike is warranted, and its exit criteria.
- `docs/engineering/DECISIONS.md`: 21 existing ADRs. Read the relevant ones before recommending anything that contradicts one.
- An existing research doc under `docs/features/` (`grounded-selection`, `companies-own-table`): study the depth and the way evidence is cited.
