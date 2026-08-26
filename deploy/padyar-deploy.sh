#!/usr/bin/env bash
# Deploy one already-installed PadyarAIChatbot instance to a specific commit.
#
#   sudo /usr/local/bin/padyar-deploy <slug> <commit-sha>
#
# WHAT THIS IS
# ------------
# The whole server-side half of the auto-deploy pipeline. The GitHub Actions
# job (deploy/padyar-deploy.yml in the runner's copy of the repo) does exactly
# one privileged thing: call this script. Everything dangerous lives here,
# root-owned, reviewed once, changed only through the repository — the runner
# user itself can read the app but cannot touch systemd, postgres or nginx.
#
# The script is idempotent and safe to re-run. It is also the rollback path:
# calling it with an OLD sha walks the same steps backwards.
#
# THE ORDER IS THE SAFETY
# -----------------------
#   1. backup        the database is dumped BEFORE anything changes
#   2. checkout      new code lands, but the OLD process keeps serving —
#                    uvicorn has the old files open and .py files are already
#                    imported into memory
#   3. deps          pip install; a failure here aborts before any schema or
#                    restart, leaving the old version fully intact
#   4. migrate       apply_migrations.py — each file in its own transaction;
#                    a failure aborts (old process STILL serving) and resets
#                    the worktree to the old commit
#   5. restart       only now does the new code go live
#   6. health        /health × 3; red => reset to the old commit + restart.
#                    That is a CODE rollback. The database is additive-only
#                    so far in this project's history; if a migration is ever
#                    destructive, restoring the step-1 backup is a manual,
#   explicitly-confirmed action from the admin panel (Infrastructure >
#   Backups), never an automatic one.
#
# DURING AN EVENT: do not deploy. Migrations and restarts are for quiet hours
# (DEPLOYMENT_RUNBOOK.md says the same). The GitHub side has an approval
# click; this script cannot know the calendar, so the operator is the calendar.
set -euo pipefail

SLUG="${1:-}"
NEW_SHA="${2:-}"
case "$SLUG" in
  inotex)  PORT=8001 ;;
  elecomp) PORT=8002 ;;
  *) echo "Usage: sudo $0 {inotex|elecomp} <commit-sha>" >&2; exit 1 ;;
esac
if [[ ! "$NEW_SHA" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "padyar-deploy: '$NEW_SHA' is not a commit sha" >&2; exit 1
fi
if [[ $EUID -ne 0 ]]; then echo "Run with sudo." >&2; exit 1; fi

APP_DIR="/opt/padyar-${SLUG}"
USER="padyar-${SLUG}"
SERVICE="padyar-${SLUG}"
# The app's liveness endpoint (app/routers/public.py). NOT /health — that
# route does not exist, and a 404 here would read as "unhealthy" and trigger
# a pointless rollback on a perfectly good deploy.
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"

log()  { printf '\n==> %s\n' "$*"; }
die()  { printf '\n!! %s\n' "$*" >&2; exit 1; }
as_app() { sudo -u "$USER" bash -c "$*"; }

# One authenticated fetch of main, as the app user. The remote-tracking ref
# refs/remotes/deploy/main is deliberately NOT origin/main: origin is the
# install-time URL and may carry no credentials, and overwriting its
# remote-tracking refs from here would confuse a later manual pull.
as_app_env() {
  if [[ -n "${PADYAR_GIT_TOKEN:-}" ]]; then
    sudo -u "$USER" PADYAR_GIT_TOKEN="$PADYAR_GIT_TOKEN" bash -c "
      cd '$APP_DIR' && git -c credential.helper='!f() {
            echo username=x-access-token
            echo password=\$PADYAR_GIT_TOKEN
          }; f' fetch https://github.com/Kohandezh/PadyarAIChatBot.git \
              '+refs/heads/main:refs/remotes/deploy/main'"
  else
    as_app "git -C '$APP_DIR' fetch origin '+refs/heads/main:refs/remotes/deploy/main'"
  fi
}

[[ -d "$APP_DIR/.git" ]] || die "$APP_DIR is not a git checkout; run deploy/10-install-app.sh first."
CURRENT_SHA=$(as_app "git -C '$APP_DIR' rev-parse HEAD")

# ── Serialize: one deploy per install at a time ──────────────────────────
# Two approved deploys raced on 2026-08-26: the older run's health checks and
# rollback interleaved with the newer run's reset+restart, and both failed.
# The lock is held for the whole script; a later run WAITS rather than losing,
# because the later run carries the newer sha (and the sha guard below still
# refuses anything that is no longer main's tip).
exec 9>"/run/padyar-deploy-${SLUG}.lock"
if ! flock -w 900 9; then
  die "another deploy of ${SLUG} is still running after 15 minutes — not starting this one; investigate with: journalctl -u ${SERVICE} and ps aux | grep padyar-deploy"
fi

if [[ "$CURRENT_SHA" == "$NEW_SHA" ]]; then
  log "Already at $NEW_SHA — nothing to do."
  exit 0
fi
log "Deploying $SLUG: $CURRENT_SHA -> $NEW_SHA"

# ── 1. Backup the database before anything changes ───────────────────────
log "Taking a pre-deploy backup"
# Runs as the app user so the dump lands in the app's backups/ dir with the
# right owner. reason=deploy marks it in the manifest and the audit log.
if ! as_app "cd '$APP_DIR' && set -a && . ./.env && set +a && \
            .venv/bin/python -c \"from app.services import pg_backup; pg_backup.create(actor='deploy', reason='deploy')\""; then
  die "Pre-deploy backup failed. Refusing to touch anything."
fi

# ── 2. Land the new code (old process keeps serving) ─────────────────────
log "Fetching $NEW_SHA"
# The fetch runs as the app user. PADYAR_GIT_TOKEN (optional) is the GitHub
# job token, passed through sudo's env_keep — it authenticates the private
# repository without any stored credential, lives only for this deploy, and
# reaches git through a one-shot credential helper so it never appears in
# argv. Without it the fetch falls back to whatever credential helper the
# app user already has (a deploy key also works).
if ! as_app_env fetch; then
  die "Fetch of main failed (bad/missing token? network?)."
fi
FETCHED=$(as_app "git -C '$APP_DIR' rev-parse 'refs/remotes/deploy/main'")
if [[ "$FETCHED" != "$NEW_SHA" ]]; then
  # The workflow asked for this exact sha; main has moved on since (a newer
  # merge raced us). Deploy the sha that was approved, or nothing.
  # Exit 0, not 1: nothing was changed and nothing is wrong — the older run
  # aborting here used to fail the whole CI job and page someone for a race
  # that the newer queued run already resolves (2026-08-26). The message is
  # loud on purpose so a human reading the log cannot mistake it for success.
  log "SUPERSEDED: origin/main is at $FETCHED, expected $NEW_SHA — a newer commit landed. Nothing was changed; the newer run carries it."
  exit 0
fi
as_app "git -C '$APP_DIR' reset --hard '$NEW_SHA'"

# ── 3. Dependencies ──────────────────────────────────────────────────────
log "Installing dependencies"
if ! as_app "cd '$APP_DIR' && .venv/bin/pip install -q -r requirements.txt"; then
  log "pip failed — resetting to $CURRENT_SHA; the old process never stopped."
  as_app "git -C '$APP_DIR' reset --hard '$CURRENT_SHA'"
  die "Dependency install failed."
fi

# ── 4. Migrations ────────────────────────────────────────────────────────
log "Applying database migrations"
if ! as_app "cd '$APP_DIR' && set -a && . ./.env && set +a && \
            .venv/bin/python scripts/apply_migrations.py"; then
  log "Migration failed — resetting code to $CURRENT_SHA. The old process never
stopped, and a failed migration leaves no partial schema (each file is one
transaction). The pre-deploy backup from step 1 exists if manual restoration
is ever needed."
  as_app "git -C '$APP_DIR' reset --hard '$CURRENT_SHA'"
  die "Migration failed."
fi

# ── 5. Restart onto the new code ─────────────────────────────────────────
log "Restarting $SERVICE"
systemctl restart "$SERVICE"

# ── 6. Health check, with code rollback ──────────────────────────────────
log "Health check ($HEALTH_URL, 3 tries)"
ok=1
for i in 1 2 3; do
  sleep 5
  if curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then ok=0; break; fi
  log "  try $i: not healthy yet"
done
if [[ $ok -ne 0 ]]; then
  log "UNHEALTHY after restart — rolling the CODE back to $CURRENT_SHA."
  log "(The database keeps the applied migrations: they are additive, and the
old code ignores tables it does not know. The step-1 backup is untouched.)"
  as_app "git -C '$APP_DIR' reset --hard '$CURRENT_SHA'"
  as_app "cd '$APP_DIR' && .venv/bin/pip install -q -r requirements.txt"
  systemctl restart "$SERVICE"
  sleep 5
  curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1 \
    || log "WARNING: rollback also looks unhealthy — see journalctl -u $SERVICE"
  die "Deploy of $NEW_SHA failed health check; rolled back to $CURRENT_SHA."
fi

log "Deployed $SLUG to $NEW_SHA and healthy."
