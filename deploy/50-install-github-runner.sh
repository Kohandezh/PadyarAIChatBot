#!/usr/bin/env bash
# Install a self-hosted GitHub Actions runner for auto-deploy.
#
#   sudo bash deploy/50-install-github-runner.sh <registration-token>
#
# WHY A SELF-HOSTED RUNNER
# ------------------------
# The server sits behind NAT/Cloudflare Tunnel, so GitHub's hosted runners
# cannot reach it. A self-hosted runner works the other way around: it dials
# OUT to GitHub over 443, pulls jobs, and runs them on the server itself.
# Nothing inbound opens.
#
# WHAT THIS SCRIPT DOES — AND DELIBERATELY DOES NOT
# -------------------------------------------------
#   * creates a dedicated `gh-runner` system user (no login shell)
#   * installs the runner under /var/lib/gh-runner, as a systemd service
#     that auto-starts on boot and restarts on failure
#   * labels it `padyar` (the workflow selects it with runs-on: [self-hosted,
#     padyar])
#   * installs deploy/padyar-deploy.sh to /usr/local/bin (root-owned,
#     mode 0755) and grants the runner EXACTLY ONE privilege: running that
#     script without a password. Not systemctl, not git, not anything else —
#     the deploy script is the reviewed, root-owned surface; the runner can
#     call it but cannot change it.
#   * the runner user is deliberately NOT given read access to the app .env
#     files: the deploy fetch uses GitHub's ephemeral job token, so no
#     credential of any kind is stored on this machine for the runner.
#
# THE REGISTRATION TOKEN
# ----------------------
# Short-lived (1 hour), single-purpose. On a machine where `gh` is logged in
# as a repo admin:
#
#   gh api -X POST repos/Kohandezh/PadyarAIChatBot/actions/runners/registration-token \
#       --jq .token
#
# Then run this script with that token before the hour is up.
#
# TO REMOVE (cleanly, later):
#   sudo systemctl stop github-actions-runner.{service,socket} 2>/dev/null || true
#   sudo /var/lib/gh-runner/config.sh remove --token <fresh-token>
#   sudo systemctl disable --now github-actions-runner 2>/dev/null || true
#   sudo userdel -r gh-runner && sudo rm -f /usr/local/bin/padyar-deploy /etc/sudoers.d/gh-runner-deploy
set -euo pipefail

TOKEN="${1:-}"
REPO_URL="https://github.com/Kohandezh/PadyarAIChatBot"
RUNNER_USER="gh-runner"
RUNNER_DIR="/var/lib/gh-runner"
SERVICE_NAME="github-actions-runner"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then echo "Run with sudo." >&2; exit 1; fi
if [[ -z "$TOKEN" ]]; then
  echo "Usage: sudo bash $0 <registration-token>" >&2
  echo "Get one (valid 1h): gh api -X POST ${REPO_URL/github.com/}/actions/runners/registration-token --jq .token" >&2
  exit 1
fi

log "Creating the $RUNNER_USER user"
if ! id -u "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$RUNNER_DIR" --shell /usr/sbin/nologin "$RUNNER_USER"
fi

log "Installing the deploy script (root-owned — the runner may call it, never edit it)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 0755 -o root -g root "$HERE/padyar-deploy.sh" /usr/local/bin/padyar-deploy

log "Granting the runner exactly one privilege"
# The single sudoers surface. No wildcard, no NOPASSWD:ALL, no editor. If the
# deploy script needs to change, it changes in the repository and is
# re-installed by re-running this script — never by the runner itself.
#
# PADYAR_GIT_TOKEN is the GitHub job token (ephemeral: it dies when the job
# ends). env_keep lets the workflow pass it through `sudo VAR=... cmd` so the
# deploy script can authenticate the app user's git fetch of the private
# repository — no credential of any kind is stored on this machine. The token
# travels in process environments (/proc/PID/environ, owner-only) and never
# in argv, on disk, or in logs (GitHub masks it anyway).
cat > /etc/sudoers.d/gh-runner-deploy <<'SUDOERS'
gh-runner ALL=(root) NOPASSWD: /usr/local/bin/padyar-deploy inotex *, /usr/local/bin/padyar-deploy elecomp *
Defaults:gh-runner env_keep += "PADYAR_GIT_TOKEN"
SUDOERS
chmod 0440 /etc/sudoers.d/gh-runner-deploy
visudo -cf /etc/sudoers.d/gh-runner-deploy >/dev/null

if [[ -d "$RUNNER_DIR/.runner" ]]; then
  log "Runner already registered at $RUNNER_DIR — reconfiguring"
else
  log "Downloading the latest runner"
  # Resolved from the API so this script does not rot on a hardcoded version.
  RUNNER_VER=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed 's/^v//')
  curl -fsSL -o /tmp/runner.tgz "https://github.com/actions/runner/releases/download/v${RUNNER_VER}/actions-runner-linux-x64-${RUNNER_VER}.tar.gz"
  mkdir -p "$RUNNER_DIR"
  tar -xzf /tmp/runner.tgz -C "$RUNNER_DIR"
  rm -f /tmp/runner.tgz
  chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"
fi

log "Configuring the runner (label: padyar)"
sudo -u "$RUNNER_USER" bash -c "cd '$RUNNER_DIR' && ./config.sh --url '$REPO_URL' --token '$TOKEN' \
  --name padyar-gpuserver --labels padyar --unattended --replace"

log "Installing and starting the systemd service"
# svc.sh install must run AS ROOT — it writes the unit under /etc/systemd and
# chowns the runner directory. Running it via `sudo -u gh-runner … sudo …`
# would prompt for a password the system user does not have. The unit it
# writes runs the service as the gh-runner user (passed as the argument).
(cd "$RUNNER_DIR" && ./svc.sh install "$RUNNER_USER")
systemctl enable --now "$SERVICE_NAME" 2>/dev/null || systemctl enable --now "${SERVICE_NAME}.service"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME" || { journalctl -u "$SERVICE_NAME" -n 30 --no-pager; exit 1; }

log "Done. The runner is online and labelled 'padyar'."
log "Next: merge the deploy workflow, then watch the first run deploy itself."
