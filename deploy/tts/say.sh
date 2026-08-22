#!/usr/bin/env bash
# Speak a Persian sentence through the TTS service and play it locally.
#
#   ./say.sh "سلام، به نمایشگاه خوش آمدید"
#
# The service listens on the server's loopback only, so this sends the request
# over SSH rather than exposing the port. JSON is built with python so Persian
# text and quotes survive the trip intact.
set -euo pipefail

HOST="${PADYAR_HOST:-gpu@192.168.100.6}"
SOCK=/tmp/.pdyr-cm.sock
TEXT="$*"
[[ -n "$TEXT" ]] || { echo 'Usage: ./say.sh "متن فارسی"' >&2; exit 1; }

SSH=(ssh)
[[ -S "$SOCK" ]] && SSH=(ssh -S "$SOCK")

OUT="${TMPDIR:-/tmp}/padyar-tts-$$.mp3"
PAYLOAD=$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$TEXT")

echo "synthesising: $TEXT"
start=$(date +%s)
printf '%s' "$PAYLOAD" | "${SSH[@]}" "$HOST" \
  "curl -s --max-time 900 -X POST http://127.0.0.1:8003/tts \
     -H 'Content-Type: application/json' --data-binary @-" > "$OUT"
end=$(date +%s)

# The service answers with mp3 bytes or a JSON error object, so one character
# separates the two cases.
if [[ ! -s "$OUT" ]] || [[ "$(head -c 1 "$OUT")" == "{" ]]; then
  echo "FAILED, the service returned an error rather than audio:" >&2
  head -c 300 "$OUT" >&2; echo >&2
  exit 1
fi

bytes=$(wc -c < "$OUT")
# An mp3 has no fixed bytes per second, so ask ffprobe rather than divide.
secs=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" 2>/dev/null || echo "?")
echo "  audio: ${secs}s   ${bytes} bytes   took: $((end-start))s   file: $OUT"
command -v afplay >/dev/null && afplay "$OUT" || echo "  (play it with: open '$OUT')"
