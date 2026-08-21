#!/usr/bin/env bash
# Install (or upgrade) one PadyarAIChatbot instance.
#
#   sudo bash deploy/10-install-app.sh inotex
#   sudo bash deploy/10-install-app.sh elecomp
#
# Expects deploy/05-create-databases.sh to have run, and the matching
# /opt/padyar-<slug>/.env to exist (copy it from deploy/env/<slug>.env.template
# and fill in the real values first — the app REFUSES to boot in production
# with a placeholder, by design: app/prodcheck.py).
set -euo pipefail

SLUG="${1:-}"
case "$SLUG" in
  inotex)  PORT=8001 ;;
  elecomp) PORT=8002 ;;
  *) echo "Usage: sudo bash $0 {inotex|elecomp}" >&2; exit 1 ;;
esac

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0 $SLUG" >&2; exit 1; fi

APP_DIR="/opt/padyar-${SLUG}"
STATE_DIR="/var/lib/padyar/${SLUG}"
USER="padyar-${SLUG}"
REPO="${PADYAR_REPO:-https://github.com/Kohandezh/PadyarAIChatBot.git}"
BRANCH="${PADYAR_BRANCH:-main}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

log "Fetching the application into ${APP_DIR}"
# Two sources. PADYAR_SRC (a local directory, usually rsynced from a developer
# machine) wins, because the GitHub repository is private and a server that
# cannot authenticate to it must still be installable.
if [[ -n "${PADYAR_SRC:-}" ]]; then
  echo "  syncing from ${PADYAR_SRC}"
  # .env, .venv and media are install state, not source — never overwrite them.
  rsync -a --delete \
    --exclude '.env' --exclude '.venv' --exclude 'media' \
    --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'chat_history.db' --exclude 'application_logs.db' \
    --exclude 'ADMIN_CREDENTIALS.txt' \
    "${PADYAR_SRC}/" "${APP_DIR}/"
  chown -R "$USER:$USER" "$APP_DIR"
elif [[ -d "${APP_DIR}/.git" ]]; then
  sudo -u "$USER" git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  sudo -u "$USER" git -C "$APP_DIR" reset --hard "origin/${BRANCH}"
else
  # /opt/padyar-<slug> is the service user's home and already exists.
  find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name '.env' ! -name '.venv' -exec rm -rf {} +
  sudo -u "$USER" git clone --depth 1 --branch "$BRANCH" "$REPO" "${APP_DIR}/.checkout"
  sudo -u "$USER" bash -c "shopt -s dotglob && mv '${APP_DIR}/.checkout'/* '${APP_DIR}/' && rmdir '${APP_DIR}/.checkout'"
fi

log "Pointing media/ at persistent storage"
# app/config.py:32 hardcodes VIDEO_DIR = BASE_DIR/media/videos and app/main.py
# mounts StaticFiles(directory="media") by RELATIVE path, so the directory has
# to sit inside the checkout. A symlink keeps the bytes on /var/lib.
if [[ -d "${APP_DIR}/media" && ! -L "${APP_DIR}/media" ]]; then
  rsync -a "${APP_DIR}/media/" "${STATE_DIR}/media/"
  rm -rf "${APP_DIR}/media"
fi
ln -sfn "${STATE_DIR}/media" "${APP_DIR}/media"
chown -h "$USER:$USER" "${APP_DIR}/media"
chown -R "$USER:$USER" "${STATE_DIR}"

log "Building the virtualenv"
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  sudo -u "$USER" python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u "$USER" "${APP_DIR}/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$USER" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

log "Checking the .env"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  install -o "$USER" -g "$USER" -m 0600 "${HERE}/env/${SLUG}.env.template" "${APP_DIR}/.env"
  echo "  Created ${APP_DIR}/.env from the template."
  echo "  FILL IT IN, then re-run this script. Startup will fail until you do."
  exit 2
fi
chown "$USER:$USER" "${APP_DIR}/.env"
chmod 0600 "${APP_DIR}/.env"

# Catch an unfilled template BEFORE sourcing it. A leftover <PLACEHOLDER>
# contains shell metacharacters, so `. .env` would fail with a redirection
# error that says nothing about the real problem.
if grep -qE '^[A-Z_]+=.*<[A-Z]' "${APP_DIR}/.env"; then
  echo "Unfilled placeholders in ${APP_DIR}/.env:" >&2
  grep -nE '^[A-Z_]+=.*<[A-Z]' "${APP_DIR}/.env" | sed 's/^/  /' >&2
  exit 2
fi
# The systemd unit passes --workers ${WEB_CONCURRENCY}; an unset value would
# make uvicorn exit with an unhelpful argument error on every restart.
if ! grep -qE '^WEB_CONCURRENCY=[0-9]+' "${APP_DIR}/.env"; then
  echo "WEB_CONCURRENCY must be set to a number in ${APP_DIR}/.env" >&2
  exit 2
fi

log "Pre-warming the local embedding model cache"
# model2vec downloads on first use. Doing it here means the first visitor does
# not wait for it, and a blocked network fails now instead of at the exhibition.
sudo -u "$USER" bash -c "cd '${APP_DIR}' && .venv/bin/python -c \"
from app.services import embeddings
assert embeddings.available(), 'model2vec not installed'
idx = embeddings.build_index(['سلام'])
print('embedding backend ready' if idx else 'embedding backend UNAVAILABLE')
\"" 2>&1 | tail -3 || echo "  WARNING: embedding pre-warm failed; the app falls back to TF-IDF."

log "Applying database migrations"
sudo -u "$USER" bash -c "set -a; . '${APP_DIR}/.env'; set +a; cd '${APP_DIR}' && .venv/bin/python scripts/apply_migrations.py"

log "Installing the systemd unit"
install -m 0644 "${HERE}/systemd/padyar-${SLUG}.service" "/etc/systemd/system/padyar-${SLUG}.service"
systemctl daemon-reload
systemctl enable "padyar-${SLUG}"
systemctl restart "padyar-${SLUG}"

log "Waiting for the service to answer on 127.0.0.1:${PORT}"
for i in $(seq 1 30); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "  healthy after ${i}s"
    curl -s "http://127.0.0.1:${PORT}/api/health"; echo
    exit 0
  fi
  sleep 1
done

echo "SERVICE DID NOT BECOME HEALTHY. Last 40 log lines:" >&2
journalctl -u "padyar-${SLUG}" -n 40 --no-pager >&2
exit 1
