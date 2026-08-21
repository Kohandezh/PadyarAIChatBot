#!/usr/bin/env bash
# Regenerate /etc/nginx/conf.d/cloudflare-realip.conf from Cloudflare's
# published ranges, then reload nginx if the config still parses.
set -euo pipefail
OUT=/etc/nginx/conf.d/cloudflare-realip.conf
TMP=$(mktemp)
{
  echo "# Generated $(date -u +%Y-%m-%dT%H:%M:%SZ) by deploy/refresh-cloudflare-ips.sh"
  curl -fsS https://www.cloudflare.com/ips-v4 | sed 's/^/set_real_ip_from /; s/$/;/'
  curl -fsS https://www.cloudflare.com/ips-v6 | sed 's/^/set_real_ip_from /; s/$/;/'
  echo "real_ip_header CF-Connecting-IP;"
} > "$TMP"
install -m 0644 "$TMP" "$OUT"
rm -f "$TMP"
nginx -t && systemctl reload nginx
echo "Updated $OUT"
