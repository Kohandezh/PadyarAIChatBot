# Project Management

## Tooling

- **Linear** — Task tracking, sprint management, status
- **docs/** — Research, specs, plans, context (the "why" and "how")
- **GitHub** — Code, PRs, CI/CD

Both docs/ and Linear stay in sync. Docs hold the deep context. Linear holds the tracking.

## Feature Pipeline

Every feature follows this pipeline:

```
Pick → R&D → Document → Spec → Plan → Linear Issues → Implement → Archive
```

| Step | Where | What Happens | Output |
|------|-------|-------------|--------|
| Pick | `docs/ROADMAP.md` | Choose next feature from priority order | Feature slug |
| R&D | Web search + codebase analysis | Deep research: best practices, libraries, patterns, alternatives, trade-offs, risks | Knowledge |
| Document | `docs/features/{slug}/RESEARCH.md` | Write down R&D findings, risks, recommended approach | RESEARCH.md |
| Spec | `docs/features/{slug}/SPEC.md` | Define what, why, acceptance criteria, success metrics | SPEC.md |
| Plan | `docs/features/{slug}/BREAKDOWN.md` | Execution order, dependencies, LOC estimates | BREAKDOWN.md |
| Issues | Linear | Create trackable tickets from BREAKDOWN.md | Linear issues |
| Implement | Code + tests + GitHub PRs | Write code + tests, run mandatory checks, open PR | Passing CI |

## Sprint Model

- **Cadence:** Weekly sprints, starting Monday
- **Planning:** Monday — pick issues from backlog, assign to sprint
- **Review:** Friday — review completed, move unfinished to next sprint
- **Sprint scope:** 3-5 issues per sprint (realistic for a small team)

## Linear Setup

### Workspace Structure

| Team | Purpose |
|------|---------|
| Engineering | All development work |

### Projects

| Project | Scope |
|---------|-------|
| Agents | @padyar/agents — gstack, ruflo, tools |
| Pipelines | @padyar/pipelines — CFT, research, discovery |
| Memory | @padyar/memory — semantic store, session store |
| Design System | @padyar/design-system — components, themes |
| Web App | apps/web — pages, API routes, features |
| Infrastructure | CI/CD, testing, DevOps |

### Labels

| Label | When to use |
|-------|-------------|
| research | R&D phase |
| spec | Writing specification |
| implementation | Coding |
| bug | Bug fix |
| chore | Maintenance, cleanup |
| docs | Documentation only |

### Workflow States

```
Backlog → Todo → In Progress → In Review → Done
```

### Issue Rules

- <500 LOC per issue
- <5 acceptance criteria per issue
- Independently mergeable
- Single responsibility
- No mixing schema + logic
- **Title format:** `F{N}.{M}: {short description}` (matches ROADMAP.md IDs)
- **Description must include:** Link to `docs/features/{slug}/` folder

## Sync Rules

| When | Action |
|------|--------|
| Plan approved | Create Linear issues from BREAKDOWN.md |
| Issue started | Move to "In Progress", create branch `feat/{slug}` |
| PR opened | Move to "In Review", link PR in Linear |
| PR merged | Move to "Done", update docs/features/INDEX.md |
| Sprint ends | Archive completed, carry over unfinished |

## Feature Folder

Each feature lives in `docs/features/{slug}/`:

```
features/{slug}/
  RESEARCH.md      → Research findings
  SPEC.md          → What to build and why
  BREAKDOWN.md     → How to implement
```

Issues live in Linear, not in the docs folder. The docs folder holds context only.
