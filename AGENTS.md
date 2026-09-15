# AGENTS.md — PadyarAIChatbot

## Product Principle

This application must be usable by anyone — from a kid to an elderly person. No special knowledge required.

- If a feature needs an explanation, simplify it.
- If a user has to think about what to do next, the UI is wrong.
- Every screen should be understandable in under 3 seconds.
- Every action should be completable in under 3 clicks where practical.
- No jargon or technical terms in user-facing UI.
- Defaults should just work; advanced options stay hidden unless needed.
- When in doubt: simplify, remove, hide, auto-detect, default.

## Communication

Malik-e product (Sina) Finglish minevise. Javab-ha HAMESH Finglish ast. Faghat baraye chat — code, commit, docs va tests zaban-e khod ra estefade kon.

## Source of Truth

This file contains repository-wide engineering and product rules. `CLAUDE.md` contains Claude-specific execution guidance and project orientation. Detailed standards live under `docs/engineering/`.

When documents conflict:
1. Current code and tests establish the current state.
2. `AGENTS.md` establishes binding engineering rules.
3. `docs/engineering/` explains the standards and rationale.
4. `CLAUDE.md` explains execution workflow and tool routing.

Never document a target architecture as if it were already implemented.

## Engineering Judgment

The user's requested implementation is an input, not an architectural command.

Agents MUST preserve the intended outcome, but may reject or change the proposed mechanism when it conflicts with:
- security;
- correctness;
- maintainability;
- scalability;
- accessibility;
- existing repository patterns;
- product UX;
- operational reliability.

Before implementing a requested change, inspect the surrounding code and identify the actual problem. Do not blindly comply with the first proposed solution.

### Compliance Is Not Engineering

A request such as "add a header so the conversation survives" must trigger investigation before implementation:
- Why is conversation state currently lost?
- Is a cookie coupled to the wrong lifecycle?
- Should conversation be a first-class server resource?
- Is the API client expected to be independent of browser state?
- Is there already an established session/conversation pattern?

Fix the root problem when the evidence supports it. Do not add a compensating mechanism merely because it satisfies the wording of the request.

## Avoid Over-Engineering

Senior engineering does not mean maximum abstraction. Prefer the simplest design that satisfies the requirement while remaining secure, maintainable, testable, observable and consistent with the system.

Do NOT:
- add abstractions without a concrete reuse or isolation need;
- introduce configuration for behavior that can be safely auto-detected;
- add feature flags for trivial or permanent behavior;
- create one-off frameworks around a single use case;
- duplicate existing patterns when a stable repository pattern already exists;
- build speculative extensibility with no current requirement;
- split code into extra files/classes merely to make a change look architectural.

The goal is minimum necessary complexity, not minimum lines of code.

## STOP CONDITIONS

Pause and reassess before proceeding when any of these appears:
- a new special-case conditional is being added;
- client-specific or browser-specific branches appear without a documented reason;
- logic is being duplicated instead of reusing an established pattern;
- a new abstraction exists only for one call site;
- a security rule is being bypassed to make a feature work;
- destructive or irreversible database changes are proposed;
- a new cookie/session mechanism is introduced without understanding the existing lifecycle;
- a feature is being built without a real end-to-end scenario;
- a UI is being implemented before the user flow is understood;
- documentation describes machinery that does not exist in code;
- a capability is added without its production caller/consumer.

At a stop condition, investigate the root cause and either reuse an existing pattern, simplify the design, or explicitly document why a new pattern is necessary.

## Feature Shipping Standard

A feature is a scenario, not a capability.

Before implementation, name the real scenario: who triggers it, from where, under what conditions, and what successful completion looks like. The scenario — not the mechanism — is what gets tested and reviewed.

### Default workflow: spike → prototype → spec → wire → verify

1. **Spike** (optional, timeboxed) — de-risk an unknown. Throwaway code only.
2. **Prototype** — build the thinnest vertical slice that exercises the real scenario end-to-end.
3. **Spec** — document what is actually shipping. Planned items must be clearly marked as planned.
4. **Wire** — every new parameter, function, endpoint, setting, cookie or table must have its production caller/consumer in the same change.
5. **Verify** — add a test that fails if the production wiring is removed or broken.

For small bugfixes, use the same judgment and wiring checks without forcing unnecessary ceremony.

### Reader–writer pairs must close

Examples:
- cookie read ↔ cookie set;
- token validated ↔ token lifecycle defined;
- secret saved ↔ secret reachable by its consumer;
- admin page ↔ data API;
- sidebar link ↔ route;
- docs ↔ actual code;
- health/status ↔ real serving capability.

A green configuration state with zero reachable routes is a defect.

## Feature Documentation Workflow

For new features, the agent must choose the minimum appropriate documentation workflow. The user does not need to specify which artifacts to create.

Standard lifecycle:

`Request → PRD → [Spike] → Spec → [UX/ADR] → Plan → Tasks → Implementation → Verification → Docs`

Optional phases are used only when justified by ambiguity, uncertainty, user impact or architectural significance.

- **PRD** — why, who, outcome, scope and acceptance.
- **Spike** — timeboxed investigation of meaningful technical uncertainty.
- **Spec** — exact behavior, contracts, states and edge cases that will ship.
- **ADR** — durable record of a material architectural decision and trade-offs.
- **Plan** — repository-specific implementation approach, sequencing and verification.
- **Tasks** — atomic executable work derived from the plan.

Templates live under `docs/engineering/templates/`. Full rules and decision guidance live in `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md`.

Do not create documents for ceremony. Do not skip a document when its absence would make a material product or architecture decision impossible to review or reproduce. Update existing authoritative documents instead of creating duplicates.

## UI/UX Engineering Standards

UI/UX is product engineering, not decoration. A feature is not complete when its API works; it is complete when users can understand and successfully use it.

### Mandatory UI/UX workflow

For every user-facing feature or browser UI change:

1. Inspect existing screens, components, themes, typography, spacing and interaction patterns.
2. Load and use the repository's UI/UX skill — including **UI-UX Pro Max when installed** — BEFORE implementation.
3. Define the user flow and interaction model before writing UI code.
4. Reuse established patterns unless there is a strong reason to introduce a new one.
5. Implement all meaningful states: default, loading, success, error, empty, disabled, validation, permission, long content and narrow/mobile layouts where applicable.
6. Verify behavior with the repository's browser/e2e tooling.
7. Perform the UI/UX review again after implementation and fix findings before calling the work complete.

Do not wait for the user to say "use UI-UX Pro Max". This is mandatory for user-facing work.

### Do not blindly implement UI requests

If the request says "add a notification", do not assume that means "create a notification page". Determine the correct interaction from the intended outcome and existing product patterns. Consider whether the right solution is a toast, inline status, badge, notification center, modal, email/push message, or another pattern.

The agent may choose a better interaction than the literal request when it better satisfies the intended outcome.

### UI/UX skill is not absolute authority

UI-UX Pro Max and other design skills are decision support, not a replacement for product requirements or repository conventions.

The priority is:
1. product intent and real user scenario;
2. accessibility and correctness;
3. established repository design system;
4. UI/UX skill recommendations;
5. visual novelty.

Do not introduce a visually attractive pattern that conflicts with the product or existing system merely because a design skill suggested it.

### UX review questions

Before implementation, resolve:
- Who is the user?
- What are they trying to accomplish?
- Where do they naturally enter the flow?
- What should happen before, during and after the action?
- What happens on success, failure, empty data, loading and permission denial?
- What happens with many items or long content?
- How does the flow behave on mobile/narrow screens?
- Can it be used with keyboard and assistive technology?
- Is the copy understandable without technical knowledge?

Do not ask the user for every small design decision. Use existing patterns and sound product judgment. Ask only when ambiguity materially changes scope, behavior or product intent.

## Security and Authentication: Target State vs Current State

For **new API authentication designs**, the target standard is explicit bearer-style authentication rather than introducing new browser-cookie authentication.

This is a target standard, not a claim about the current codebase.

### Current-state exceptions

The current repository contains established mechanisms that predate this standard, including:
- admin cookie sessions;
- HMAC-signed chat tokens;
- visitor/session cookies where already required by the current product flow.

These are documented as current-state mechanisms and must not be silently rewritten merely to make documentation appear compliant.

Rules:
- New API work must not introduce another authentication mechanism without an architectural decision.
- Existing authentication mechanisms remain supported until an explicit migration is planned and tested.
- Security-sensitive changes must inspect the current authentication/session lifecycle before modifying it.
- A migration from legacy/current mechanisms to the target standard is separate work unless explicitly included in scope.

## Testing and Verification

Tests should prove the real scenario and the production wiring, not merely exercise an isolated helper.

For browser-visible changes, use the repository's browser/e2e tooling. For backend changes, choose test depth based on risk and failure impact.

CI is the merge signal. Local checks are useful for fast feedback but must not be represented as stronger evidence than they are.

## Repository-Specific Baseline

The application is PadyarAIChatbot: a per-customer CMS for AI chatbots.

- Python 3.10+
- FastAPI + Uvicorn
- PostgreSQL 16 in production; SQLite for tests/rollback artifact
- Vanilla HTML/CSS/JS chat UI; Tabler/Bootstrap 5 RTL admin
- BM25 + local model2vec embeddings + feature reranker
- Persian normalization and Vazirmatn
- Modular feature registry with core and optional modules
- White-label branding via `whitelabel_*` settings and theme tokens

Read the existing project-specific sections in `CLAUDE.md` and the relevant engineering document before changing architecture, security, database, retrieval, modules or themes.
