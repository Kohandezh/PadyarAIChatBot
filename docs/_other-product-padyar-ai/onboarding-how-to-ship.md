# How to Ship a Feature

## Workflow

```
1. Research      → docs/features/{slug}/RESEARCH.md
2. Write a spec  → docs/features/{slug}/SPEC.md
3. Create a plan → docs/features/{slug}/BREAKDOWN.md
4. Break into issues → docs/features/{slug}/issues/{NN}-{slug}.md
5. Implement     → Code, test, review
6. Merge         → Squash merge to main
7. Archive       → Move feature folder to docs/features/archive/
```

## Monorepo Structure

```
apps/web/          → The Next.js app (pages, API routes, components)
packages/agents/   → Agent framework (gstack, ruflo)
packages/pipelines/ → Pipeline infrastructure (CFT, research)
packages/memory/   → Memory systems (session, semantic)
packages/design-system/ → UI library (themes, patterns)
```

## Adding a New AI App

1. Create page: `apps/web/app/(apps)/{app-name}/page.tsx`
2. Create API route: `apps/web/app/api/(apps)/{app-name}/route.ts`
3. Add tool config: `apps/web/app/(apps)/{app-name}/toolConfig.ts`
4. Register in: `apps/web/lib/ai/apps.ts`

## Adding a New Agent

1. Create agent file: `packages/agents/src/gstack/{name}.ts`
2. Extend `BaseAgent` and implement `execute(task)`
3. Export from `packages/agents/src/index.ts`
4. Wire into orchestrator if needed

## Adding a New Pipeline

1. Create stages in: `packages/pipelines/src/{name}/stages/`
2. Create index with pipeline factory: `packages/pipelines/src/{name}/index.ts`
3. Each stage implements `PipelineStage<I, O>`
4. Export from `packages/pipelines/src/index.ts`

## Code Quality

- TypeScript strict mode — no `any` without justification
- Follow existing patterns — don't invent new ones
- Keep it simple — if it needs a comment, simplify it
- Every feature passes the grandmother test — can anyone use it?
