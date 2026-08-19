# Features — Specifications, Plans & Research

Each feature lives in its own folder. Everything about a feature — research, spec, plan, and issues — is in one place.

## Structure

```
features/
  INDEX.md                  # Central tracking table
  templates/                # Templates for all document types
    research.md             # Pre-implementation research
    spec-feature.md         # Full feature spec (200+ LOC)
    spec-quick.md           # Quick feature spec (<100 LOC)
    spec-refactor.md        # Refactor spec
    breakdown.md            # Plan overview and execution order
    issue.md                # Individual implementation issue
  backlog/                  # Features planned but not yet ready
  archive/                  # Completed features (by quarter)
  {slug}/                   # One folder per active feature
    RESEARCH.md             # Research findings (created first)
    SPEC.md                 # What to build and why
    BREAKDOWN.md            # How to implement (plan)
    issues/                 # Implementation issues
      01-{slug}.md
      02-{slug}.md
```

## Workflow

```
Idea → Research → Spec → Plan → Issues → Implement → Archive
```

### Step 1: Research (mandatory)

Before writing any spec, research the feature:

1. Copy `templates/research.md` to `features/{slug}/RESEARCH.md`
2. Research the topic (web, docs, existing code, competitors)
3. Document findings, risks, and recommended approach

### Step 2: Spec

Define what to build and why:

- **Feature** (200+ LOC): Use `templates/spec-feature.md`
- **Quick Feature** (<100 LOC): Use `templates/spec-quick.md`
- **Refactor**: Use `templates/spec-refactor.md`

### Step 3: Plan

Define how to build it:

1. Copy `templates/breakdown.md` to `features/{slug}/BREAKDOWN.md`
2. Break the spec into small issues using `templates/issue.md`

### Step 4: Implement

- Each issue targets **<500 LOC** and is **independently mergeable**
- Update issue status as you work
- When all issues are done, move the feature folder to `archive/`

## Spec Status

| Status | Meaning |
|--------|---------|
| Draft | Being written |
| Review | Ready for feedback |
| Approved | Ready to plan |
| In Progress | Being implemented |
| Done | Completed |

## Plan Status

| Status | Meaning |
|--------|---------|
| Planning | Plan being written |
| Ready | Ready for implementation |
| In Progress | Issues being worked on |
| Done | All issues completed |

## Issue Rules

- <500 LOC per issue
- <5 acceptance criteria per issue
- Independently mergeable
- Single responsibility
- No mixing schema + logic
- No mixing refactoring + feature work

## Merge Safety

Every issue must declare one of three strategies:

| Strategy | When to use |
|----------|-------------|
| Feature flag | New feature, hidden until final enable issue |
| Direct (low risk) | Bug fix, small refactor, safe to merge |
| Direct (guarded) | Safe with null checks or conditionals |

## Naming Convention

- Feature folder: `{short-descriptive-slug}` (e.g., `agent-playground`, `memory-dashboard`)
- Issue files: `{NN}-{short-slug}.md` (e.g., `01-types-and-constants.md`)
