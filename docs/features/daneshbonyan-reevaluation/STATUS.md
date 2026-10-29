# Daneshbonyan re-evaluation: status and handoff (2026-10-29)

Written by the main Claude session that coordinated the work (closed after this handoff).
Started 2026-09-30. Goal: fix every reason the evaluator gave for rejecting
PadyarAIChatbot, then file the re-evaluation request ("darkhast-e barresi-ye mojadad").

The per-track briefs and Herdr sessions were deleted after this handoff. The re-evaluation form draft is `FORM_DRAFT.md` in this folder.

## Evaluator's reasons and where each stands

| # | Reason | Status | Evidence on main |
|---|---|---|---|
| 1 | Wrapper over ready-made services, no own AI tech | Mostly addressed | Reproducible benchmark + ablation (#147), own intent model as versioned artifact (#148/#149), own-model readout in admin |
| 2 | Relies on OpenAI/GapGPT, a classic term-weighting engine, no own model | Mostly addressed | The classic engine is gone; local LLM spike + ADR-027 + SPEC (#164); P40 bench harness + measured numbers (#169). Local LLM NOT deployed yet; M4 quality run NOT RUN |
| 3 | Knowledge, questions, synonyms are manual | Addressed | Document/URL ingestion module S1-S4 (#146, #151-#153, #162, #163), human approval gate |
| 4 | DB and deploy not enterprise-mature | Mostly addressed in code | Restore drill (#179), row counts (#172), rollback mode (#176), off-site SFTP + admin settings (#180), gpg key in panel (#185), retention pools (#181), DB connect isolation (#183), RECOVERY_OBJECTIVES.md (#182). NOT applied on the server yet |
| 5 | Vibe coding, no human review / ownership | Process exists, evidence missing | Governance PR template + pr-governance check (#150). Still 0 PRs with a recorded GitHub human review; CODE_OWNERSHIP.md owners empty |
| 6 | No maturity evidence: monitoring, CI/CD, tests, docs | Mostly addressed in code | Monitoring stack (#166), alert rules R01-R10 (#175), watchdog SMS fixes (#170, #177, #178), runbook + SLO (#171), metrics fixes (#156-#160, #168). Stack NOT installed on the server |

All PRs #145-#185 are merged (except #173, closed and replaced by #174).
No open PRs. CI on main is green. CI runs on GitHub-hosted runners only;
the self-hosted deploy job was removed in #155, so merging does NOT deploy.

## Agents (all closed 2026-10-29)

| Tab | Agent | State |
|---|---|---|
| DB7 db-maturity | `foreman7` | Closed. Done except C (pgBackRest) and E (staging), both need the server. Ledger: ~/foreman/20260930-dbmaturity/ |
| DB8 reeval-form | `db8-form` | Closed. Its draft is `FORM_DRAFT.md` here, written 2026-09-30 before any PR, so it is outdated |

DB1-DB6 are finished and closed. Their reports stay in ~/foreman/ (20260930-eval-benchmark, 20260930-danesh-t2,
20260930-db3-localllm, 20260930-monitoring, 20260930-danesh-t5-ingest, 20260930-review-governance).

## Server work (needs the Mac on the gpuserver LAN, 192.168.100.x)

Last check: not reachable (Mac on 192.168.1.x; a VPN tunnel `utun6` answers TCP on port 22 but sends no SSH banner).
Access: GPUSERVER-CREDENTIALS.env at the repo root (0600, git-ignored). `/tmp/.pdyr-pw` is wiped on reboot;
re-create it from that file. Shared socket /tmp/.pdyr-cm.sock. Test and deploy only on inotex; never the second install.

1. Deploy current main to inotex by hand: `sudo PADYAR_GIT_TOKEN=<read-only> /usr/local/bin/padyar-deploy <slug> <port> <sha>`. None of the ~40 PRs is live yet.
2. Re-install /usr/local/bin/padyar-deploy from the repo (for `--rollback`, #176).
3. Re-run deploy/05 (creates the drill DB for #179 and applies the connect isolation of #183; SECURITY.md has the safe steps).
4. Check the watchdog on the server: #178 says it probably never sent an SMS in production.
5. Install the monitoring stack: deploy/55-monitoring.sh (#166).
6. Remove the self-hosted runner from the server (keep /usr/local/bin/padyar-deploy) and from GitHub Settings -> Actions -> Runners.
7. DB7 units C (pgBackRest 2.50, two-step restore) and E (staging), then put real numbers into RECOVERY_OBJECTIVES.md.
8. Enter the SFTP destination and the gpg public key in the admin panel (needs a destination server first).

## Decisions only Sina can make

- RPO 5 min for full server loss: accept? (doc says "derived, not confirmed")
- RPO/RTO target for the "bad deploy" scenario.
- Off-site SFTP destination: none exists yet. Without it, losing gpuserver loses everything.
- Staging: copy `companies` data or not?
- Local LLM: how to run M4 (answer quality on the golden set); approve SPEC + ADR-027; a Persian-speaking human quality check; keep or delete the 15.9 GB CUDA image on the server.
- Should alert SMS skip the daily SMS budget?
- Phone number `0912...024` in git history (sms.py comment, tests): real or placeholder? Matters before making the repo public.
- Re-evaluation form: update FORM_DRAFT.md with today's state and fill its gaps before writing into the Google Doc (the draft predates every PR). Google Docs MCP needs a fresh login.

## Before making the repo public

- Remove the `padyar` runner (item 6 above).
- Settings -> Actions -> General -> "Require approval for all outside collaborators".
- History secret scan (gitleaks, 2026-10-01): 24 hits, all test fixtures/placeholders. No .env, DB, dump or backup was ever committed.
- Public repo also makes GitHub runners free and allows branch protection without GitHub Pro.

## Evidence still missing for reason 5 (most important gap)

- Real human reviews recorded on GitHub PRs (approve with the `Kohandezh` account; `sinashamsizadeh` cannot approve its own PRs).
- Owner names in docs/engineering/CODE_OWNERSHIP.md.
- Branch protection on main (command ready in REVIEW_PROCESS.md).

## Small follow-ups found (each its own ticket)

- CI health check (ci.yml:106) and docker-compose.yml:11 still check Postgres over the socket (same flake as #184).
- deploy/05 fails with a hyphenated slug.
- `_conn_parts()` does not URL-decode the password.
- One non-PostgreSQL test takes 211 s.
- The "بررسی‌نشده" badge is not visible.
- DB5: shared number-rule helper for answer.py/ingest.py; two old races in search.py; confusing message for a typo'd domain; two admins approving the same text at once.
- AI_ASSISTANCE_LOG.md / INDEX.md conflict on almost every merge: add `merge=union` in .gitattributes.

## Lessons for the next session

- Orchestrators stall when a worker's message is lost or a turn dies on a network error; nudge them to check workers with `herdr agent get` and resume.
- Never use `gh pr merge --delete-branch` on a PR that has stacked children: GitHub closes the children (happened to #157). Retarget children to main first.
- After each merge, the next PR usually conflicts in AI_ASSISTANCE_LOG.md; merge strictly one at a time.
- The Bash tool runs zsh: `for x in $list` does not split words; use bash explicitly.
