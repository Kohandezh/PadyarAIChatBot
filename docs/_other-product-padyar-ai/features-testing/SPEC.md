# SPEC: Testing Infrastructure

**Feature:** F7 — Testing
**Status:** Approved
**Date:** 2026-04-29
**Source:** `docs/features/testing/RESEARCH.md`

---

## Problem

The project has zero tests. Every change is a risk — no way to verify behavior, catch regressions, or ensure visual consistency across 13 themes, RTL layouts, and 12 AI apps. Features ship without verification.

## Goal

Four automated test layers that run on every commit and PR, catching logic bugs, visual regressions, and accessibility issues before they reach production.

## Non-Goals

- Manual QA process
- Load/performance testing
- Security scanning (separate feature)
- E2E functional tests (Playwright used only for screenshots, not interaction flows)

---

## Test Layers

### Layer 1: Unit Tests — Vitest

**Scope:** 48 test cases across 14 modules in all 4 packages + web app
**Runs:** Every commit (local + CI lint-and-typecheck job)
**Speed:** ~5 seconds

| Package | Modules | Cases |
|---------|---------|-------|
| @padyar/memory | SessionMemoryStore, SemanticMemoryStore, EmbeddingService | 14 |
| @padyar/agents | routeIntent, classifyTask, ConsensusBuilder, WorkflowDetector | 12 |
| @padyar/pipelines | Pipeline runner, CrawlStage, NormalizeStage, ScoreStage, ReportStage | 14 |
| @padyar/web | customModel, canUseConfiguration, validateUserCredits | 8 |

**Style:** Co-located (`foo.ts` → `foo.test.ts`), table-driven for parameterized logic.

**Mocking:** `vi.mock()` for Supabase, OpenAI, fetch, AI SDK providers. Shared mock factories in `tests/mocks/`.

### Layer 2: Visual Component Tests — Storybook + Chromatic

**Scope:** Every reusable UI component in all themes, directions, breakpoints, and states
**Runs:** Every PR (CI only)
**Speed:** ~2 minutes

**Stories per component:**

| Story | Variants |
|-------|----------|
| Default | 1 |
| All Themes | 13 DaisyUI themes |
| RTL | Persian (fa) |
| Responsive | 375px, 768px, 1280px |
| States | loading, error, empty, disabled |

**Priority components:**

1. Chat interface — messages, input, toolbar, canvas
2. Input forms — used by 6 AI apps
3. Dashboard sidebar — nav, theme switcher
4. Generation cards — image/text results
5. shadcn/ui primitives — Button, Input, Select, Dialog

**Chromatic:** Publishes visual diffs on every PR. Reviewer approves or rejects pixel changes.

### Layer 3: Full-Page Visual Regression — Playwright

**Scope:** Screenshot comparison of real pages at all breakpoints and locales
**Runs:** Every PR (CI only)
**Speed:** ~3 minutes

**Pages:**

| Page | Breakpoints | Locales |
|------|------------|---------|
| Dashboard | 375px, 768px, 1280px | fa, en |
| Chat | 375px, 768px, 1280px | fa, en |
| All 12 AI apps | 1280px | fa |
| Blog listing + post | 1280px | fa, en |
| Auth pages | 375px, 1280px | fa, en |

**Baselines:** Stored in `tests/visual/__screenshots__/`. Updated when intentional design changes happen.

### Layer 4: Accessibility Tests

**Scope:** WCAG 2.1 AA compliance on all components and pages
**Runs:** Bundled with Storybook and Playwright (no separate step)

| Tool | Where | Checks |
|------|-------|--------|
| `@storybook/addon-a11y` | Every story | Contrast, aria labels, keyboard nav |
| `axe-playwright` | Every page screenshot | Full-page a11y audit |

---

## Acceptance Criteria

- AC-1: `pnpm test` runs all unit tests and passes with 0 failures
- AC-2: Every package has `>= 1` test file
- AC-3: `pnpm test:visual` runs Playwright screenshots and passes
- AC-4: Storybook builds without errors at `pnpm storybook`
- AC-5: Chromatic runs on PRs and posts visual diff
- AC-6: CI has 3 jobs: lint-and-typecheck (with unit tests), build, visual-tests
- AC-7: All 4 priority component groups have stories
- AC-8: a11y addon shows 0 violations on default theme

## Success Metrics

| Metric | Target |
|--------|--------|
| Unit test coverage | >= 70% on priority 1 modules |
| Visual regression catch rate | >= 95% of pixel changes flagged |
| Test run time (unit) | <= 10 seconds |
| Test run time (visual CI) | <= 5 minutes |
| False positive rate | <= 5% |

---

## Config Files

| File | Purpose |
|------|---------|
| `vitest.config.ts` (root) | Vitest config with vite-tsconfig-paths |
| `playwright.config.ts` (root) | Playwright browsers, viewports, screenshot settings |
| `apps/web/.storybook/main.ts` | Storybook with Next.js framework, a11y addon, viewport addon |
| `apps/web/.storybook/preview.ts` | Theme decorator, RTL decorator |
| `tests/mocks/` | Shared mock factories for Supabase, OpenAI, fetch |

## Package Scripts

| Script | What it runs |
|--------|-------------|
| `pnpm test` | Vitest unit tests (all packages) |
| `pnpm test:visual` | Playwright screenshot tests |
| `pnpm test:watch` | Vitest in watch mode |
| `pnpm storybook` | Start Storybook dev server |
| `pnpm build-storybook` | Build static Storybook |
| `pnpm chromatic` | Publish to Chromatic for visual diff |
