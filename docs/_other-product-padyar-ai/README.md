# ⚠️ These documents describe a DIFFERENT product

Nothing in this folder describes the chatbot in this repository.

These files were inherited from **“Padyar AI”** — a pnpm/Turborepo monorepo of
around twelve AI apps built on Next.js, Supabase, Vercel and Stripe, tested with
Vitest, Storybook and Chromatic.

**This repository is a different thing entirely:** a single-customer
FastAPI + SQLite chatbot, installed once per customer, tested with pytest. It has
no monorepo, no Node packages, no Supabase, and no Stripe.

## Why they were moved here instead of deleted

Following `docs/onboarding/setup.md` used to tell a new developer to run
`pnpm install` and provision Supabase — instructions that cannot work here. For
an external reviewer (the دانش‌بنیان evaluation, an auditor, a new hire) that is
worse than missing documentation: it is confidently wrong documentation.

They are kept because they may still be the real documentation for that other
product. Nothing was lost — `git mv` preserved the full history of every file.

## What replaced them

| Was | Now |
|---|---|
| `docs/ROADMAP.md` | `docs/knowledge-based-evidence/15-limitations-and-roadmap-fa.md` |
| `docs/onboarding/setup.md` | the Setup section of `CLAUDE.md`, and `README.md` |
| `docs/onboarding/how-to-ship.md` | “Mandatory Checks Before Every Commit” in `CLAUDE.md` |
| `docs/services/README.md` | the Tech Stack and module tables in `CLAUDE.md` |
| `docs/workflow/git.md`, `workflow/plan.md` | no equivalent — this project has no Linear board or monorepo branch policy |
| `docs/features/testing/*` | the Testing section of `CLAUDE.md`; the suite itself is `tests/` |
| `docs/legal/README.md` | no equivalent yet |

## If you are working on THIS product

Start with `README.md` and `CLAUDE.md` in the repository root, then
`docs/engineering/` (architecture, decisions, security model, deployment
runbook) and `docs/knowledge-based-evidence/` (the technical evidence package).

## Safe to delete?

Yes — if the other product's documentation lives somewhere else, this whole
folder can be removed. That is a call for the repository owner, not something
to do automatically.
