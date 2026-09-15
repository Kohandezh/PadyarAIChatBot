# Feature Development Workflow

## Purpose

This workflow defines how the agent decides which product and engineering artifacts are needed for a feature. It is intentionally adaptive: process must scale with risk, ambiguity and architectural impact.

The agent must **not create documents just to satisfy ceremony**. It must choose the minimum appropriate workflow while producing enough durable reasoning for future engineers and agents.

## Lifecycle

```text
Request
  ↓
PRD (when product intent/scope needs definition)
  ↓
Spike (only when meaningful uncertainty needs investigation)
  ↓
Spec (when behavior/contract needs to be made precise)
  ↓
UX Review (if user-facing)
  ↓
ADR (if a material architecture/design decision is made)
  ↓
Plan
  ↓
Tasks
  ↓
Implementation
  ↓
Verification / Review
  ↓
Docs / ADR update when durable knowledge changed
```

The normal order is:

**Request → PRD → [Spike] → Spec → [UX/ADR] → Plan → Tasks → Implementation → Verification → Docs**

Brackets mean optional phases. A phase can be skipped when the feature does not need it.

## Artifact Responsibilities

### PRD — Product Requirements Document

Answers **why, who, what outcome, and scope**.

Use when:
- the product outcome is not already unambiguous;
- scope, users, acceptance criteria or priorities need agreement;
- the feature changes a meaningful user workflow;
- multiple implementation options could satisfy the same business outcome.

Do not use a PRD for a trivial bugfix or obvious local change.

### SPIKE — Technical Discovery

Answers **what we do not know yet**.

Use only for meaningful uncertainty, such as:
- an unfamiliar dependency or API;
- performance/scalability uncertainty;
- compatibility risk;
- unclear behavior in existing infrastructure;
- a risky integration;
- architecture feasibility that cannot be decided from repository inspection.

A spike is timeboxed and should produce evidence and a decision. Throwaway code must not quietly become production code.

### SPEC — Behavior / System Contract

Answers **exactly what will ship**.

Define behavior, inputs/outputs, state transitions, edge cases, errors, permissions, data changes, compatibility and acceptance criteria as applicable.

Use when implementation would otherwise rely on implicit assumptions across multiple files, layers, actors or states.

### ADR — Architecture Decision Record

Answers **which material technical decision was made, why, and what alternatives were rejected**.

Use when a feature introduces or changes a durable architectural choice, for example:
- a new persistence model;
- a new integration boundary;
- a new authentication strategy;
- a significant API contract;
- a new cross-cutting pattern;
- a meaningful trade-off affecting future work.

Do not create an ADR for ordinary implementation details.

### PLAN — Implementation Plan

Answers **how this repository will implement the approved behavior**.

The plan should identify affected areas, sequencing, dependencies, migrations, tests, UX verification and rollout concerns. It should be implementation-oriented but not become a giant task list.

### TASKS — Atomic Work Breakdown

Answers **what concrete work gets executed**.

Tasks should be independently understandable, ordered by dependency, and small enough to implement/review. Every task should point back to the relevant requirement/spec/plan section when useful.

Do not create tasks before the plan when the work has meaningful architectural or cross-layer complexity.

## Choosing the Minimum Workflow

| Feature shape | Minimum expected workflow |
|---|---|
| Tiny bugfix / copy / isolated CSS | Inspect → Implement → Verify |
| Small self-contained feature using existing patterns | Request → Plan → Implement → Verify |
| User-facing feature with meaningful behavior | PRD/Spec as needed → UX Review → Plan → Tasks → Implement → Verify |
| Cross-layer feature | PRD → Spec → Plan → Tasks → Implement → Verify |
| Technically uncertain/risky feature | PRD/Spec → Spike → decision → Plan → Tasks → Implement → Verify |
| Material architecture change | PRD/Spec → ADR → Plan → Tasks → Implement → Verify |
| Large product feature | PRD → Spike if needed → Spec → UX/ADR if needed → Plan → Tasks → Implement → Verify → durable Docs |

This table is guidance, not a checklist. Repository context and risk determine the actual path.

## Exit Criteria

### PRD complete when
- user/problem/outcome is clear;
- scope and non-goals are explicit;
- success/acceptance criteria are understandable;
- important product ambiguities are resolved or recorded.

### Spike complete when
- the uncertainty is answered with evidence;
- important constraints are known;
- the recommended direction is clear;
- unresolved risks are explicit;
- throwaway work is clearly separated from production implementation.

### Spec complete when
- shipped behavior is unambiguous;
- important states and edge cases are defined;
- contracts and data changes are clear enough to plan;
- acceptance criteria can be verified.

### ADR complete when
- the decision is explicit;
- context and constraints are recorded;
- alternatives and trade-offs are captured;
- consequences are understood;
- the decision is discoverable by future agents.

### Plan complete when
- implementation boundaries are known;
- dependencies and sequencing are clear;
- tests and verification are planned;
- production wiring is accounted for;
- no task exists without a reason tied to the approved outcome.

### Tasks complete when
- work is actionable;
- dependencies are ordered;
- each task has a clear done condition;
- no task is merely a vague restatement of the feature.

## Documentation Rules

- Prefer one authoritative artifact over duplicated summaries.
- PRD/specs describe product behavior; ADRs preserve durable architecture decisions; plans/tasks describe execution.
- Temporary discovery material may be archived or removed after the decision if it has no durable value.
- Durable architectural decisions must survive beyond the feature branch.
- Do not document planned machinery as shipped behavior.
- After implementation, update durable documentation only when the feature changes knowledge future engineers/agents need.
- If an existing document already owns the information, update it instead of creating another document.

## Agent Rule

The user does **not** need to tell the agent which artifact to create. The agent should inspect the request, repository, uncertainty, user impact and architectural risk, then select the minimum appropriate workflow.

If skipping a normally expected artifact would make the decision difficult to review or reproduce, create it. If creating it would add no useful information, skip it.
