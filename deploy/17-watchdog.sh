#!/usr/bin/env bash
# Install the critical watchdog: the shared probe script, its systemd
# timer + template units, and the branded nginx maintenance pages.
#
#   sudo bash deploy/17-watchdog.sh <slug> <port> <domain>
#
# <port> must be the install's APP_PORT (from /opt/padyar-<slug>/.env) — the
# watchdog probes 127.0.0.1:<port>. Set MAINTENANCE_TITLE to the install's
# visitor-facing name for the maintenance page (default: چت‌بات پایدیار).
#
# Expects deploy/10-install-app.sh and
# deploy/15-nginx-and-ssl.sh to have run first: the watchdog reads the
# install's database through that install's venv, and the maintenance pages
# are served by the vhosts the nginx script installs. Safe to re-run.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0 <slug> <port> <domain>" >&2; exit 1; fi

SLUG="${1:-}"
PORT="${2:-}"
DOMAIN="${3:-}"
if [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] || [[ ! "$PORT" =~ ^[0-9]+$ ]] || [[ -z "$DOMAIN" ]]; then
  echo "Usage: sudo bash $0 <slug> <port> <domain>" >&2; exit 1
fi
INSTALLS=("$SLUG")

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

log "Creating the per-install state directories"
# One directory per install, owned by that install's user. The watchdog
# services then never share a writable path, so one broken (or compromised)
# install cannot read, corrupt, or lock another's alert state.
for slug in "${INSTALLS[@]}"; do
  install -d -o "padyar-${slug}" -g "padyar-${slug}" "/var/lib/padyar-watchdog/${slug}"
done

log "Installing the watchdog script"
install -d /opt/padyar-watchdog
install -m 0755 "${HERE}/watchdog/watchdog.py" /opt/padyar-watchdog/watchdog.py

log "Installing the systemd units"
install -m 0644 "${HERE}/systemd/padyar-watchdog@.service" /etc/systemd/system/padyar-watchdog@.service
install -m 0644 "${HERE}/systemd/padyar-watchdog@.timer" /etc/systemd/system/padyar-watchdog@.timer
systemctl daemon-reload

log "Rendering the branded maintenance pages"
# The vhosts serve /__maintenance.html from these roots on 502/504
# (proxy_intercept_errors). maintenance.html carries {{SITE_TITLE}} twice;
# it is replaced once per install here so nginx never templates anything.
# The title is the install's visitor-facing name — pass it as
# MAINTENANCE_TITLE; re-running without it resets the page to the default.
TITLE="${MAINTENANCE_TITLE:-چت‌بات پایدیار}"
for slug in "${INSTALLS[@]}"; do
  install -d "/var/www/padyar/maintenance/${slug}"
  sed "s/{{SITE_TITLE}}/${TITLE}/" "${HERE}/nginx/maintenance.html" \
    > "/var/www/padyar/maintenance/${slug}/__maintenance.html"
done

log "Re-installing the nginx vhosts"
# The repo vhosts now carry the error_page blocks pointing at the maintenance
# roots above. Re-installing picks those up on boxes where 15-nginx-and-ssl.sh
# ran before the blocks existed. Same render and layout as that script:
# sites-available/<domain>.conf + sites-enabled symlink.
for d in "$DOMAIN"; do
  sed -e "s/{{DOMAIN}}/${d}/g" -e "s/{{SLUG}}/${SLUG}/g" -e "s/{{PORT}}/${PORT}/g" \
    "${HERE}/nginx/instance.conf.template" > "/etc/nginx/sites-available/${d}.conf"
  ln -sfn "/etc/nginx/sites-available/${d}.conf" "/etc/nginx/sites-enabled/${d}.conf"
done
nginx -t
systemctl reload nginx

log "Enabling the watchdog timers"
for slug in "${INSTALLS[@]}"; do
  systemctl enable --now "padyar-watchdog@${slug}.timer"
done

cat <<NEXT

------------------------------------------------------------
 WATCHDOG IS LIVE. NEXT STEPS FOR THE OPERATOR
------------------------------------------------------------
 Check the timer is scheduled:
   systemctl list-timers 'padyar-watchdog@*'
 Check what a cycle reported:
   journalctl -u padyar-watchdog@${SLUG}.service -n 20

 NO SMS WILL GO OUT until the alert phone number is set in the
 install's admin panel: تنظیمات → ثبت‌نام و پیامک. The watchdog reads it from
 the database on every cycle and skips alerting while it is empty.
------------------------------------------------------------
NEXT
