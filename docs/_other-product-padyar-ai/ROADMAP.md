# Padyar AI — Feature Roadmap

**Last updated:** 2026-04-28

---

## What's Built

### 12 AI Apps (all functional with real API integrations)

| App | What it does | AI Provider |
|-----|-------------|-------------|
| Chat | Multi-model chat agent with web search, document creation, tools | OpenAI, Anthropic, Groq, xAI, DeepSeek, Google |
| Studio | AI image generation and editing | GPT-Image-1 |
| Image AI | Multi-model image generator | Replicate (SDXL, Flux, Recraft) |
| DALL-E | AI logo generator | DALL-E 3 |
| Vision | Image analysis and description | GPT-4o Vision |
| GPT | Marketing plan generator | GPT-4o-mini |
| Claude | Business plan generator | Claude 3.5 Sonnet |
| Grok | Product Hunt launch simulator | xAI Grok |
| LLaMA | Personal branding generator | Groq LLaMA 3.3 |
| PDF | Chat with PDF documents (vector search) | GPT-4o + pgvector |
| Audio | Voice-to-notes (transcribe + summarize) | Whisper + GPT-4o-mini |
| Voice | Text-to-speech | ElevenLabs |

### Infrastructure

| Component | Status | Details |
|-----------|--------|---------|
| Monorepo | Done | pnpm workspaces, 5 packages |
| CI/CD | Done | GitHub Actions: lint + typecheck + build |
| CLAUDE.md + AGENTS.md | Done | Decision engine, coding rules, patterns |
| MCP Config | Done | filesystem, github, tinyfish, n8n |
| Database | Done | 15 tables, 8 migrations, RLS on all |
| Auth | Done | Supabase Auth |
| Storage | Done | Cloudflare R2 |
| Credit system | Done | Free/premium model tiering |
| i18n | Done | Persian (fa) + English (en) |
| Redis | Wired but unused | Client exists, no service uses it yet |

---

## What's Left to Build

### Phase 1: Core Packages (turn stubs into real code)

#### F1. Memory System — `@padyar/memory`
**Status:** Mostly real. Needs integration testing and wiring into the web app.

| Task | What | Why |
|------|------|-----|
| F1.1 | Test semantic store end-to-end | `knowledge-base.ts` calls Supabase + OpenAI but hasn't been tested with real data |
| F1.2 | Wire memory into Chat app | Chat agent should be able to remember user preferences and past conversations |
| F1.3 | Session memory persistence | Currently in-memory only — survives within a session, not across restarts |
| F1.4 | Memory management UI | Users need to see/search/delete their stored memories |

**Files:** `packages/memory/src/semantic/knowledge-base.ts`, `packages/memory/src/persistent/session-store.ts`

---

#### F2. Agent Framework — `@padyar/agents`
**Status:** Orchestration is real. Individual agents return hardcoded outputs — no LLM calls.

| Task | What | Why |
|------|------|-----|
| F2.1 | Connect Gstack agents to real LLMs | Planner, architect, coder, reviewer, QA all return fake data. Need to call `customModel()` |
| F2.2 | Build the tools layer | `code-tools.ts`, `analysis-tools.ts`, `research-tools.ts` all return "delegated to runtime". Need real implementations |
| F2.3 | Wire model router to web app | `model-router.ts` duplicates what `lib/ai/ai-utils.ts` already does — should reuse it |
| F2.4 | Agent playground UI | Developers need a way to test agents without building a full feature |

**Key dependency:** F2.1 depends on `customModel()` from the web app being accessible via workspace reference.

**Files:** `packages/agents/src/gstack/*.ts`, `packages/agents/src/tools/*.ts`, `packages/agents/src/providers/model-router.ts`

---

#### F3. Pipeline Infrastructure — `@padyar/pipelines`
**Status:** CFT pipeline is mostly real (crawl/normalize/score/report work). Research and discovery are stubs. n8n has an HTTP client but incomplete webhook creation.

| Task | What | Why |
|------|------|-----|
| F3.1 | Add LLM analysis to CFT | `analyze.ts` uses regex heuristics — needs real AI analysis for entity extraction and relevance scoring |
| F3.2 | Build research pipeline | `execute.ts` returns placeholder text. Needs real web search (Serper API already in chat tools) |
| F3.3 | Build discovery pipeline | `search.ts` always returns empty. Needs GitHub/npm search integration |
| F3.4 | Complete n8n webhook creation | `createWebhookWorkflow` returns `workflowId: "pending"` — needs real n8n API calls |

**Existing patterns to reuse:** Serper API + Jina AI from `apps/web/app/(apps)/chat/tools/browseInternet.ts`

**Files:** `packages/pipelines/src/cft/stages/analyze.ts`, `packages/pipelines/src/research/`, `packages/pipelines/src/discovery/`, `packages/pipelines/src/integrations/n8n.ts`

---

#### F4. Design System — `@padyar/design-system`
**Status:** Config only. Has theme definitions and pattern descriptors but no React components.

| Task | What | Why |
|------|------|-----|
| F4.1 | Create component re-exports | Re-export shadcn/ui, Magic UI, and AI elements from `apps/web/components/` |
| F4.2 | Build theme registry | 3 themes defined (Padyar, Whisper, Branding) — need to wire to DaisyUI's 43 themes |
| F4.3 | Build pattern presets | 6 UI patterns defined (hero, dashboard, chat, form, pricing, feature-grid) — need real component implementations |

**Constraint:** Can't break Next.js Server Components. Re-exports must be careful with "use client" boundaries.

**Files:** `packages/design-system/src/themes/`, `packages/design-system/src/patterns/`

---

### Phase 2: Smart Features

#### F5. Decision Engine
**Status:** Stub. `routeIntent()` in agents package does keyword matching. CLAUDE.md has the rules but they aren't programmatically connected.

| Task | What | Why |
|------|------|-----|
| F5.1 | Upgrade router to LLM-based classification | Keyword matching is fragile. Use a lightweight LLM call to classify intent |
| F5.2 | Connect routing to real agents/pipelines | When intent is "engineering", actually spin up gstack agents |
| F5.3 | Build routing API endpoint | Expose intent classification as an API route the web app can call |

**Files:** `packages/agents/src/core/router.ts`

---

#### F6. Self-Improving Layer
**Status:** Algorithm exists in `workflow-detector.ts` but isn't wired to anything.

| Task | What | Why |
|------|------|-----|
| F6.1 | Record real user action sequences | Currently has no input source — needs to receive events from the web app |
| F6.2 | Persist detected patterns to memory | When a workflow is detected, save it via `@padyar/memory` |
| F6.3 | Suggest automations | When a pattern is detected 3+ times, suggest an n8n workflow or pipeline alias |

**Files:** `packages/agents/src/core/workflow-detector.ts`

---

### Phase 3: Infrastructure

#### F7. Testing
**Status:** Spec'd. No test framework configured yet. Full spec at `docs/features/testing/`.

| Task | What | Why |
|------|------|-----|
| F7.1 | Install Vitest, configure for monorepo | Unit test foundation for all packages |
| F7.2 | Write unit tests — priority 1 (zero mocks) | SessionMemoryStore, ConsensusBuilder, ScoreStage, NormalizeStage, ReportStage, routeIntent, canUseConfiguration, WorkflowDetector |
| F7.3 | Create shared mock factories | Supabase, OpenAI, fetch, AI SDK providers |
| F7.4 | Write unit tests — priority 2 (simple mocks) | Pipeline runner, CrawlStage, getProviderFromModelId, validateUserCredits |
| F7.5 | Write unit tests — priority 3 (service mocks) | SemanticMemoryStore, EmbeddingService |
| F7.6 | Install Storybook with Next.js framework | Component isolation for visual testing |
| F7.7 | Write stories for priority components | Chat, input forms, sidebar, generation cards, shadcn primitives |
| F7.8 | Set up Chromatic for visual diff in CI | Catch pixel changes on every PR |
| F7.9 | Install Playwright for screenshot tests | Full-page visual regression |
| F7.10 | Write page screenshot tests | All routes at all breakpoints and locales |
| F7.11 | Add a11y testing | addon-a11y + axe-playwright |
| F7.12 | Update CI with test + visual-test jobs | 3 CI jobs: lint+typecheck+unit, build, visual |

**Spec:** `docs/features/testing/SPEC.md` | **Research:** `docs/features/testing/RESEARCH.md`

---

#### F8. Redis Integration
**Status:** Client wired in `lib/redis.ts` but no service uses it.

| Task | What | Why |
|------|------|-----|
| F8.1 | Wire Redis into cached queries | `lib/db/cached-queries/` exists but doesn't use Redis |
| F8.2 | Add rate limiting | API routes need rate limiting per user |
| F8.3 | Cache AI model responses | Repeated identical prompts should hit cache |

**Files:** `apps/web/lib/redis.ts`, `apps/web/lib/db/cached-queries/`

---

## Priority Order

Build in this order (each depends on the previous):

```
F7.1 Vitest setup            ← do this FIRST — every feature needs tests
F2.1 Agents → LLMs          ← unblocks everything else
F1.1 Memory → test          ← agents need memory
F3.1 CFT → LLM analysis     ← uses agents for AI calls
F5.1 Decision engine         ← connects users to agents/pipelines
F6.1 Self-improving layer    ← needs agents + memory working
F7.6 Storybook + visual      ← after real UI components exist
F4.1 Design system           ← lowest priority, web app works fine without it
F8.1 Redis                   ← optimization, not critical path
```

## R&D Needed

Before implementing each feature, follow the docs workflow:

```
docs/features/{slug}/RESEARCH.md  →  SPEC.md  →  BREAKDOWN.md  →  Linear issues
```

Features that need R&D first:

| Feature | Open Questions |
|---------|---------------|
| F2.1 Agent LLMs | How to share `customModel()` across packages without circular deps? |
| F3.2 Research pipeline | Which search API? Serper (already used) vs Tavily vs Perplexity? |
| F3.4 n8n integration | Is n8n self-hosted or cloud? What permissions does the API key have? |
| F4.1 Design system | How to re-export without breaking Server Components? |
| F5.1 Decision engine | Which model for intent classification? Cost per call? |
| F1.4 Memory UI | Where in the app? Settings page? Sidebar? Standalone page? |
