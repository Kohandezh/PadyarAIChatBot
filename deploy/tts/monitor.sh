#!/usr/bin/env bash
# Sample the machine every INTERVAL seconds into a CSV, until stopped.
#   monitor.sh /tmp/metrics.csv 2
# CPU% is a delta between samples — a single /proc/stat read gives the average
# since boot, which on a long-lived server is flat and useless.
set -uo pipefail
OUT="${1:-/tmp/metrics.csv}"
INTERVAL="${2:-2}"

echo "ts,load1,cpu_pct,mem_used_mb,mem_total_mb,gpu0_pct,gpu0_mem_mb,gpu1_pct,gpu1_mem_mb,gpu_temp" > "$OUT"

read_cpu() { awk '/^cpu /{idle=$5+$6; total=0; for(i=2;i<=9;i++) total+=$i; print idle, total}' /proc/stat; }
read -r PREV_IDLE PREV_TOTAL < <(read_cpu)

while true; do
  sleep "$INTERVAL"
  read -r IDLE TOTAL < <(read_cpu)
  D_IDLE=$((IDLE - PREV_IDLE)); D_TOTAL=$((TOTAL - PREV_TOTAL))
  CPU=0
  [ "$D_TOTAL" -gt 0 ] && CPU=$(awk -v i="$D_IDLE" -v t="$D_TOTAL" 'BEGIN{printf "%.1f", (1-i/t)*100}')
  PREV_IDLE=$IDLE; PREV_TOTAL=$TOTAL

  LOAD=$(awk '{print $1}' /proc/loadavg)
  MEM=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf "%d,%d", (t-a)/1024, t/1024}' /proc/meminfo)

  GPU="0,0,0,0,0"
  if command -v nvidia-smi >/dev/null 2>&1; then
    GPU=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu \
          --format=csv,noheader,nounits 2>/dev/null | \
          awk -F', *' 'NR==1{u0=$1;m0=$2;t=$3} NR==2{u1=$1;m1=$2} END{printf "%s,%s,%s,%s,%s", u0,m0,(u1==""?0:u1),(m1==""?0:m1),t}')
  fi
  echo "$(date +%s),$LOAD,$CPU,$MEM,$GPU" >> "$OUT"
done
