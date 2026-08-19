# Plan: {Feature Slug}

| Field | Value |
|-------|-------|
| Source | `features/{slug}/SPEC.md` |
| Type | Feature \| Refactor |
| Created | {YYYY-MM-DD} |
| Status | Planning |

## Summary

_One paragraph describing the overall implementation approach._

## Non-Goals

- {Explicitly out of scope for this plan}

## Execution Order

| Order | Issue | Depends On | LOC Est. | Status |
|-------|-------|------------|----------|--------|
| 01 | {issue-title} | — | ~{N} | Pending |
| 02 | {issue-title} | 01 | ~{N} | Pending |
| 03 | {issue-title} | 01 | ~{N} | Pending |

## Success Criteria Mapping

| Spec Criterion | Issue(s) |
|----------------|----------|
| SC-001 | 01, 02 |
| SC-002 | 03 |

## Feature Flags

| Flag | Purpose | Default | Enable Issue |
|------|---------|---------|--------------|
| `FEATURE_{NAME}` | {Description} | `false` | {NN} |

## Rollback Strategy

_If something goes wrong, how do we safely revert?_
