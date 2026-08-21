#!/usr/bin/env bash
# Publish both sites through a Cloudflare Tunnel.
#
#   sudo CF_TUNNEL_TOKEN=eyJ... bash deploy/40-cloudflare-tunnel.sh
#
# WHY A TUNNEL AND NOT A PORT-FORWARD
# Measured on this host: a probe from outside Iran to 46.100.15.28:443 is
# REFUSED (TCP reset), and a packet capture on the server recorded zero
# inbound SYNs during that probe. Nothing from the internet reaches
# 192.168.100.6, so no origin-side change can fix it. cloudflared dials OUT
# instead — no inbound port, no forward rule, no static IP.
#
# The tunnel runs with a LOCAL config file rather than dashboard-managed
# routing, so the ingress rules are version-controlled here and reviewable,
# instead of living only as clicks in a web UI.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0" >&2; exit 1; fi
if [[ -z "${CF_TUNNEL_TOKEN:-}" ]]; then
  echo "CF_TUNNEL_TOKEN is not set. Zero Trust -> Networks -> Tunnels -> Create." >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

log "Installing cloudflared"
if ! command -v cloudflared >/dev/null 2>&1; then
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    -o /usr/share/keyrings/cloudflare-main.gpg
  chmod 0644 /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y cloudflared
fi
cloudflared --version

log "Deriving tunnel credentials from the token"
# The token is base64url JSON: a = account tag, t = tunnel UUID, s = secret.
# Turning it into a credentials file is what lets this tunnel run from a local
# config. The secret is written straight to a 0600 file and never printed.
install -d -m 0700 /etc/cloudflared
python3 - "$CF_TUNNEL_TOKEN" <<'PY'
import base64, json, sys, os
raw = sys.argv[1].strip()
raw += "=" * (-len(raw) % 4)
d = json.loads(base64.urlsafe_b64decode(raw))
creds = {"AccountTag": d["a"], "TunnelID": d["t"], "TunnelSecret": d["s"]}
path = "/etc/cloudflared/credentials.json"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    json.dump(creds, fh)
with open("/etc/cloudflared/tunnel-id", "w") as fh:
    fh.write(d["t"])
print(f"  tunnel id: {d['t']}")
print(f"  account:   {d['a'][:8]}...")
PY
TUNNEL_ID=$(cat /etc/cloudflared/tunnel-id)

log "Writing the ingress configuration"
# Both hostnames go to nginx on HTTPS 127.0.0.1:443 rather than straight to
# uvicorn, so media serving, the 500m upload limit and the proxy timeouts
# still apply. originServerName makes the Let's Encrypt certificate validate,
# which is what keeps this equivalent to Full (strict).
cat > /etc/cloudflared/config.yml <<CONF
tunnel: ${TUNNEL_ID}
credentials-file: /etc/cloudflared/credentials.json
no-autoupdate: true
loglevel: info

originRequest:
  connectTimeout: 30s
  # The Tier-2 AI fallback can be slow; must not be cut shorter than nginx's
  # own 120s proxy_read_timeout.
  tlsTimeout: 30s
  noHappyEyeballs: true

ingress:
  - hostname: inotex.padyar.com
    service: https://127.0.0.1:443
    originRequest:
      originServerName: inotex.padyar.com
      httpHostHeader: inotex.padyar.com

  - hostname: elecomp.padyar.com
    service: https://127.0.0.1:443
    originRequest:
      originServerName: elecomp.padyar.com
      httpHostHeader: elecomp.padyar.com

  # Anything else that somehow reaches this tunnel is not ours.
  - service: http_status:404
CONF
chmod 0600 /etc/cloudflared/config.yml
cloudflared --config /etc/cloudflared/config.yml ingress validate

log "Installing the systemd service"
cat > /etc/systemd/system/cloudflared.service <<'UNIT'
[Unit]
Description=Cloudflare Tunnel for padyar.com
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/cloudflared --config /etc/cloudflared/config.yml --no-autoupdate tunnel run
Restart=always
RestartSec=5
TimeoutStartSec=0

NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable cloudflared
systemctl restart cloudflared

log "Waiting for the tunnel to register with Cloudflare"
for i in $(seq 1 30); do
  if journalctl -u cloudflared --since "-2min" --no-pager 2>/dev/null | grep -q "Registered tunnel connection"; then
    echo "  registered after ${i}s"
    break
  fi
  sleep 2
done
journalctl -u cloudflared --since "-3min" --no-pager | grep -iE "registered tunnel connection|error|failed" | tail -6

log "Pointing DNS at the tunnel"
# CNAME to <tunnel>.cfargotunnel.com, proxied. This is what replaces the A
# records that pointed at an unreachable public IP.
TOKEN=$(awk -F' = ' '{print $2}' /root/.secrets/cloudflare.ini)
ZONE=$(curl -s "https://api.cloudflare.com/client/v4/zones?name=padyar.com" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json;print(json.load(sys.stdin)['result'][0]['id'])")

for host in inotex.padyar.com elecomp.padyar.com; do
  rec=$(curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records?name=$host" \
    -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json;r=json.load(sys.stdin)['result'];print(r[0]['id'] if r else '')")
  body=$(printf '{"type":"CNAME","name":"%s","content":"%s.cfargotunnel.com","proxied":true,"ttl":1}' "$host" "$TUNNEL_ID")
  if [[ -n "$rec" ]]; then
    out=$(curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records/$rec" \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data "$body")
  else
    out=$(curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records" \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data "$body")
  fi
  echo "$out" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('  $host ->', d['result']['content'], 'proxied=' + str(d['result']['proxied'])) if d.get('success') \
  else print('  $host FAILED:', [e.get('message') for e in d.get('errors',[])])
"
done

log "Done. Verify from outside with:  curl -I https://inotex.padyar.com/"
