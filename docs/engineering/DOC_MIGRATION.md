# Documentation Migration Plan

## Decision

The existing engineering standards are being folded into the new engineering structure rather than maintained as competing sources of truth.

### New structure

- `AGENTS.md` — binding repository-wide rules
- `CLAUDE.md` — Claude execution and routing
- `docs/engineering/ENGINEERING_CONSTITUTION.md`
- `docs/engineering/ARCHITECTURE.md`
- `docs/engineering/API_STANDARDS.md`
- `docs/engineering/SECURITY.md`
- `docs/engineering/DATABASE.md`
- `docs/engineering/TESTING.md`
- `docs/engineering/UI_UX.md`

## Legacy files

The old `docs/ENGINEERING_STANDARDS.md` and `docs/CODINGW_WORKFLOW_STANDARD.md` should be removed only after their repository-specific content has been reviewed and transferred into the new structure.

Do not delete them merely because a generic replacement exists.

## PR #129

PR #129 should be updated to implement the new structure rather than merged as a second, competing documentation system.

## Important Preservation Rule

Project-specific facts are not to be replaced by generic engineering language. If a legacy document contains a real repository decision, workflow, command, constraint or known exception, transfer that information to the appropriate new document.

## Authentication Note

The bearer-only rule is a target for new API work. Current admin cookie sessions, HMAC chat tokens and visitor cookies remain documented as current-state mechanisms until a deliberate migration is undertaken.
