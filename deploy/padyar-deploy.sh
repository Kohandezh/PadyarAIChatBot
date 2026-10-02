#!/usr/bin/env bash
# Deploy one already-installed PadyarAIChatbot instance to a specific commit.
#
#   sudo /usr/local/bin/padyar-deploy <slug> <port> <commit-sha>
#   sudo [PADYAR_ROLLBACK_CONFIRM=<sha>] /usr/local/bin/padyar-deploy <slug> <port> --rollback <sha>
#
# <slug> names the install (APP_DIR=/opt/padyar-<slug>, DB=padyar_<slug>,
# service/user padyar-<slug>); <port> is the install's APP_PORT — the same
# number in /opt/padyar-<slug>/.env, so the health check below probes the
# port the unit actually listens on.
#
# WHAT THIS IS
# ------------
# The whole deploy. An operator runs it by hand on the server after a merge
# to main (deploy/README.md, "Deploying a new version"); CI no longer runs
# anything on this host. Everything dangerous lives here, root-owned,
# reviewed once, changed only through the repository.
#
# The script is idempotent and safe to re-run. A plain deploy only accepts the
# tip of main: any other sha exits with SUPERSEDED and changes nothing (see the
# FETCHED check in step 2). The script resets the code to the old commit on its
# own in three cases: a failed pip install (step 3), a failed migration (step 4)
# and a red health check (step 6).
#
# ROLLING BACK BY HAND
# --------------------
# The normal way: `git revert` on main, wait for green CI, then a plain deploy
# of the revert's sha. Use that whenever there is time for a merge.
#
# `--rollback <sha>` is for when there is no time. It goes back to an older
# commit that main contains, and nowhere else. Step 0 runs
# deploy/rollback-plan.sh (from the checkout, as the app user) against the
# freshly fetched main and the running commit. The planner refuses a target
# that is not on main, is the running commit, or is not behind it, and lists
# every migration the old code has never seen. If it lists any, the script
# stops unless PADYAR_ROLLBACK_CONFIRM holds the same sha (full or short), and
# it warns when one of them is destructive. Then: backup (step 1), checkout,
# deps, NO migrations, restart, health check, the same as a deploy. The
# database is never rolled back: old code cannot apply or undo a newer
# migration. If a listed migration dropped something, the old code may miss
# it, and the step-1 dump of the deploy that applied it is the way back
# (admin panel, Infrastructure > Backups). docs/engineering/DATABASE.md,
# "Destructive migrations: expand, then contract", keeps that list harmless.
#
# THE ORDER IS THE SAFETY
# -----------------------
#   0. plan          --rollback only: fetch main, run rollback-plan.sh, stop
#                    on a refusal or an unconfirmed migration list
#   1. backup        the database is dumped BEFORE anything changes
#   2. checkout      new code lands, but the OLD process keeps serving —
#                    uvicorn has the old files open and .py files are already
#                    imported into memory
#   3. deps          pip install; a failure here aborts before any schema or
#                    restart, leaving the old version fully intact
#   4. migrate       apply_migrations.py — each file in its own transaction;
#                    a failure aborts (old process STILL serving) and resets
#                    the worktree to the old commit. --rollback skips it.
#   5. restart      only now does the new code go live
#   6. health        /api/health, up to 12 tries 5s apart; red => reset to the
#                    old commit + restart. That is a CODE rollback only. The
#                    database is NOT rolled back. Some migrations drop things
#                    (0006, 0013, 0028 drop columns or tables, 0029 deletes a
#                    settings row), so old code may meet a schema it does not
#                    expect. Restoring the step-1 backup is a manual,
#                    explicitly-confirmed action from the admin panel
#                    (Infrastructure > Backups), never an automatic one.
#                    A `git revert` of a deploy that contained a drop also
#                    needs that step-1 backup. See docs/engineering/DATABASE.md.
#
# DURING AN EVENT: do not deploy. Migrations and restarts are for quiet hours
# (DEPLOYMENT_RUNBOOK.md says the same). There is no CI approval step any more;
# this script cannot know the calendar, so the operator is the calendar.
set -euo pipefail

SLUG="${1:-}"
PORT="${2:-}"
NEW_SHA="${3:-}"
MODE=deploy
if [[ "$NEW_SHA" == "--rollback" ]]; then
  MODE=rollback
  NEW_SHA="${4:-}"
  if [[ $# -ne 4 ]]; then
    echo "Usage: sudo $0 <slug> <port> --rollback <commit-sha>" >&2; exit 1
  fi
fi
if [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "Usage: sudo $0 <slug> <port> <commit-sha>" >&2
  echo "       sudo $0 <slug> <port> --rollback <commit-sha>" >&2; exit 1
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "padyar-deploy: '$PORT' is not a port (use the install's APP_PORT)" >&2; exit 1
fi
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

# ── Serialize: one deploy per install at a time ──────────────────────────
# Two approved deploys raced on 2026-08-26: the older run's health checks and
# rollback interleaved with the newer run's reset+restart, and both failed.
# The lock is held for the whole script; a later run WAITS rather than losing,
# because the later run carries the newer sha (and the sha guard below still
# refuses anything that is no longer main's tip). A --rollback takes the same
# lock, so a rollback and a deploy never run at the same time.
exec 9>"/run/padyar-deploy-${SLUG}.lock"
if ! flock -w 900 9; then
  die "another deploy of ${SLUG} is still running after 15 minutes — not starting this one; investigate with: journalctl -u ${SERVICE} and ps aux | grep padyar-deploy"
fi

# Read HEAD only now that the lock is held. Read before the lock, it could be
# the commit a still-running deploy was about to replace: the rollback plan
# and every "reset to the old commit" below would then aim at a stale commit.
CURRENT_SHA=$(as_app "git -C '$APP_DIR' rev-parse HEAD")

# The fetch runs as the app user. PADYAR_GIT_TOKEN (optional) is a read-only
# GitHub token passed on the sudo command line. It authenticates the private
# repository without any stored credential, lives only for this deploy, and
# reaches git through a one-shot credential helper so it never appears in
# git's argv (the sudo command line that passes it in is visible in `ps`
# while the deploy runs). Without it the fetch falls back to whatever credential helper the
# app user already has (a deploy key also works).
fetch_main() {
  if ! as_app_env fetch; then
    die "Fetch of main failed (bad/missing token? network?)."
  fi
}

if [[ "$MODE" == "rollback" ]]; then
  # ── 0. Plan the rollback before anything changes ───────────────────────
  # The planner comes from the RUNNING commit's checkout and runs as the app
  # user: it only reads git, so it needs no root. It prints its own reason
  # on stderr when it refuses.
  log "Fetching main to check rollback target $NEW_SHA"
  fetch_main
  PLANNER="$APP_DIR/deploy/rollback-plan.sh"
  [[ -f "$PLANNER" ]] || die "$PLANNER is missing: the running commit predates --rollback. Use git revert on main instead. Nothing was changed."
  if ! PLAN=$(as_app "bash '$PLANNER' '$APP_DIR' '$NEW_SHA' '$CURRENT_SHA' refs/remotes/deploy/main"); then
    die "Rollback to $NEW_SHA refused (reason above). Nothing was changed."
  fi
  TARGET_SHA=$(as_app "git -C '$APP_DIR' rev-parse --verify '${NEW_SHA}^{commit}'")
  if [[ -n "$PLAN" ]]; then
    log "The database has migrations that $TARGET_SHA has never seen:"
    printf '%s\n' "$PLAN"
    log "They stay applied. This rollback does not run or undo any migration."
    if grep -q ' destructive$' <<<"$PLAN"; then
      log "WARNING: at least one of them is destructive. The old code may look for
a column or table that is gone, and data written by the newer code may be
lost. The way back is the pre-deploy dump of the deploy that applied it:
admin panel, Infrastructure > Backups. See docs/engineering/DATABASE.md."
    fi
    CONFIRM="${PADYAR_ROLLBACK_CONFIRM:-}"
    if [[ ! "$CONFIRM" =~ ^[0-9a-f]{7,40}$ || "$TARGET_SHA" != "$CONFIRM"* ]]; then
      die "Refusing to roll back over these migrations without confirmation. Nothing was changed. If you accept the list above, run:
  sudo PADYAR_GIT_TOKEN=<read-only token> PADYAR_ROLLBACK_CONFIRM=$NEW_SHA $0 $SLUG $PORT --rollback $NEW_SHA
(leave out PADYAR_GIT_TOKEN if the app user has a deploy key)"
    fi
    log "Confirmed by PADYAR_ROLLBACK_CONFIRM=$CONFIRM."
  fi
  NEW_SHA="$TARGET_SHA"
  log "Rolling back $SLUG: $CURRENT_SHA -> $NEW_SHA"
else
  if [[ "$CURRENT_SHA" == "$NEW_SHA" ]]; then
    log "Already at $NEW_SHA. Nothing to do."
    exit 0
  fi
  log "Deploying $SLUG: $CURRENT_SHA -> $NEW_SHA"
fi

# ── 1. Backup the database before anything changes ───────────────────────
log "Taking a pre-$MODE backup"
# Runs as the app user so the dump lands in the app's backups/ dir with the
# right owner. reason=deploy (or rollback) marks it in the manifest and the
# audit log.
if ! as_app "cd '$APP_DIR' && set -a && . ./.env && set +a && \
            .venv/bin/python -c \"from app.services import pg_backup; pg_backup.create(actor='deploy', reason='$MODE')\""; then
  die "Pre-$MODE backup failed. Refusing to touch anything."
fi

# ── 2. Land the new code (old process keeps serving) ─────────────────────
if [[ "$MODE" == "deploy" ]]; then
  log "Fetching $NEW_SHA"
  fetch_main
  FETCHED=$(as_app "git -C '$APP_DIR' rev-parse 'refs/remotes/deploy/main'")
  if [[ "$FETCHED" != "$NEW_SHA" ]]; then
    # The operator asked for this exact sha; main has moved on since (a newer
    # merge landed). Deploy the sha that was asked for, or nothing. A plain
    # deploy never goes back: rolling back is --rollback or a git revert.
    # Exit 0, not 1: nothing was changed and nothing is wrong. The message is
    # loud on purpose so a human reading the log cannot mistake it for success.
    log "SUPERSEDED: origin/main is at $FETCHED, expected $NEW_SHA: a newer commit landed. Nothing was changed; the newer run carries it."
    exit 0
  fi
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
if [[ "$MODE" == "rollback" ]]; then
  # The old code's migrations/ is a subset of what is applied, and the
  # runner has no downgrade. Running it would only re-check checksums.
  log "Skipping migrations: a rollback leaves the database as it is."
else
  log "Applying database migrations"
  if ! as_app "cd '$APP_DIR' && set -a && . ./.env && set +a && \
              .venv/bin/python scripts/apply_migrations.py"; then
    log "Migration failed. Resetting code to $CURRENT_SHA. The old process never
stopped, and a failed migration leaves no partial schema (each file is one
transaction). The pre-deploy backup from step 1 exists if manual restoration
is ever needed."
    as_app "git -C '$APP_DIR' reset --hard '$CURRENT_SHA'"
    die "Migration failed."
  fi
fi

# ── 5. Restart onto the new code ─────────────────────────────────────────
log "Restarting $SERVICE"
systemctl restart "$SERVICE"

# ── 6. Health check, with code rollback ──────────────────────────────────
# 12 tries × 5s = a full minute. Boot is not instant: dataset load, the
# embedding index, intent training and the seeded content all run in the
# lifespan before the first request can be answered. The old 3×5s window
# was tuned when the corpus was 16 entries — the day it grew past ~30 the
# deploy rolled back perfectly healthy code (twice, 2026-08-27) and the
# rollback restart raced the same window, which read as an outage. A
# minute of patience costs nothing; a rollback of good code costs an
# incident. The window stays bounded so a genuinely dead deploy still
# fails inside the job's timeout.
HEALTH_TRIES=12
HEALTH_SLEEP=5
log "Health check ($HEALTH_URL, up to $HEALTH_TRIES tries)"
ok=1
for i in $(seq 1 "$HEALTH_TRIES"); do
  sleep "$HEALTH_SLEEP"
  if curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then ok=0; break; fi
  log "  try $i: not healthy yet"
done
if [[ $ok -ne 0 ]]; then
  log "UNHEALTHY after restart — rolling the CODE back to $CURRENT_SHA."
  log "(The database is NOT rolled back: every applied migration stays applied.
If one of them dropped a column or table, the old code may fail on it; then
restore the step-1 backup from the admin panel, Infrastructure > Backups.
See docs/engineering/DATABASE.md, expand/contract. The backup is untouched.)"
  as_app "git -C '$APP_DIR' reset --hard '$CURRENT_SHA'"
  as_app "cd '$APP_DIR' && .venv/bin/pip install -q -r requirements.txt"
  systemctl restart "$SERVICE"
  # The rollback boots the OLD code — give it the same patience, not the
  # old single 5s shot that reported a healthy rollback as unhealthy.
  ok=1
  for i in $(seq 1 "$HEALTH_TRIES"); do
    sleep "$HEALTH_SLEEP"
    if curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then ok=0; break; fi
  done
  [[ $ok -ne 0 ]] && log "WARNING: rollback also looks unhealthy — see journalctl -u $SERVICE"
  if [[ "$MODE" == "rollback" ]]; then
    die "Rollback to $NEW_SHA failed health check; back on $CURRENT_SHA."
  fi
  die "Deploy of $NEW_SHA failed health check; rolled back to $CURRENT_SHA."
fi

if [[ "$MODE" == "rollback" ]]; then
  log "Rolled back $SLUG to $NEW_SHA and healthy."
else
  log "Deployed $SLUG to $NEW_SHA and healthy."
fi
