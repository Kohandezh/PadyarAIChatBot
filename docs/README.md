# Padyar AI Documentation

Documentation hub for the Padyar AI platform.

## Structure

| Directory | Purpose |
|-----------|---------|
| `features/` | Features — research, specs, plans, and issues. One folder per feature |
| `features/templates/` | Templates for research, specs, breakdowns, and issues |
| `features/INDEX.md` | Central feature tracking table |
| `workflow/` | Development workflow (git, project management) |
| `services/` | Third-party service documentation |
| `onboarding/` | Developer onboarding and setup |
| `legal/` | Legal, compliance, privacy |

## Workflow

```
Idea → Research → Spec → Plan → Issues → Implement → Archive
```

1. **Research** the feature (web, docs, code analysis) — write `features/{slug}/RESEARCH.md`
2. **Spec** what to build and why — write `features/{slug}/SPEC.md`
3. **Plan** how to build it — write `features/{slug}/BREAKDOWN.md` + `issues/`
4. **Implement** each issue (<500 LOC, independently mergeable)
5. **Archive** the feature folder when done

## Feature Folder Structure

```
features/{slug}/
  RESEARCH.md      → What we learned before building
  SPEC.md          → What to build and why
  BREAKDOWN.md     → How to implement (plan)
  issues/
    01-{slug}.md   → Implementation issues
    02-{slug}.md
```

## Conventions

- Features are tracked in `features/INDEX.md` with spec and plan status
- Each issue targets <500 LOC and is independently mergeable
- Feature flags hide incomplete work until the final enable issue
- Research is mandatory before any spec is written
