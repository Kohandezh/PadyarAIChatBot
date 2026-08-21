#!/usr/bin/env bash
# End-to-end smoke test. Run as any user, after everything else.
set -uo pipefail
fail=0
ok()   { printf '  \033[1;32mOK\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fail=1; }
warn() { printf '  \033[1;33mWARN\033[0m %s\n' "$*"; }

echo "== services =="
for s in postgresql nginx padyar-inotex padyar-elecomp padyar-tts; do
  if systemctl is-active --quiet "$s"; then ok "$s active"; else bad "$s not active"; fi
  if systemctl is-enabled --quiet "$s" 2>/dev/null; then :; else warn "$s not enabled at boot"; fi
done

echo
echo "== app health (loopback) =="
for pair in "inotex 8001" "elecomp 8002"; do
  slug=${pair%% *}; port=${pair##* }
  body=$(curl -fsS --max-time 5 "http://127.0.0.1:${port}/api/health" 2>/dev/null)
  if [[ -n "$body" ]]; then ok "${slug} /api/health: ${body:0:120}"; else bad "${slug} /api/health unreachable"; fi
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${port}/api/ready")
  case "$code" in
    200) ok "${slug} /api/ready 200 (retrieval index built)" ;;
    503) warn "${slug} /api/ready 503 — index still building, re-check in a minute" ;;
    *)   bad "${slug} /api/ready returned ${code}" ;;
  esac
done

echo
echo "== the app must see the REAL client IP, not 127.0.0.1 =="
# A wrong answer here is silent in normal use and catastrophic under load:
# every visitor shares one rate-limit bucket and one admin lockout counter.
for pair in "inotex 8001" "elecomp 8002"; do
  slug=${pair%% *}; port=${pair##* }
  seen=$(curl -s --max-time 5 -H 'X-Forwarded-For: 203.0.113.9' \
         "http://127.0.0.1:${port}/api/health" -o /dev/null -w '%{http_code}')
  [[ "$seen" == "200" ]] && ok "${slug} accepts proxied requests" || bad "${slug} proxy request returned ${seen}"
done
grep -q 'forwarded-allow-ips' /etc/systemd/system/padyar-inotex.service \
  && ok "uvicorn runs with --proxy-headers --forwarded-allow-ips" \
  || bad "systemd unit is missing --proxy-headers/--forwarded-allow-ips"
[[ -f /etc/nginx/conf.d/cloudflare-realip.conf ]] \
  && ok "cloudflare real-ip config installed" \
  || bad "cloudflare real-ip config MISSING — all visitors share one rate limit"
# Test the EFFECTIVE config, not the sites-enabled directory: those entries are
# symlinks and `grep -r` does not follow them, so a directory grep silently
# passes whatever it was meant to catch.
# Comments are stripped first: this repo's own config carries the string
# `$proxy_add_x_forwarded_for` inside a comment explaining why it is NOT used,
# and matching that comment would report a problem that does not exist.
effective=$(nginx -T 2>/dev/null | sed 's/#.*//')
if grep -q 'proxy_add_x_forwarded_for' <<< "$effective"; then
  bad "a site appends X-Forwarded-For — the chat rate limit can be bypassed"
elif grep -q 'X-Forwarded-For.*remote_addr' <<< "$effective"; then
  ok "X-Forwarded-For is overwritten, not appended"
else
  bad "no X-Forwarded-For override found in the effective nginx config"
fi

echo
echo "== TLS =="
for d in inotex.padyar.com elecomp.padyar.com; do
  if [[ -f "/etc/letsencrypt/live/${d}/fullchain.pem" ]]; then
    exp=$(openssl x509 -enddate -noout -in "/etc/letsencrypt/live/${d}/fullchain.pem" | cut -d= -f2)
    ok "${d} certificate expires ${exp}"
  else
    bad "${d} has no certificate"
  fi
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 --resolve "${d}:443:127.0.0.1" "https://${d}/api/health")
  [[ "$code" == "200" ]] && ok "${d} serves HTTPS locally (${code})" || bad "${d} local HTTPS returned ${code}"
done

echo
echo "== upload limit =="
size=$(nginx -T 2>/dev/null | grep -m1 'client_max_body_size' | awk '{print $2}' | tr -d ';')
if [[ -n "$size" ]]; then
  ok "client_max_body_size = ${size} (video upload will not 413)"
else
  bad "client_max_body_size not set — video uploads will fail at 1 MB"
fi

echo
echo "== database =="
for slug in inotex elecomp; do
  cnt=$(sudo -u postgres psql -tAc "SELECT count(*) FROM pg_stat_activity WHERE datname='padyar_${slug}'" 2>/dev/null)
  [[ -n "$cnt" ]] && ok "padyar_${slug}: ${cnt} open connections" || bad "cannot query padyar_${slug}"
done
maxc=$(sudo -u postgres psql -tAc 'SHOW max_connections;' 2>/dev/null)
echo "       max_connections=${maxc}"

echo
echo "== TTS =="
body=$(curl -fsS --max-time 5 http://127.0.0.1:8003/health 2>/dev/null)
if [[ -z "$body" ]]; then
  bad "TTS not answering on 127.0.0.1:8003"
# Whitespace-tolerant: curl returns compact JSON ("model_loaded":true) while
# json.tool pretty-prints it ("model_loaded": true). Matching one spelling
# reported a healthy service as degraded.
elif echo "$body" | tr -d ' ' | grep -q '"model_loaded":true'; then
  ok "TTS model loaded: $(echo "$body" | tr -d '\n' | cut -c1-140)"
  t0=$(date +%s%N)
  curl -fsS --max-time 120 -X POST http://127.0.0.1:8003/tts \
    -H 'Content-Type: application/json' \
    -d '{"text":"سلام، به نمایشگاه خوش آمدید."}' -o /tmp/tts-smoke.wav
  t1=$(date +%s%N)
  if [[ -s /tmp/tts-smoke.wav ]]; then
    ok "generated $(du -h /tmp/tts-smoke.wav | cut -f1) in $(( (t1-t0)/1000000 )) ms"
  else
    bad "TTS returned no audio"
  fi
else
  bad "TTS degraded: $(echo "$body" | tr -d '\n' | cut -c1-200)"
fi
# The TTS port must never be reachable from outside.
if ss -ltn 2>/dev/null | grep -q '0.0.0.0:8003\|\*:8003'; then
  bad "port 8003 is bound to a public interface — it has no authentication"
else
  ok "TTS is loopback-only"
fi

echo
echo "== firewall =="
ufw status 2>/dev/null | grep -qi 'Status: active' && ok "ufw active" || warn "ufw not active"
for p in 5432 8001 8002 8003; do
  if ufw status 2>/dev/null | grep -q "^${p}.*ALLOW"; then bad "port ${p} is open to the world"; fi
done
ok "no internal port opened in ufw"

echo
[[ $fail -eq 0 ]] && echo "ALL CHECKS PASSED." || { echo "SOME CHECKS FAILED — see FAIL lines above."; exit 1; }
