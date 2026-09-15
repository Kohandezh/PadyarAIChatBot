# PadyarAIChatbot Documentation Package

This package contains the proposed documentation structure discussed for the PadyarAIChatbot repository.

## Install

Copy these files into the repository, preserving the paths:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/engineering/*`

## Important

This package intentionally separates binding engineering rules from Claude-specific workflow and detailed engineering standards.

It also treats authentication as **target state + explicit current-state exceptions**, because the current repository already uses admin cookie sessions, HMAC chat tokens and visitor/session cookies.

The supplied conversation file contained the existing `AGENTS.md` project orientation. The package preserves that project's key architecture facts while reorganizing the new engineering rules. Because the complete current `CLAUDE.md`, `ENGINEERING_STANDARDS.md` and `CODINGW_WORKFLOW_STANDARD.md` source files were not available in the supplied file, this package does not pretend to have merged unseen text from those files. Review their repository versions before deleting the legacy files.

## Feature Development Artifacts

The package now includes an adaptive feature lifecycle and reusable templates:

- `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md`
- `docs/engineering/templates/PRD.md`
- `docs/engineering/templates/SPIKE.md`
- `docs/engineering/templates/SPEC.md`
- `docs/engineering/templates/ADR.md`
- `docs/engineering/templates/PLAN.md`
- `docs/engineering/templates/TASKS.md`

The agent should choose the minimum appropriate workflow rather than forcing every feature through every document.
