# RESEARCH: Testing Strategy

**Feature:** F7 — Testing Infrastructure
**Status:** Done
**Date:** 2026-04-29

---

## Current State

The project has **zero tests** — no test runner, no test files, no test dependencies, no test scripts.

| Item | Status |
|------|--------|
| Test runner | None |
| Test files | 0 |
| Test dependencies | 0 |
| Test scripts in package.json | 0 |
| Test task in turbo.json | 0 |
| Test step in CI | 0 |

---

## What Needs Testing

### Unit Tests — Pure Logic (48 test cases, 14 modules)

**Priority 1 — Zero mocks needed:**

| Module | Package | Functions | Cases |
|--------|---------|-----------|-------|
| SessionMemoryStore | memory | `store`, `retrieve`, `delete`, `update` | CRUD, tag filtering, date range, text search, limit, update throws on missing |
| ConsensusBuilder | agents | `build` | Empty input, all agree, partial fail, divergent, threshold at 0.6 |
| ScoreStage | pipelines | `execute`, `calculateQuality` | 60/40 weighting, quality sub-scores, sorting, top-10 cutoff |
| NormalizeStage | pipelines | `execute`, `stripHtml`, `extractTitle` | Strip tags, remove scripts/styles, decode entities, title fallback |
| ReportStage | pipelines | `execute`, `generateInsights`, `generateRecommendations` | Format findings, high score rec, empty findings |
| routeIntent | agents | `routeIntent`, `classifyTask` | 6 categories, keyword matching, confidence formula, fallback |
| canUseConfiguration | web | `canUseConfiguration` | Free model + no browse, premium = 1 credit, 0 credits blocked |
| WorkflowDetector | agents | `recordAction`, `detectPatterns`, `suggestOptimizations` | 3+ repeats, sliding window, trim at 1000 |

**Priority 2 — Simple mocks (mock objects or fetch):**

| Module | Package | Mock Needed | Cases |
|--------|---------|------------|-------|
| Pipeline runner | pipelines | Mock PipelineStage | Retry, rollback, skip after non-recoverable, partial success |
| CrawlStage | pipelines | `fetch` | Network errors, non-200, abort, empty URLs |
| getProviderFromModelId | web | AI SDK providers | gpt→openai, claude→anthropic, llama→groq, grok→xiai, fallback |
| validateUserCredits | web | Supabase query | Paywall off, no user, null credits, insufficient, sufficient |

**Priority 3 — Service mocks (Supabase, OpenAI):**

| Module | Package | Mock Needed | Cases |
|--------|---------|------------|-------|
| SemanticMemoryStore | memory | Supabase + EmbeddingService | Ingest, batch ingest, search, delete, update re-embeds |
| EmbeddingService | memory | OpenAI SDK | Single embed, batch embed, empty array |

### Visual Component Tests — Storybook + Chromatic

Every UI component rendered in isolation across all themes, directions, breakpoints, and states.

**Story types per component:**

| Story | What it captures |
|-------|-----------------|
| Default | Normal render |
| All Themes | 13 DaisyUI themes (padyar, light, dark, cupcake, dracula, etc.) |
| RTL | Persian right-to-left layout |
| Responsive | Mobile (375px), tablet (768px), desktop (1280px) |
| States | Loading, error, empty, disabled, hover, focus |

**Priority components to storybook:**

1. AI chat interface — messages, input, toolbar, canvas
2. Input form components — used by 6 AI apps (GPT, Claude, Grok, LLaMA, Vision, DALL-E)
3. Dashboard sidebar — navigation, theme switcher, user menu
4. Generation cards — image/text result cards across all apps
5. shadcn/ui primitives — Button, Input, Select, Dialog, Sheet, etc.

### Full-Page Visual Regression — Playwright

Screenshot comparison of real pages at all breakpoints.

| Page | Breakpoints | Locales |
|------|------------|---------|
| Dashboard (app list) | 375px, 768px, 1280px | fa, en |
| Chat app | 375px, 768px, 1280px | fa, en |
| All 12 AI app pages | 1280px | fa |
| Blog listing + post | 1280px | fa, en |
| Login / signup | 375px, 1280px | fa, en |

### Accessibility Tests

| Tool | Where | What |
|------|-------|------|
| `@storybook/addon-a11y` | Storybook stories | WCAG 2.1 AA, contrast, aria labels, keyboard nav |
| `axe-playwright` | Playwright pages | Full-page a11y audit |

---

## External Dependencies Needing Mocks

### Supabase
- `createClient` → mock `.from().insert().select()`, `.update().eq()`, `.delete().eq()`, `.rpc()`
- Used in: memory, creditValidation, auth middleware

### OpenAI
- `client.embeddings.create()` → mock embedding vectors
- Used in: memory/embedding.ts

### AI SDK Providers
- `@ai-sdk/openai`, `@ai-sdk/anthropic`, `@ai-sdk/groq`, `@ai-sdk/xai`, `@ai-sdk/deepseek`, `@ai-sdk/google`
- Mock model factory functions
- Used in: ai-utils.ts

### Global fetch
- Mock with `vi.fn()` or Playwright's route interception
- Used in: pipelines/crawl.ts

### Next.js modules
- `next/server` (NextResponse, NextRequest)
- `next/navigation` (useRouter, usePathname)
- `next/headers`
- Vitest handles these with module mocking

---

## Tool Decisions

### Unit Tests: Vitest
- Matches CLAUDE.md rule ("When adding tests, use Vitest")
- Native TypeScript support via Vite
- Built-in mocking with `vi.fn()`, `vi.mock()`
- Turborepo integration via `"test"` task
- Fast — runs in ~5 seconds for all unit tests

### Visual Component Tests: Storybook 8 + Chromatic
- Storybook isolates components for visual review
- Chromatic provides pixel-level diff in CI (every PR)
- Supports theme switching, viewport resizing, RTL
- Next.js framework integration via `@storybook/nextjs`
- `@storybook/addon-a11y` for accessibility

### Full-Page Screenshots: Playwright
- Real browser rendering (Chromium, Firefox, WebKit)
- `toHaveScreenshot()` for pixel comparison
- Supports multiple viewports, locales, auth states
- Baseline management in `tests/visual/__screenshots__/`
- Runs in CI via GitHub Actions

### Accessibility: addon-a11y + axe-playwright
- Storybook addon catches component-level a11y issues during development
- Playwright axe catches full-page a11y issues in CI

---

## Config Requirements

### Vitest
- Root `vitest.config.ts` with `vite-tsconfig-paths` plugin
- `vitest` as devDependency at root
- `"test"` script in root + each package
- `"test"` task in `turbo.json`
- Co-located test files (`foo.ts` → `foo.test.ts`)

### Storybook
- `apps/web/.storybook/main.ts` — Next.js framework, a11y addon, viewport addon
- `apps/web/.storybook/preview.ts` — global decorators for themes, RTL
- `"storybook"` and `"build-storybook"` scripts
- Chromatic CLI for CI

### Playwright
- Root `playwright.config.ts`
- `tests/visual/` directory with page-level test files
- `"test:visual"` script
- Baseline screenshots committed to repo

### CI
- `test` job in `.github/workflows/ci.yml` (runs Vitest)
- `visual-tests` job (runs Chromatic + Playwright screenshots)

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Flaky visual tests (font rendering differences between OS) | Use Chromatic's cloud rendering (consistent environment) |
| Playwright baselines need updating on intentional design changes | Commit baselines, review diffs in PR |
| Storybook setup complex with Next.js App Router | Use `@storybook/nextjs` framework preset |
| Mock setup boilerplate for Supabase | Create shared mock factories in `tests/mocks/` |
| Slow CI with 4 test layers | Run unit tests every commit, visual tests on PRs only |
