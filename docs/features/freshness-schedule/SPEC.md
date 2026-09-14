# SPEC: Freshness schedule — weekly on-host timer + advisory CI run

| Field | Value |
|-------|-------|
| Created | 2026-09-14 |
| Updated | 2026-09-14 |
| Status | Implemented |
| Domain | infrastructure |
| Author | تیم پادیار |
| Sources | The 2026-09-14 danesh-bonyan assessment (freshness checker exists but is never scheduled); `scripts/refresh-inotex-context.py` (shipped 2026-08-14, run by hand); the `padyar-watchdog@` unit pattern; commits on `task/ops-loops` |

This document describes what IS shipped, not what was planned.

---

## 1. Scenario

**Who triggers what, from where.** The freshness checker
(`scripts/refresh-inotex-context.py`) has existed since 2026-08-14 but was
only ever run by hand, so a stale official page could sit unnoticed for
months. Now two schedulers run it without a human remembering:

- **On the server** — `padyar-freshness@<install>.timer`, weekly
  (`OnCalendar=weekly`, `Persistent=true` so a week the host was down fires
  the missed check on the next boot). The result lands in journald and in
  `content/freshness-report.json` inside the install.
- **On GitHub** — `.github/workflows/freshness.yml`, Mondays 06:00 UTC,
  `continue-on-error`, report uploaded as an artifact. Advisory on purpose:
  it depends on GitHub-hosted runners reaching the official site.

An operator reads the outcome weekly: `unchanged` rows are silence; a
`changed` row names the source that moved, and the report's `next_step`
says what to do (review, update facts, bump `knowledge_version`, reset
content). Publication of new facts always passes through human review —
the scheduler never edits the database or `app/default_content.py`.

## 2. What ships

| Piece | What it does |
|---|---|
| `deploy/systemd/padyar-freshness@.service` | Oneshot, `User=padyar-%i`, templated on the install slug like `padyar-watchdog@`. Runs the checker with the install's venv. `SuccessExitStatus=2 3` — exit 2 (a page changed) and 3 (a page unreachable) are findings, not failures; a weekly unit going red on its own report would train operators to ignore its state. `ProtectSystem=strict` + `ReadWritePaths=/opt/padyar-%i/content` enforce the read-only contract at the unit level: snapshots and the report under `content/` are the only writes allowed. |
| `deploy/systemd/padyar-freshness@.timer` | Weekly, `Persistent=true`, `WantedBy=timers.target`. |
| `.github/workflows/freshness.yml` | Weekly `schedule` (+ `workflow_dispatch`), stdlib-only script so no dependency install, every step advisory, artifact `freshness-report` uploaded `if: always()`. Touches nothing in `ci.yml`. |
| `deploy/README.md` | Install + enable commands and how to read the outcome. |

## 3. Wiring — reader/writer pairs

| Writer | Reader | What breaks if they drift |
|---|---|---|
| `content/sources.json` manifest (verified facts, 2026-08-14) | The checker reads it on every run, on host and in CI | The check silently covers nothing (empty manifest → exit 0 "fresh") |
| Checker writes `content/snapshots/*.sha256` + `content/freshness-report.json` | Next run's diff baseline; the operator's weekly read | A wiped snapshot dir reports `first_snapshot` instead of `changed` — the change is missed once, never falsely reported |
| `SuccessExitStatus=2 3` in the unit | The checker's documented exit codes (0/2/3) | A changed page marks the unit failed; operators learn the red is noise |
| Timer `Persistent=true` | A host that was down over the weekly elapse | The week's check is silently skipped |

## 4. Verification

- Unit files follow the shipped `padyar-watchdog@` pattern (install-path
  parameterization via `%i`, journald identifiers, hardening block);
  `systemd-analyze` is not available on the dev machine, so the review is
  by inspection.
- Workflow YAML parses (`yaml.safe_load`) and runs the script with no
  dependencies — the checker is stdlib-only.
- No test asserts cron behaviour; the script itself was already shipped and
  tested by hand. The schedule is declarative systemd/Actions config, and
  both files fail loudly in their own tooling if malformed.
