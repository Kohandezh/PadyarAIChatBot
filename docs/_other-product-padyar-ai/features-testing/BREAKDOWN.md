# BREAKDOWN: Testing Infrastructure

**Source:** `docs/features/testing/SPEC.md`
**Type:** Feature
**Created:** 2026-04-29
**Status:** Ready

---

## Summary

Set up 4-layer automated testing: Vitest unit tests, Storybook component tests with Chromatic, Playwright screenshot tests, and accessibility checks. Start with unit tests (highest value, lowest effort), then visual layers.

## Non-Goals

- Performance/load testing
- E2E interaction tests (click flows)
- Security scanning

---

## Execution Order

| Order | Issue | Depends On | LOC Est. | Status |
|-------|-------|------------|----------|--------|
| 01 | Install Vitest and configure for monorepo | — | ~80 | Pending |
| 02 | Write unit tests for priority 1 modules (zero mocks) | 01 | ~400 | Pending |
| 03 | Create shared mock factories (Supabase, OpenAI, fetch) | 01 | ~150 | Pending |
| 04 | Write unit tests for priority 2 modules (simple mocks) | 03 | ~250 | Pending |
| 05 | Write unit tests for priority 3 modules (service mocks) | 03 | ~150 | Pending |
| 06 | Install and configure Storybook with Next.js | 01 | ~120 | Pending |
| 07 | Write stories for priority components (chat, input, sidebar) | 06 | ~300 | Pending |
| 08 | Set up Chromatic for visual diff in CI | 06, 07 | ~30 | Pending |
| 09 | Install and configure Playwright for screenshot tests | 01 | ~100 | Pending |
| 10 | Write page screenshot tests for all routes | 09 | ~200 | Pending |
| 11 | Add a11y testing (addon-a11y + axe-playwright) | 06, 09 | ~80 | Pending |
| 12 | Update CI workflow with test + visual-test jobs | 01, 08, 09 | ~60 | Pending |

---

## Success Criteria Mapping

| Spec Criterion | Issue(s) |
|----------------|----------|
| AC-1: pnpm test passes | 01, 02, 04, 05 |
| AC-2: Every package has >= 1 test | 02, 04, 05 |
| AC-3: pnpm test:visual passes | 09, 10 |
| AC-4: Storybook builds | 06, 07 |
| AC-5: Chromatic runs on PRs | 08, 12 |
| AC-6: CI has 3 jobs | 12 |
| AC-7: Priority components have stories | 07 |
| AC-8: 0 a11y violations | 11 |

---

## Rollback Strategy

Each issue is independently mergeable. If Storybook or Playwright setup causes issues, revert that specific issue — unit tests remain independent. No database changes involved.
