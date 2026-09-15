# CLAUDE.md — PadyarAIChatbot

This file is the Claude-specific execution guide. Repository-wide rules live in `AGENTS.md`; do not duplicate them here.

## Mandatory First Step

Read `AGENTS.md` before making changes.

Then load the skill BEFORE writing code. A skill read afterwards cannot influence the design that was already implemented.

If a skill contradicts the actual repository, treat the skill as stale and fix the skill or document the discrepancy rather than writing code against a false assumption.

## Project Orientation

PadyarAIChatbot is a per-customer CMS for AI chatbots. Each installation exposes core functionality plus selected optional modules. Customers manage content, branding and operational settings through the admin panel.

Current baseline:
- Python 3.10+
- FastAPI + Uvicorn
- PostgreSQL 16 production
- SQLite test/rollback artifact
- Vanilla HTML/CSS/JS chat UI
- Tabler UI / Bootstrap 5 RTL admin
- BM25 + model2vec embeddings + feature reranker
- Persian normalization
- Vazirmatn

The existing repository structure and detailed architecture remain authoritative for implementation-specific facts. Do not replace project knowledge with generic templates.

## Skill Routing

| Work | Load first |
|---|---|
| Any user-facing/browser change | repository UI/UX skill + **UI-UX Pro Max when installed** + `e2e-test-gen` + `playwright-cli` |
| New feature/refactor | `implement` |
| Auth, sessions, cookies, tokens, access control | `authorization` + security review |
| New endpoint | `api-test` |
| New service/util/auth function | `write-tests` |
| Debugging | `systematic-debugging` + relevant engineering debugging skill |
| Architecture decision | `engineering:architecture` |
| Subsystem design | `engineering:system-design` |
| Testing strategy | `engineering:testing-strategy` |
| Release | `engineering:deploy-checklist` |
| Production incident | `engineering:incident-response` |
| Refactor / technical debt | `engineering:tech-debt` |
| PR / commit | `scoped-pr` + `commit` |
| Code review | `code-review` + specialist reviewer |
| Product specification | `product-management:write-spec` |
| Product brainstorming | `product-management:product-brainstorming` |

## Feature Development Workflow

For a new feature, determine the minimum appropriate artifact chain before implementation:

`Request → PRD → [Spike] → Spec → [UX/ADR] → Plan → Tasks → Implementation → Verification → Docs`

Do not force every feature through every phase. Create PRD/Spec/Spike/ADR only when they add decision value. Plans and tasks should reflect the approved outcome, not merely restate the request.

Use the templates in `docs/engineering/templates/` and the detailed rules in `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md`.

## UI/UX Execution Gate

Do not wait for the user to request UI-UX Pro Max explicitly.

For any feature that changes what users see or do in the browser:

1. Inspect the related existing UI.
2. Load the repository UI/UX skill and UI-UX Pro Max when available.
3. Establish the intended user flow before implementation.
4. Reuse existing components/patterns where possible.
5. Implement all relevant states and responsive behavior.
6. Verify with browser/e2e tooling.
7. Run the UI/UX review again after implementation and fix findings.

A UI/UX review is not a cosmetic approval step. It must evaluate hierarchy, discoverability, interaction clarity, consistency, accessibility, responsiveness, states, copy and visual quality.

## Product Judgment Gate

When the user proposes a mechanism, preserve the outcome rather than blindly preserving the mechanism.

Example: "Add a header so conversation persists" requires investigation of conversation ownership, session lifecycle, cookies, API-client independence and existing conversation architecture before adding the header.

If a requested mechanism is materially wrong, say so internally in the implementation plan and use the better design.

## Definition of Done

A change is not complete merely because:
- the code compiles;
- the endpoint exists;
- the database migration succeeds;
- a component renders.

It is complete when:
- the real scenario works end-to-end;
- production wiring exists;
- relevant failure/empty/loading/security states are handled;
- tests cover the meaningful behavior;
- browser-visible work has been visually and behaviorally verified;
- documentation reflects actual shipped behavior;
- no new dead configuration, dead endpoints, orphan readers/writers or duplicated patterns were introduced.

## Current Authentication Reality

The target standard for new API authentication is bearer-style authentication. However, this repository currently contains established admin cookie sessions, HMAC chat tokens and visitor/session cookies.

Do not pretend the repository is already bearer-only. Do not remove legacy/current mechanisms merely to satisfy the documentation. New authentication designs must follow the target standard unless an architecture decision explicitly approves an exception.

## Project-Specific Knowledge

Keep the existing detailed project facts in this file when maintaining the repository: tiered retrieval pipeline, module registry, white-label system, theme inheritance, database details, commands, thresholds, key files and current product instance information.

Do not replace those sections with generic engineering prose. This file should remain the bridge between the generic engineering constitution and the actual codebase.

## Documentation Map

- `AGENTS.md` — binding repository-wide engineering/product rules
- `docs/engineering/ENGINEERING_CONSTITUTION.md` — engineering judgment and decision principles
- `docs/engineering/ARCHITECTURE.md` — architecture documentation contract
- `docs/engineering/API_STANDARDS.md` — API conventions and compatibility
- `docs/engineering/SECURITY.md` — security/authentication standard and current-state exceptions
- `docs/engineering/DATABASE.md` — database/migration rules
- `docs/engineering/TESTING.md` — test strategy and evidence standards
- `docs/engineering/UI_UX.md` — UI/UX workflow and review gate
- `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md` — adaptive feature lifecycle and artifact rules
- `docs/engineering/templates/` — PRD, Spike, Spec, ADR, Plan and Tasks templates
- `docs/engineering/DOC_MIGRATION.md` — migration from the two legacy engineering documents
