#!/bin/bash
# Per-model bench steps for llama-server on one P40. Results go to
# /var/lib/padyar/llm/results/<alias>/. The API key is only ever read from
# 0600 files (api-key for the server, auth-header for curl); never printed.
#   llm-bench.sh start <alias> <gguf> [extra llama-server args...]  M1 + M3 before/after load
#   llm-bench.sh probe <alias> [json-extra]                        warm-up, M3 after TTS, M5
#   llm-bench.sh stop  <alias>                                     stop the server
#   llm-bench.sh bench <alias> <gguf> <gpu>                        M2 llama-bench (server stopped)
set -euo pipefail
L=/var/lib/padyar/llm; BIN=/opt/padyar-llm/llama/bin
export LD_LIBRARY_PATH=/opt/padyar-llm/llama/lib CUDA_DEVICE_ORDER=PCI_BUS_ID
PORT=8010; URL=http://127.0.0.1:$PORT
cmd=$1; alias=$2; extra=${EXTRA:-}; R=$L/results/$alias; mkdir -p "$R"
smi() { echo "## $1 $(date -Is)"; nvidia-smi --query-gpu=index,memory.used,memory.free,memory.total --format=csv,noheader; }
body() { printf "{\"model\":\"%s\",\"messages\":[%s],\"max_tokens\":%s%s}" "$alias" "$1" "$2" "$extra"; }
m5() {  # M5: the spike Gate 4 curl probe and the app health.py probe form
  { echo "## M5a spike Gate 4 probe (response_format json_object) $(date -Is)"
    curl -s $URL/v1/chat/completions -H @"$L/auth-header" -H "Content-Type: application/json" \
      -d "{\"model\":\"$alias\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with {\\\"ok\\\": true}\"}],\"response_format\":{\"type\":\"json_object\"},\"max_tokens\":64$extra}"
    echo; echo "## M5b app health.py probe form (system prompt + strict parse) $(date -Is)"
    curl -s $URL/v1/chat/completions -H @"$L/auth-header" -H "Content-Type: application/json" \
      -d "{\"model\":\"$alias\",\"messages\":[{\"role\":\"system\",\"content\":\"Answer with exactly one JSON object and nothing else.\"},{\"role\":\"user\",\"content\":\"Reply with {\\\"ok\\\": true}\"}],\"response_format\":{\"type\":\"json_object\"},\"max_tokens\":64,\"temperature\":0$extra}"
    echo; } > "$1"
}
case $cmd in
start)
  gguf=$3; shift 3
  { echo "## port check"; ss -ltnp | grep ":$PORT " && { echo "PORT $PORT BUSY"; exit 3; } || echo "port $PORT free"; } > "$R/m1-preflight.txt"
  smi before-load > "$R/m3-vram.txt"
  gpu=$(nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits | sort -t, -k2 -nr | head -1 | cut -d, -f1)
  echo "$gpu" > "$R/gpu"
  args=(-m "$L/models/$gguf" --alias "$alias" --host 127.0.0.1 --port $PORT
        --api-key-file "$L/api-key" -ngl 999 --split-mode layer -fa on
        -c 16384 -np 4 -ctk q8_0 -ctv q8_0 "$@")
  echo "CUDA_VISIBLE_DEVICES=$gpu CUDA_DEVICE_ORDER=PCI_BUS_ID LD_LIBRARY_PATH=/opt/padyar-llm/llama/lib $BIN/llama-server ${args[*]}" > "$R/m1-command.txt"
  t0=$(date +%s.%N)
  CUDA_VISIBLE_DEVICES=$gpu nohup "$BIN/llama-server" "${args[@]}" > "$R/server.log" 2>&1 < /dev/null &
  echo $! > "$R/server.pid"
  code=000
  for i in $(seq 1 1200); do
    code=$(curl -s -o /dev/null -w "%{http_code}" $URL/health || true)
    [ "$code" = 200 ] && break
    kill -0 "$(cat "$R/server.pid")" 2>/dev/null || { echo "server exited"; break; }
    sleep 0.5
  done
  t1=$(date +%s.%N)
  { echo "gpu=$gpu health=$code"; echo "load_seconds=$(echo "$t1 - $t0" | bc)"
    echo "## offload / backend lines from server.log"
    grep -vE "llama_model_loader: - (kv|type)|load: control-looking|print_info" "$R/server.log" | grep -iE "offload|mmq|cublas|flash_attn|n_ctx|KV buffer|buffer size|CUDA0|fit|failed|error" | head -60; } > "$R/m1-load.txt"
  smi after-load >> "$R/m3-vram.txt"
  cat "$R/m1-load.txt" | head -3
  ;;
probe)
  { echo "## warm-up $(date -Is)"
    curl -s $URL/v1/chat/completions -H @"$L/auth-header" -H "Content-Type: application/json" \
      -d "$(body "{\"role\":\"user\",\"content\":\"سلام\"}" 8)"; echo; } > "$R/warmup.txt"
  txt="این یک آزمون کوتاه برای سنجش حافظه است، شماره $(date +%s)"
  { echo "## tts POST $(date -Is)"; curl -s -o /dev/null -w "http=%{http_code} seconds=%{time_total} bytes=%{size_download}\n" \
      -X POST http://127.0.0.1:8003/tts -H "Content-Type: application/json" \
      -d "{\"text\":\"$txt\"}"; } > "$R/m3-tts.txt"
  smi after-tts >> "$R/m3-vram.txt"
  m5 "$R/m5-json-probe.txt"
  cat "$R/m3-vram.txt"
  ;;
stop)
  pid=$(cat "$R/server.pid"); kill "$pid" 2>/dev/null || true
  for i in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  smi after-stop >> "$R/m3-vram.txt"; echo stopped
  ;;
bench)
  gguf=$3; gpu=$4
  cmdline="CUDA_VISIBLE_DEVICES=$gpu $BIN/llama-bench -m $L/models/$gguf -ngl 999 -fa 1 -ctk q8_0 -ctv q8_0 -p 512 -n 128 -r 3 -o md"
  { echo "$cmdline"; smi before-bench
    CUDA_VISIBLE_DEVICES=$gpu "$BIN/llama-bench" -m "$L/models/$gguf" -ngl 999 -fa 1 -ctk q8_0 -ctv q8_0 -p 512 -n 128 -r 3 -o md 2>&1
    smi after-bench; } > "$R/m2-llama-bench.txt"
  tail -8 "$R/m2-llama-bench.txt"
  ;;
json)
  m5 "$R/m5-json-probe${3:-}.txt"; cat "$R/m5-json-probe${3:-}.txt" | wc -c
  ;;
esac
