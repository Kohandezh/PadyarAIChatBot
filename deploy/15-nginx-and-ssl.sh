#!/usr/bin/env bash
# Install the nginx sites and obtain Let's Encrypt certificates for
# inotex.padyar.com and elecomp.padyar.com.
#
#   sudo bash deploy/15-nginx-and-ssl.sh
#
# WHY DNS-01 AND NOT --nginx:
# padyar.com is proxied by Cloudflare (both names resolve to 172.67.141.4).
# An http-01 challenge then has to survive Cloudflare's edge — "Always Use
# HTTPS", caching rules, and the origin being reachable from the internet on
# port 80. A dns-01 challenge needs none of that and works before the origin
# is publicly reachable at all. Set CERT_MODE=http to use webroot instead.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0" >&2; exit 1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAINS=(inotex.padyar.com elecomp.padyar.com)
EMAIL="${CERT_EMAIL:-brainfoemail@gmail.com}"
CERT_MODE="${CERT_MODE:-dns}"
CF_INI=/root/.secrets/cloudflare.ini

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

log "Installing the Cloudflare real-IP config"
install -m 0644 "${HERE}/nginx/cloudflare-realip.conf" /etc/nginx/conf.d/cloudflare-realip.conf

log "Preparing the ACME webroot"
install -d -m 0755 /var/www/certbot

if [[ "$CERT_MODE" == "dns" ]]; then
  if [[ ! -f "$CF_INI" ]]; then
    cat >&2 <<MSG

Cloudflare API token missing.

Create one at  https://dash.cloudflare.com/profile/api-tokens
  Permissions: Zone -> DNS -> Edit
  Zone resources: Include -> Specific zone -> padyar.com

Then, on this server:

  sudo install -d -m 0700 /root/.secrets
  sudo tee /root/.secrets/cloudflare.ini >/dev/null <<'INI'
dns_cloudflare_api_token = PASTE_TOKEN_HERE
INI
  sudo chmod 0600 /root/.secrets/cloudflare.ini

Then re-run this script.  (Or run it with CERT_MODE=http to use webroot.)
MSG
    exit 1
  fi
  chmod 0600 "$CF_INI"
fi

for d in "${DOMAINS[@]}"; do
  if [[ -d "/etc/letsencrypt/live/${d}" ]]; then
    log "Certificate for ${d} already exists — skipping issuance"
    continue
  fi
  log "Requesting a certificate for ${d} (mode: ${CERT_MODE})"
  if [[ "$CERT_MODE" == "dns" ]]; then
    certbot certonly --non-interactive --agree-tos --email "$EMAIL" \
      --dns-cloudflare --dns-cloudflare-credentials "$CF_INI" \
      --dns-cloudflare-propagation-seconds 30 \
      -d "$d"
  else
    certbot certonly --non-interactive --agree-tos --email "$EMAIL" \
      --webroot -w /var/www/certbot -d "$d"
  fi
done

log "Installing the site configurations"
for d in "${DOMAINS[@]}"; do
  install -m 0644 "${HERE}/nginx/${d}.conf" "/etc/nginx/sites-available/${d}.conf"
  ln -sfn "/etc/nginx/sites-available/${d}.conf" "/etc/nginx/sites-enabled/${d}.conf"
done
# The default site would otherwise answer for any unmatched Host.
rm -f /etc/nginx/sites-enabled/default

log "Testing and reloading nginx"
nginx -t
systemctl reload nginx

log "Making renewal reload nginx"
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/usr/bin/env bash
systemctl reload nginx
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
systemctl enable --now certbot.timer 2>/dev/null || true

log "Verifying"
for d in "${DOMAINS[@]}"; do
  echo -n "  ${d} local TLS: "
  curl -sk -o /dev/null -w '%{http_code}\n' --resolve "${d}:443:127.0.0.1" "https://${d}/api/health" || echo "FAILED"
done

cat <<'BANNER'

------------------------------------------------------------
 ONE MANUAL STEP LEFT, IN THE CLOUDFLARE DASHBOARD
------------------------------------------------------------
 Both names currently return HTTP 525 (SSL handshake failed
 between Cloudflare and this origin). After the certificates
 above are live, set:

   SSL/TLS -> Overview -> Full (strict)

 Anything less ("Flexible") leaves Cloudflare -> origin
 traffic unencrypted and makes COOKIE_SECURE meaningless.

 Also confirm the two A records point at this server's public
 address and that 80/443 are forwarded to 192.168.100.6.
------------------------------------------------------------
BANNER
