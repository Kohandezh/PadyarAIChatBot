#!/usr/bin/env bash
# Bootstrap a bare Ubuntu 24.04 host for two PadyarAIChatbot installs.
#
# Idempotent: safe to re-run. Installs packages, creates the two service
# users and their directories, hardens PostgreSQL, and opens the firewall.
# It does NOT install the apps (10-install-app.sh) or the GPU stack
# (20-gpu-chatterbox.sh).
#
# Run as a user with sudo:  sudo bash deploy/00-bootstrap-server.sh
set -euo pipefail

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2; exit 1
fi

log "Updating the package index"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

log "Installing base packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git curl wget ca-certificates gnupg software-properties-common \
  build-essential pkg-config unzip zip rsync jq htop tmux vim \
  python3 python3-venv python3-pip python3-dev \
  postgresql postgresql-contrib postgresql-client libpq-dev \
  nginx ffmpeg certbot python3-certbot-nginx python3-certbot-dns-cloudflare \
  ufw fail2ban unattended-upgrades

log "Verifying PostgreSQL is 16.x"
psql_version=$(sudo -u postgres psql -tAc 'SHOW server_version;' | cut -d. -f1)
if [[ "$psql_version" != "16" ]]; then
  echo "WARNING: PostgreSQL major version is ${psql_version}, expected 16." >&2
fi

log "Creating service users"
for u in padyar-inotex padyar-elecomp padyar-tts; do
  if ! id -u "$u" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/opt/${u}" --shell /usr/sbin/nologin "$u"
    echo "  created $u"
  else
    echo "  $u already exists"
  fi
done

log "Creating state and log directories"
for slug in inotex elecomp; do
  install -d -o "padyar-${slug}" -g "padyar-${slug}" -m 0755 \
    "/var/lib/padyar/${slug}/media/videos" \
    "/var/lib/padyar/${slug}/media/uploads" \
    "/var/lib/padyar/${slug}/backups" \
    "/var/log/padyar/${slug}"
done
install -d -o padyar-tts -g padyar-tts -m 0755 \
  /var/lib/padyar/tts/models /var/lib/padyar/tts/cache /var/log/padyar/tts

log "Hardening PostgreSQL authentication (scram-sha-256, localhost only)"
PG_CONF=$(sudo -u postgres psql -tAc 'SHOW config_file;')
PG_HBA=$(sudo -u postgres psql -tAc 'SHOW hba_file;')
cp -n "$PG_HBA" "${PG_HBA}.padyar.bak"
# Local TCP connections must present a password. Never 'trust' in production.
sed -i -E 's/^(host\s+all\s+all\s+127\.0\.0\.1\/32\s+).*$/\1scram-sha-256/' "$PG_HBA"
sed -i -E 's/^(host\s+all\s+all\s+::1\/128\s+).*$/\1scram-sha-256/' "$PG_HBA"
sudo -u postgres psql -c "ALTER SYSTEM SET password_encryption = 'scram-sha-256';"
sudo -u postgres psql -c "ALTER SYSTEM SET listen_addresses = 'localhost';"
systemctl restart postgresql

log "Configuring the firewall"
ufw allow 22/tcp   comment 'SSH'
ufw allow 80/tcp   comment 'HTTP (ACME + redirect)'
ufw allow 443/tcp  comment 'HTTPS'
ufw --force enable
ufw status verbose

log "Enabling fail2ban (sshd + nginx)"
cat > /etc/fail2ban/jail.d/padyar.conf <<'JAIL'
[sshd]
enabled  = true
maxretry = 5
bantime  = 1h

# nginx's own auth log. The application's admin lockout is separate and
# lives in app/routers/admin.py.
[nginx-http-auth]
enabled = true

[nginx-limit-req]
enabled = true
maxretry = 20
bantime  = 10m
JAIL
systemctl enable --now fail2ban
systemctl restart fail2ban

log "Done. Next: deploy/05-create-databases.sh"
