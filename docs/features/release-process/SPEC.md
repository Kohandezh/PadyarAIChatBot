# SPEC: Release process — VERSION, CHANGELOG, tagged releases

| Field | Value |
|-------|-------|
| Created | 2026-09-14 |
| Updated | 2026-09-14 |
| Status | Implemented |
| Domain | infrastructure |
| Author | تیم پادیار |
| Sources | The 2026 danesh-bonyan assessment finding («no tags/releases/versioned artifacts»); `docs/superpowers/plans/2026-09-14-assessment-remediation.md` Task 7 |

This document describes what IS shipped on branch `task/release-process`, not
what was planned.

## 1. Scenario

**Who triggers what, from where:**

- **Operator** — cuts a release by editing two files (`VERSION`,
  `CHANGELOG.md`), committing, tagging `vX.Y.Z` and pushing the tag. Five
  commands, no tooling to install (`docs/engineering/RELEASING.md`).
- **GitHub Actions** — on the pushed tag, runs the full test suite and
  publishes a GitHub Release whose notes are the matching CHANGELOG section.
- **Operator, later** — asks "which build is the server running?" and answers
  it with `curl https://<install>/api/health` → `{"status": "ok",
  "version": "0.1.0"}`, compared against the GitHub Releases page.

## 2. What ships

| Piece | File | Behavior |
|---|---|---|
| Version source | `VERSION` (repo root) | The version string, e.g. `0.1.0`. One source of truth. |
| Reader | `app/__init__.py` | `__version__` = file contents, stripped. Missing/unreadable file → `0.0.0`; never a crash at import time. |
| Public surface | `app/routers/public.py` | `GET /api/health` adds `"version": __version__`. Additive only: `status` unchanged, diagnostics stay behind admin auth at `/admin/api/ops/health`. |
| Changelog | `CHANGELOG.md` | Keep a Changelog format; empty `[Unreleased]` on top, `[0.1.0]` seeded with the current state (≤5 bullets). |
| Workflow | `.github/workflows/release.yml` | `on: push: tags: ['v*']` → checkout → install → `pytest -q` → extract the `[X.Y.Z]` section → GitHub Release. No deploy job. |
| Runbook | `docs/engineering/RELEASING.md` | Semver policy for this product, exact cut-a-release commands, what the workflow does, rollback note. |

## 3. Design decisions

- **A file, not git metadata.** The running server cannot read git tags (the
  deploy checkout does not guarantee them), but it can always read a file
  next to the package — and serve it on `/api/health`.
- **`version` on `/api/health` is public by design.** The August 2026 health
  pullback removed fields with reconnaissance value (modules, toggles,
  dataset size). A version string is already public on GitHub Releases and
  tells an attacker nothing about the attack surface; the leak test keeps
  every other diagnostic key out.
- **The release workflow re-runs the suite** rather than trusting that CI
  was green on the tagged commit. The tag is the artifact people bookmark.
- **A tag with no CHANGELOG section fails the workflow** before publishing —
  notes are extracted, never hand-written, so the section must exist.

## 4. Wiring — reader/writer pairs

| Writer | Reader | What breaks if they drift |
|---|---|---|
| `VERSION` file (operator edit at release time) | `app/__init__.py::__version__` | Health reports a stale build |
| `app/__init__.py::__version__` | `app/routers/public.py` `/api/health` payload | Health loses the version field |
| `CHANGELOG.md` `[X.Y.Z]` section (operator edit at release time) | release.yml notes-extraction step | Workflow fails: a release with no notes |
| `git tag vX.Y.Z` (operator push) | release.yml trigger + section lookup | Workflow never runs, or runs and fails |

## 5. Tests

- `tests/test_health_surface.py` —
  `test_public_health_reports_the_running_version` pins `__version__` and the
  `/api/health` field to the repo-root VERSION file (one string, not two);
  `test_public_health_is_status_and_version_only` pins the body to exactly
  `{status, version}`; the leak test keeps every diagnostic key out.
- `tests/test_smoke.py` — still asserts `/api/health` never touches the
  database; body shape is owned by `test_health_surface.py`.

Written red-first: both version tests fail with the wiring (VERSION file +
`__init__` reader + payload field) removed, and pass with it in place.
