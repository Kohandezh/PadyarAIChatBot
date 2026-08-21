#!/usr/bin/env bash
# Pre-render one install's dataset answers into the TTS cache.
#
#   sudo bash deploy/45-prerender.sh inotex
#   sudo bash deploy/45-prerender.sh elecomp --dry-run
#
# Runs the model in a STANDALONE process, not through the service: the same
# text renders at RTF ~5 here and ~16 through uvicorn on this host. The cache
# it writes is the one the service reads, so visitors get instant audio.
set -euo pipefail

SLUG="${1:-}"
shift || true
case "$SLUG" in
  inotex|elecomp) ;;
  *) echo "Usage: sudo bash $0 {inotex|elecomp} [--dry-run]" >&2; exit 1 ;;
esac
[[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }

DB="padyar_${SLUG}"
JSON="/tmp/${SLUG}_texts.json"

echo "==> Extracting answers from ${DB}"
sudo -u postgres psql -d "$DB" -tAc \
  "SELECT COALESCE(json_agg(text)::text,'[]') FROM app.dataset
   WHERE text IS NOT NULL AND text <> ''" > "$JSON"
chmod 644 "$JSON"
python3 -c "import json;d=json.load(open('$JSON'));print(f'    {len(d)} answers, {sum(len(t) for t in d)} chars')"

echo "==> Rendering"
# The service's own settings, so the cache keys match exactly. Without
# TTS_DEVICE the module default is cuda and this aborts on a CPU-only host.
set -a
# shellcheck disable=SC1091
[[ -f /etc/default/padyar-tts ]] && . /etc/default/padyar-tts
set +a
export TTS_DEVICE="${TTS_DEVICE:-cpu}"
export TTS_MODEL_DIR="${TTS_MODEL_DIR:-/var/lib/padyar/tts/models/chatterbox-mtl}"
export TTS_LANGUAGE="${TTS_LANGUAGE:-fa}"
export TTS_CPU_THREADS="${TTS_CPU_THREADS:-32}"
export OMP_NUM_THREADS="$TTS_CPU_THREADS" MKL_NUM_THREADS="$TTS_CPU_THREADS"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

cd /opt/padyar-tts
sudo -u padyar-tts \
  env TTS_DEVICE="$TTS_DEVICE" TTS_MODEL_DIR="$TTS_MODEL_DIR" \
      TTS_LANGUAGE="$TTS_LANGUAGE" TTS_CPU_THREADS="$TTS_CPU_THREADS" \
      OMP_NUM_THREADS="$OMP_NUM_THREADS" MKL_NUM_THREADS="$MKL_NUM_THREADS" \
      HF_HOME=/var/lib/padyar/tts/models/hf \
      HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  ./.venv/bin/python prerender_dataset.py "$JSON" "$@"
