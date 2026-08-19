# Git Workflow

## Branch Naming

```
feat/{description}      → New features
fix/{description}       → Bug fixes
refactor/{description}  → Code refactoring
docs/{description}      → Documentation
```

## Branch Strategy

- `main` — production-ready code, protected
- Feature branches merge into `main` via squash merge
- One branch per spec/feature

## Commit Messages

```
type(scope): short description

plan: plans/{slug}/issues/{NN}
```

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`

## Pull Requests

- Link to spec in description
- Keep PRs small (<500 LOC)
- One PR per plan issue
- Verify: `pnpm lint` and `pnpm build` pass
