# Setup Guide

## Prerequisites

- Node.js 18+
- pnpm (`npm install -g pnpm`)
- Git
- Supabase account
- OpenAI API key
- Docker (for Redis)

## Quick Start

```bash
# 1. Install dependencies
pnpm install

# 2. Configure environment
cp apps/web/.env.example apps/web/.env
# Edit apps/web/.env with your keys

# 3. Start Redis
cd apps/web && docker-compose up -d redis && cd ../..

# 4. Set up database
cd apps/web
npx supabase login
npx supabase init
npx supabase link --project-ref <your-project-id>
npx supabase db push
cd ../..

# 5. Start development
pnpm dev
```

App runs at `http://127.0.0.1:3000`.

## Day-to-Day Commands

| Command | What it does |
|---------|-------------|
| `pnpm dev` | Start dev server |
| `pnpm build` | Production build |
| `pnpm lint` | Run ESLint |
| `pnpm clean` | Clean all caches |

## Package Commands

| Command | What it does |
|---------|-------------|
| `pnpm --filter @padyar/web dev` | Run only the web app |
| `pnpm --filter @padyar/agents typecheck` | Type-check agents package |
| `pnpm -r typecheck` | Type-check all packages |
