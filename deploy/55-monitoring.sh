#!/usr/bin/env bash
# Install or update the host's monitoring stack: Prometheus, Alertmanager and
# three exporters from the Ubuntu noble archive, under systemd, every one of
# them on 127.0.0.1 only. Spec: docs/features/monitoring-stack/SPEC.md.
# Decision: ADR-024 in docs/engineering/DECISIONS.md.
#
#   sudo bash deploy/55-monitoring.sh <slug> [<slug>...] [--host-alerts <slug>]
#
# Run it after deploy/40-cloudflare-tunnel.sh. Each <slug> must already have
# METRICS_TOKEN in /opt/padyar-<slug>/.env and its app restarted since; the
# script stops with the one-line fix when it does not. Ports and domains are
# read from the host (the .env and the nginx site), never from arguments.
#
# Safe to re-run, and re-running is how changed rules reach the host:
#   * a re-run with fewer slugs deletes nothing of the other installs;
#   * passwords and token files are only created when missing;
#   * the tunnel is restarted only the first time (to add its metrics port).
#
# Exit codes: 0 done, 1 wrong usage or a failed precondition, 2 a monitoring
# port listening on a non-loopback address.
set -euo pipefail
# Run the last command of a pipe in this shell, not a subshell. Config files
# are written as `render ... | write_config <path>`, and write_config must
# record each path in CHANGED for the rollback to see it.
shopt -s lastpipe

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MON="${HERE}/monitoring"

SLUG_RE='^[a-z0-9][a-z0-9-]*$'
DOMAIN_RE='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
PORT_RE='^[0-9]+$'
TOKEN_RE='^[A-Za-z0-9_-]+$'
BUDGET_RE='^[0-9]*$'
# shellcheck disable=SC2016  # the $ signs are literal: a bcrypt hash
BCRYPT_RE='^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'

PROM_DIR=/etc/prometheus
SECRETS_DIR=/etc/prometheus/secrets
STATE_DIR=/etc/padyar-monitoring
INSTALLS_DIR=/etc/padyar-monitoring/installs
OWNER_FILE=/etc/padyar-monitoring/host-alerts-owner
WATCHDOG_PASS=/etc/padyar-monitoring/alertmanager-watchdog.pass
PROMETHEUS_PASS=/etc/prometheus/secrets/alertmanager-prometheus.pass
OPERATOR_PASS=/root/.secrets/alertmanager-operator.pass
WEB_CONFIG=/etc/prometheus/alertmanager-web.yml
AMTOOL_CONFIG=/root/.config/amtool/config.yml
CF_CONFIG=/etc/cloudflared/config.yml
SITES_DIR=/etc/nginx/sites-enabled
TSDB_DIR=/var/lib/prometheus
BACKUP_ROOT=/root/padyar-backups
LOCK_FILE=/run/padyar-monitoring.lock
ALERT_GROUP=padyar-alertread

METRICS_LINE='metrics: 127.0.0.1:20241'
LOOPBACK_PORTS=(9090 9093 9100 9115 9187 20241)
CLUSTER_PORT=9094
# Rule metrics that have no series until something happens (SPEC REQ-021).
WAITING_FOR_EVENT=(ai_circuit_state)

PACKAGES=(prometheus prometheus-alertmanager prometheus-node-exporter
          prometheus-postgres-exporter prometheus-blackbox-exporter)

# Upstream versions of the exporters (ADR-024). The metric allowlists in
# deploy/monitoring/exporter-metrics/ are named <exporter>-<version>.txt after
# these, and tests/test_monitoring_rules.py fails when the two drift.
# cloudflared is installed by 40-cloudflare-tunnel.sh, not pinned: its row is
# the tag whose source the allowlist was checked against.
EXPORTER_PACKAGES=(
  "node_exporter prometheus-node-exporter 1.7.0"
  "postgres_exporter prometheus-postgres-exporter 0.15.0"
  "blackbox_exporter prometheus-blackbox-exporter 0.24.0"
  "cloudflared cloudflared 2024.12.1"
)

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die() { echo "55-monitoring: $*" >&2; exit 1; }

# ── reading a .env without executing it (SPEC SEC-010) ──────────────────

# Print KEY's value from the dotenv FILE. Returns 0 when found (the value may
# be empty), 1 when the key is not in the file, 2 when a line cannot be read
# with certainty. On 2 the message names the key and the file, never the
# value.
#
# The app rewrites this file from admin-panel input
# (app/services/secure_store.py write_env_values), so nothing in it is ever
# run: no source, no eval, no expansion. Only lines of the exact form KEY=...
# count, one layer of matching quotes is removed, and anything a shell,
# systemd or python-dotenv could read differently (export, indentation,
# escapes, $, backticks, an inline comment) stops the script instead of
# being guessed at.
env_value() {
  local file="$1" key="$2" line value quote result="" found=0
  local unreadable="${key} in ${file} is written in a form this script does not read with certainty"
  if grep -qE "^[[:space:]]+(export[[:space:]]+)?${key}[[:space:]]*=|^export[[:space:]]+${key}[[:space:]]*=|^${key}[[:space:]]+=" "$file"; then
    echo "55-monitoring: ${unreadable} (export, indentation, or a space before '='). Write it as ${key}=<value>." >&2
    return 2
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == "${key}="* ]] || continue
    value="${line#"${key}="}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"* || "$value" == \'* ]]; then
      quote="${value:0:1}"
      if (( ${#value} < 2 )) || [[ "${value: -1}" != "$quote" ]]; then
        echo "55-monitoring: ${unreadable} (a quote is not closed)." >&2
        return 2
      fi
      value="${value:1:${#value}-2}"
      if [[ "$value" == *[\"\'\\\$\`]* ]]; then
        echo "55-monitoring: ${unreadable} (a quote, backslash, \$ or backtick inside the quotes)." >&2
        return 2
      fi
    elif [[ "$value" == *[[:space:]\"\'\\\$\`\#]* ]]; then
      echo "55-monitoring: ${unreadable} (an unquoted space, quote, backslash, \$, backtick or #)." >&2
      return 2
    fi
    if (( found )) && [[ "$value" != "$result" ]]; then
      echo "55-monitoring: ${key} is set more than once in ${file}, with different values. Keep one line." >&2
      return 2
    fi
    result="$value"
    found=1
  done < "$file"
  (( found )) || return 1
  printf '%s\n' "$result"
}

# Set INSTALL_PORT and INSTALL_TOKEN from one install's .env, or stop with the
# fix (REQ-003, REQ-004). The script never edits an app's .env itself: a new
# token only takes effect after the app restarts, and that is the operator's
# call.
read_install_env() {
  local slug="$1" env="$2" rc
  [[ -f "$env" ]] || die "${env} not found. Run deploy/10-install-app.sh ${slug} first."
  rc=0; INSTALL_PORT=$(env_value "$env" APP_PORT) || rc=$?
  case "$rc" in
    0) ;;
    1) die "APP_PORT is missing in ${env}." ;;
    *) exit 1 ;;
  esac
  [[ "$INSTALL_PORT" =~ $PORT_RE ]] || die "APP_PORT in ${env} is not a number."
  rc=0; INSTALL_TOKEN=$(env_value "$env" METRICS_TOKEN) || rc=$?
  case "$rc" in
    0) ;;
    1) die "METRICS_TOKEN is missing in ${env}. Add one and restart the app, then run this script again:
  echo \"METRICS_TOKEN=\$(openssl rand -hex 32)\" | sudo tee -a ${env} >/dev/null && sudo systemctl restart padyar-${slug}" ;;
    *) exit 1 ;;
  esac
  if [[ -z "$INSTALL_TOKEN" ]]; then
    die "METRICS_TOKEN is empty in ${env}. Fill it and restart the app, then run this script again:
  sudo sed -i \"s/^METRICS_TOKEN=.*/METRICS_TOKEN=\$(openssl rand -hex 32)/\" ${env} && sudo systemctl restart padyar-${slug}"
  fi
  [[ "$INSTALL_TOKEN" =~ $TOKEN_RE ]] \
    || die "METRICS_TOKEN in ${env} may only hold letters, digits, '_' and '-'. Replace it with: openssl rand -hex 32"
}

# Print the warnings for "a host alert cannot be texted" (REQ-023). Read
# from the host-alerts owner's .env, because when the database is down the
# app's settings() falls back to the environment (app/services/sms.py
# setting()). send_asanak needs username, password and source; it never reads
# ASANAK_API_KEY. Stops (exit 1) only when a value cannot be read at all.
sms_env_warnings() {
  local env="$1" key value rc missing="" password secret budget
  for key in ASANAK_USERNAME ASANAK_PASSWORD ASANAK_SOURCE; do
    rc=0; value=$(env_value "$env" "$key") || rc=$?
    (( rc == 2 )) && exit 1
    [[ -n "$value" ]] || missing+=" ${key}"
  done
  if [[ -n "$missing" ]]; then
    echo "Asanak credentials are only in the database (empty in ${env}:${missing}). While the database is down, no alert SMS goes out."
  fi
  rc=0; password=$(env_value "$env" ASANAK_PASSWORD) || rc=$?
  rc=0; secret=$(env_value "$env" SECRET_KEY) || rc=$?
  (( rc == 2 )) && exit 1
  if [[ "$password" == enc:* && -z "$secret" ]]; then
    echo "ASANAK_PASSWORD in ${env} is encrypted and SECRET_KEY is empty, so decrypting it needs the database. While the database is down, no alert SMS goes out."
  fi
  rc=0; budget=$(env_value "$env" SMS_DAILY_BUDGET) || rc=$?
  (( rc == 2 )) && exit 1
  if [[ ! "$budget" =~ $BUDGET_RE ]]; then
    # Warn only: daily_budget() in app/services/sms.py reads a value it
    # cannot parse as 0 (no cap). The value itself is never printed.
    echo "SMS_DAILY_BUDGET in ${env} is not a whole number; the app treats a value it cannot read as 0 (no daily cap). Set a whole number in the admin panel."
    return 0
  fi
  if [[ -n "$budget" ]] && (( 10#$budget > 0 )); then
    echo "SMS_DAILY_BUDGET is ${budget} in ${env}. With a budget, the counter is written to the database before each send, so while the database is down no alert SMS goes out (ADR-024, decision 7)."
  fi
}

# ── what the host already says ──────────────────────────────────────────

# Print the one domain of the nginx site that proxies to this install
# (REQ-010): the file in DIR with `upstream padyar_<slug>`, as rendered from
# deploy/nginx/instance.conf.template.
domain_for_slug() {
  local dir="$1" slug="$2" file domains
  local -a files=()
  for file in "$dir"/*; do
    [[ -f "$file" ]] || continue
    if grep -qE "^[[:space:]]*upstream[[:space:]]+padyar_${slug}[[:space:]]*\{" "$file"; then
      files+=("$file")
    fi
  done
  if (( ${#files[@]} != 1 )); then
    echo "55-monitoring: found ${#files[@]} nginx sites for ${slug} in ${dir}, expected exactly 1. Run deploy/15-nginx-and-ssl.sh for this install first." >&2
    return 1
  fi
  domains=$(sed -nE 's/^[[:space:]]*server_name[[:space:]]+([^;[:space:]]+)[[:space:]]*;.*/\1/p' "${files[0]}" | sort -u)
  if [[ ! "$domains" =~ $DOMAIN_RE ]]; then
    echo "55-monitoring: ${files[0]} does not name one domain in server_name. Run deploy/15-nginx-and-ssl.sh for this install first." >&2
    return 1
  fi
  printf '%s\n' "$domains"
}

# Print the slug that owns the host alerts (REQ-008). FILE is the owner file,
# FLAG the --host-alerts value (may be empty), FIRST the first slug argument,
# the rest every registered install. Without the flag an existing owner is
# kept, so a re-run with other slugs never moves it silently.
choose_host_owner() {
  local file="$1" flag="$2" first="$3" slug current
  shift 3
  if [[ -n "$flag" ]]; then
    for slug in "$@"; do
      if [[ "$slug" == "$flag" ]]; then printf '%s\n' "$flag"; return 0; fi
    done
    echo "55-monitoring: --host-alerts ${flag} is not one of the installs this script knows (${*})." >&2
    return 1
  fi
  if [[ -f "$file" ]]; then
    current=$(head -n 1 "$file")
    if [[ ! "$current" =~ $SLUG_RE ]]; then
      echo "55-monitoring: ${file} does not hold one install name. Fix it, or pass --host-alerts <slug>." >&2
      return 1
    fi
    printf '%s\n' "$current"
    return 0
  fi
  printf '%s\n' "$first"
}

# Print the TSDB size cap in MB: the smaller of 5 GB and 30% of (free space +
# the TSDB's current size). Returns 1 when that is under 1 GB (REQ-016).
# The TSDB's own data counts as space it may keep (a deliberate change from
# the SPEC's free-space-only formula, see docs/engineering/MONITORING.md):
# with free space alone, every re-run would lower the cap a little and
# Prometheus would delete history to meet it.
retention_size_mb() {
  local avail="$1" tsdb="$2" mb
  mb=$(( (avail + tsdb) * 3 / 10 / 1048576 ))
  (( mb > 5120 )) && mb=5120
  (( mb >= 1024 )) || return 1
  printf '%s\n' "$mb"
}

# Read `ss -ltnH` on stdin. Print every monitoring port bound to anything but
# 127.0.0.1, and anything on the Alertmanager cluster port. Returns 1 if any
# (REQ-020).
public_listeners() {
  local local_addr addr port wanted bad=0
  while read -r _ _ _ local_addr _; do
    [[ -n "$local_addr" ]] || continue
    port="${local_addr##*:}"
    addr="${local_addr%:*}"
    if [[ "$port" == "$CLUSTER_PORT" ]]; then
      echo "port ${port} (Alertmanager cluster gossip) is listening on ${addr}; it must be off"
      bad=1
      continue
    fi
    for wanted in "${LOOPBACK_PORTS[@]}"; do
      if [[ "$port" == "$wanted" && "$addr" != "127.0.0.1" ]]; then
        echo "port ${port} is listening on ${addr}, not only on 127.0.0.1"
        bad=1
      fi
    done
  done
  return "$bad"
}

# Print absent | ours | other for the tunnel config's top-level metrics line
# (REQ-024).
metrics_line_state() {
  local lines
  lines=$(grep -E '^metrics:' "$1" | sed -E 's/[[:space:]]+$//' || true)
  if [[ -z "$lines" ]]; then
    echo absent
  elif [[ "$lines" == "$METRICS_LINE" ]]; then
    echo ours
  else
    echo other
  fi
}

# Add the metrics line right after the top-level credentials-file line. One
# key, nothing else touched: no ingress rule of any install changes.
insert_metrics_line() {
  local file="$1" tmp
  if ! grep -qE '^credentials-file:' "$file"; then
    echo "55-monitoring: ${file} has no top-level credentials-file: line to put the metrics line after." >&2
    return 1
  fi
  tmp=$(mktemp "${file}.XXXXXX")
  awk -v line="$METRICS_LINE" '{ print } /^credentials-file:/ && !done { print line; done = 1 }' "$file" > "$tmp"
  chmod 0600 "$tmp"
  mv -f "$tmp" "$file"
}

# The upstream part of a Debian version: no epoch, no +ds, no revision.
upstream_version() {
  local v="$1"
  v="${v#*:}"
  v="${v%%-*}"
  v="${v%%+*}"
  v="${v%%~*}"
  printf '%s\n' "$v"
}

# Read `apt-cache policy prometheus` on stdin; succeed when the candidate is
# 2.45.x, the version ADR-024 was decided on (REQ-012).
apt_candidate_is_2_45() {
  local candidate
  candidate=$(sed -nE 's/^[[:space:]]*Candidate:[[:space:]]*//p' | head -n 1)
  [[ "$candidate" == 2.45.* ]]
}

# GET /metrics with the install's token (REQ-007). The header goes through a
# 0600 temp file and `curl -H @file`, so the token never appears in argv,
# where every user on the host could read it.
check_metrics_token() {
  local slug="$1" port="$2" token_file="$3" header code token=""
  IFS= read -r token < "$token_file" || true
  header=$(mktemp)
  chmod 0600 "$header"
  printf 'Authorization: Bearer %s\n' "$token" > "$header"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H @"$header" \
    "http://127.0.0.1:${port}/metrics") || true
  rm -f "$header"
  case "$code" in
    200) return 0 ;;
    403) die "padyar-${slug} refused the token (403): the app has not been restarted since METRICS_TOKEN was set. Run: sudo systemctl restart padyar-${slug}" ;;
    *) die "GET http://127.0.0.1:${port}/metrics for ${slug} answered HTTP ${code:-000}, expected 200. Check: sudo systemctl status padyar-${slug}" ;;
  esac
}

# ── rendering (REQ-032, REQ-033) ────────────────────────────────────────

render_scrape_file() {
  local slug="$1" port="$2"
  sed -e "s/{{SLUG}}/${slug}/g" -e "s/{{PORT}}/${port}/g" \
    "${MON}/templates/scrape-install.yml.template"
}

# Read every <slug>.conf in DIR into INSTALL_SLUGS / INSTALL_PORTS /
# INSTALL_DOMAINS. The files are written by this script, but they are read
# with the same no-execution rules as an app's .env.
read_install_records() {
  local dir="$1" conf slug port domain
  INSTALL_SLUGS=(); INSTALL_PORTS=(); INSTALL_DOMAINS=()
  for conf in "$dir"/*.conf; do
    [[ -f "$conf" ]] || continue
    slug="$(basename "$conf" .conf)"
    port=$(env_value "$conf" PORT) || die "cannot read PORT from ${conf}."
    domain=$(env_value "$conf" DOMAIN) || die "cannot read DOMAIN from ${conf}."
    [[ "$slug" =~ $SLUG_RE && "$port" =~ $PORT_RE && "$domain" =~ $DOMAIN_RE ]] \
      || die "${conf} does not hold a valid install record. Fix it or delete it."
    INSTALL_SLUGS+=("$slug"); INSTALL_PORTS+=("$port"); INSTALL_DOMAINS+=("$domain")
  done
}

render_blackbox_config() {
  local i
  read_install_records "$1"
  cat "${MON}/templates/blackbox.yml.template"
  for i in "${!INSTALL_SLUGS[@]}"; do
    sed -e "s/{{SLUG}}/${INSTALL_SLUGS[$i]}/g" -e "s/{{DOMAIN}}/${INSTALL_DOMAINS[$i]}/g" \
      "${MON}/templates/blackbox-module.yml.template"
  done
}

render_blackbox_job() {
  local i
  read_install_records "$1"
  cat "${MON}/templates/blackbox-origin.yml.template"
  for i in "${!INSTALL_SLUGS[@]}"; do
    sed -e "s/{{SLUG}}/${INSTALL_SLUGS[$i]}/g" -e "s/{{DOMAIN}}/${INSTALL_DOMAINS[$i]}/g" \
      "${MON}/templates/blackbox-origin-target.yml.template"
  done
}

# ── writing files ───────────────────────────────────────────────────────

# Write stdin to PATH through a temp file in the same directory and mv, so a
# reader never sees half a file. Used for secrets, which are never rolled back.
write_file() {
  local path="$1" owner="$2" group="$3" mode="$4" tmp
  tmp=$(mktemp "${path}.XXXXXX")
  cat > "$tmp"
  chown "${owner}:${group}" "$tmp"
  chmod "$mode" "$tmp"
  mv -f "$tmp" "$path"
}

# Same, for configuration: the previous version is kept under RUN_BACKUP
# first, so a failed check can put every file back (REQ-019).
CHANGED=()
write_config() {
  local path="$1"
  if [[ -e "$path" && ! -e "${RUN_BACKUP}${path}" ]]; then
    install -d -m 0700 "$(dirname "${RUN_BACKUP}${path}")"
    cp -p "$path" "${RUN_BACKUP}${path}"
  fi
  CHANGED+=("$path")
  write_file "$@"
}

restore_configs() {
  local path
  for path in "${CHANGED[@]}"; do
    if [[ -e "${RUN_BACKUP}${path}" ]]; then
      cp -p "${RUN_BACKUP}${path}" "$path"
    else
      rm -f "$path"
    fi
  done
}

# Print each package dpkg does not list as installed.
packages_to_install() {
  local package
  for package in "$@"; do
    if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null)" != "install ok installed" ]]; then
      printf '%s\n' "$package"
    fi
  done
}

# Stop and disable the services of the packages THIS run installed (each unit
# is named after its package). apt-get starts them at once on the Debian
# defaults: every interface, Alertmanager without auth, cluster gossip on
# 0.0.0.0:9094. UFW covers that only for the seconds until this script
# restarts them with its own config (SEC-001). A run that stops before then
# must not leave them up. Disabled too, so a reboot does not bring them back.
# A package the run did not get to install is skipped, and packages that were
# installed before the run are never passed in.
stop_new_services() {
  local unit
  local -a stopped=()
  for unit in "$@"; do
    [[ -z "$(packages_to_install "$unit")" ]] || continue
    systemctl stop "$unit" || true
    systemctl disable "$unit" || true
    stopped+=("$unit")
  done
  (( ${#stopped[@]} )) || return 0
  echo "55-monitoring: stopped and disabled the services this run installed: ${stopped[*]}. They stay off until a run of this script succeeds." >&2
}

# EXIT trap, set before apt-get runs, for everything up to a passing check.
# Whatever stops the run there (a failed precondition after the install, a
# failed token check, render, hash or promtool check) leaves the previous
# config files in place, no service restarted with new ones, and no service
# this run installed still running on the Debian defaults.
CONFIG_ACCEPTED=0
NEW_PACKAGES=()
RUN_BACKUP=""
rollback_unless_accepted() {
  local rc=$?
  (( rc != 0 && ! CONFIG_ACCEPTED )) || return "$rc"
  if (( ${#CHANGED[@]} )); then
    restore_configs
    echo "55-monitoring: stopped before any restart; the previous configuration files are back in place." >&2
  fi
  if (( ${#NEW_PACKAGES[@]} )); then
    stop_new_services "${NEW_PACKAGES[@]}"
  fi
  return "$rc"
}

# Create a 48-hex-character password file only when it is missing, so a
# re-run never changes a password. No trailing newline: every reader then
# sees the same bytes, trimmed or not.
ensure_password_file() {
  local path="$1" group="$2" mode="$3"
  if [[ ! -s "$path" ]]; then
    openssl rand -hex 24 | tr -d '\n' | write_file "$path" root "$group" "$mode"
  fi
  chown "root:${group}" "$path"
  chmod "$mode" "$path"
}

# Print the bcrypt hash for USER's password file. An existing hash in the web
# config is kept when it still matches, so the file does not change on every
# run. The hash is made by the first install's venv (it has bcrypt, the
# library the app uses for admin passwords), run as that install's user,
# never as root, with the password on stdin, never in argv.
bcrypt_hash() {
  local user="$1" pass_file="$2" old=""
  if [[ -f "$WEB_CONFIG" ]]; then
    old=$(sed -nE "s/^  ${user}: (.+)$/\\1/p" "$WEB_CONFIG" | head -n 1)
    [[ "$old" =~ $BCRYPT_RE ]] || old=""
  fi
  { printf '%s\n' "$old"; cat "$pass_file"; } | sudo -u "padyar-${HASH_SLUG}" "$HASH_PY" -c '
import sys, bcrypt
old, password = sys.stdin.buffer.read().split(b"\n", 1)
if old and bcrypt.checkpw(password, old):
    print(old.decode())
else:
    print(bcrypt.hashpw(password, bcrypt.gensalt()).decode())
'
}

# ── main ────────────────────────────────────────────────────────────────

usage() { echo "Usage: sudo bash $0 <slug> [<slug>...] [--host-alerts <slug>]" >&2; exit 1; }

main() {
  if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0 <slug> [<slug>...] [--host-alerts <slug>]" >&2; exit 1
  fi

  local -a slugs=()
  local host_flag="" arg
  while (( $# )); do
    arg="$1"; shift
    case "$arg" in
      --host-alerts)
        (( $# )) || usage
        host_flag="$1"; shift
        [[ "$host_flag" =~ $SLUG_RE ]] || usage ;;
      *)
        [[ "$arg" =~ $SLUG_RE ]] || usage
        [[ " ${slugs[*]} " == *" ${arg} "* ]] || slugs+=("$arg") ;;
    esac
  done
  (( ${#slugs[@]} )) || usage

  cd /
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "Another run of 55-monitoring.sh is in progress. Wait for it to finish."

  # ── 1. Preconditions. Nothing is installed or written before all of them pass.
  log "Checking the preconditions"
  local codename
  codename=$(sed -nE 's/^VERSION_CODENAME="?([a-z]+)"?$/\1/p' /etc/os-release)
  [[ "$codename" == noble ]] || die "this host is '${codename}', not Ubuntu 24.04 (noble). ADR-024 was decided on the noble packages."

  [[ -f "$CF_CONFIG" ]] || die "${CF_CONFIG} not found. Run deploy/40-cloudflare-tunnel.sh first."
  local metrics_state
  metrics_state=$(metrics_line_state "$CF_CONFIG")
  [[ "$metrics_state" != other ]] \
    || die "${CF_CONFIG} already has a metrics: line with another address. This script does not overwrite it. Change it to '${METRICS_LINE}' by hand, or free that port."

  # The packages start their services on all interfaces the moment they are
  # installed, before this script writes 127.0.0.1. UFW (22, 80, 443 only,
  # deploy/00-bootstrap-server.sh) is what covers those few seconds.
  ufw status 2>/dev/null | head -n 1 | grep -qx 'Status: active' \
    || die "UFW is not active. Run deploy/00-bootstrap-server.sh (or: sudo ufw --force enable) first."

  local slug env domain
  local -A ports=() tokens=() domains=()
  for slug in "${slugs[@]}"; do
    id -u "padyar-${slug}" >/dev/null 2>&1 || die "user padyar-${slug} does not exist. Run deploy/00-bootstrap-server.sh ${slug} first."
    env="/opt/padyar-${slug}/.env"
    read_install_env "$slug" "$env"
    ports[$slug]="$INSTALL_PORT"
    tokens[$slug]="$INSTALL_TOKEN"
    domain=$(domain_for_slug "$SITES_DIR" "$slug") || exit 1
    domains[$slug]="$domain"
  done

  local -a registered=("${slugs[@]}")
  read_install_records "$INSTALLS_DIR"
  for slug in "${INSTALL_SLUGS[@]}"; do
    [[ " ${registered[*]} " == *" ${slug} "* ]] || registered+=("$slug")
  done
  local owner
  owner=$(choose_host_owner "$OWNER_FILE" "$host_flag" "${slugs[0]}" "${registered[@]}") || exit 1
  [[ -f "/opt/padyar-${owner}/.env" ]] || die "the host-alerts owner ${owner} has no /opt/padyar-${owner}/.env."
  local sms_warnings
  sms_warnings=$(sms_env_warnings "/opt/padyar-${owner}/.env") || exit 1

  apt-get update -qq
  apt-cache policy prometheus | apt_candidate_is_2_45 \
    || die "the prometheus package from apt is not available as 2.45.x. See ADR-024 section 7 (falling back to option c)."

  local data_path="$TSDB_DIR" avail tsdb=0 retention_mb
  [[ -d "$data_path" ]] || data_path=/var/lib
  avail=$(df --output=avail -B1 "$data_path" | tail -n 1 | tr -d ' ')
  [[ -d "$TSDB_DIR" ]] && tsdb=$(du -sb "$TSDB_DIR" | cut -f1)
  retention_mb=$(retention_size_mb "$avail" "$tsdb") \
    || die "30% of the free space on ${data_path} (plus the current TSDB) is under 1 GB. Free disk space first (SPEC Q5)."

  HASH_SLUG="${slugs[0]}"
  HASH_PY="/opt/padyar-${HASH_SLUG}/.venv/bin/python"
  sudo -u "padyar-${HASH_SLUG}" "$HASH_PY" -c 'import bcrypt' 2>/dev/null \
    || die "${HASH_PY} cannot import bcrypt. Run deploy/10-install-app.sh ${HASH_SLUG} first."

  # ── 2. Packages. Record what is new first: if the run stops before the
  # restart, the trap stops exactly those services and nothing else.
  log "Installing the monitoring packages from the Ubuntu archive"
  mapfile -t NEW_PACKAGES < <(packages_to_install "${PACKAGES[@]}")
  trap rollback_unless_accepted EXIT
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${PACKAGES[@]}"
  local row exporter package expected installed
  for row in "${EXPORTER_PACKAGES[@]}"; do
    read -r exporter package expected <<< "$row"
    if [[ "$package" == cloudflared ]]; then
      installed=$(cloudflared --version 2>/dev/null | sed -nE 's/^cloudflared version ([0-9.]+).*/\1/p')
    else
      installed=$(upstream_version "$(dpkg-query -W -f='${Version}' "$package")")
    fi
    if [[ "$installed" != "$expected" ]]; then
      warn "${package} is ${installed:-unknown}, but deploy/monitoring/exporter-metrics/${exporter}-${expected}.txt was checked against ${expected}. Check that the metric names still exist; the name check at the end lists any that are missing."
    fi
  done

  # ── 3. PostgreSQL exporter role: pg_monitor only, over the local socket
  # with peer auth, so no database password exists on disk (SEC-008).
  log "Creating the read-only PostgreSQL role for the exporter"
  if ! sudo -u postgres psql -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'prometheus'" | grep -qx 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -qc 'CREATE ROLE prometheus LOGIN'
  fi
  sudo -u postgres psql -v ON_ERROR_STOP=1 -qc 'GRANT pg_monitor TO prometheus'
  sudo -u prometheus psql -h /var/run/postgresql -d postgres -c 'select 1' >/dev/null 2>&1 \
    || die "user prometheus cannot connect to PostgreSQL over the local socket. The 'local' line in pg_hba.conf is not 'peer' (SPEC Q8)."

  # ── 4. Secrets, users and groups (REQ-006, REQ-037). Created only when
  # missing; nothing here is printed.
  log "Writing the token files and the Alertmanager passwords"
  install -d -o root -g root -m 0755 "$STATE_DIR" "$INSTALLS_DIR"
  install -d -o root -g prometheus -m 0750 "$SECRETS_DIR"
  install -d -o root -g root -m 0700 /root/.secrets /root/.config /root/.config/amtool "$BACKUP_ROOT"
  getent group "$ALERT_GROUP" >/dev/null || groupadd --system "$ALERT_GROUP"
  for slug in "${registered[@]}"; do
    id -u "padyar-${slug}" >/dev/null 2>&1 || die "user padyar-${slug} does not exist."
    usermod -aG "$ALERT_GROUP" "padyar-${slug}"
  done

  local token_file current
  for slug in "${slugs[@]}"; do
    token_file="${SECRETS_DIR}/${slug}.token"
    current=""
    [[ -f "$token_file" ]] && IFS= read -r current < "$token_file" || true
    if [[ "$current" != "${tokens[$slug]}" ]]; then
      printf '%s' "${tokens[$slug]}" | write_file "$token_file" root prometheus 0640
    fi
    chown root:prometheus "$token_file"
    chmod 0640 "$token_file"
  done

  ensure_password_file "$PROMETHEUS_PASS" prometheus 0640
  ensure_password_file "$WATCHDOG_PASS" "$ALERT_GROUP" 0640
  ensure_password_file "$OPERATOR_PASS" root 0600

  log "Checking each install's token against its running app"
  for slug in "${slugs[@]}"; do
    check_metrics_token "$slug" "${ports[$slug]}" "${SECRETS_DIR}/${slug}.token"
    echo "  ${slug}: /metrics answers 200 with the token file"
  done

  # ── 5. Configuration. Every file below is kept in RUN_BACKUP first and
  # put back if a check fails.
  log "Writing the monitoring configuration"
  RUN_BACKUP="${BACKUP_ROOT}/monitoring-$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -m 0700 "$RUN_BACKUP"
  install -d -o root -g root -m 0755 "${PROM_DIR}/rules" "${PROM_DIR}/scrape.d" "${PROM_DIR}/tests"

  for slug in "${slugs[@]}"; do
    printf 'PORT=%s\nDOMAIN=%s\n' "${ports[$slug]}" "${domains[$slug]}" \
      | write_config "${INSTALLS_DIR}/${slug}.conf" root root 0644
    render_scrape_file "$slug" "${ports[$slug]}" \
      | write_config "${PROM_DIR}/scrape.d/padyar-${slug}.yml" root root 0644
  done
  if [[ -n "$host_flag" || ! -f "$OWNER_FILE" ]]; then
    printf '%s\n' "$owner" | write_config "$OWNER_FILE" root root 0644
  fi

  # Shared files come from EVERY registered install, not only this run's
  # slugs, so a re-run with fewer slugs keeps the others' probes (REQ-009).
  local rendered
  rendered=$(render_blackbox_config "$INSTALLS_DIR") || exit 1
  printf '%s\n' "$rendered" | write_config "${PROM_DIR}/blackbox.yml" root root 0644
  rendered=$(render_blackbox_job "$INSTALLS_DIR") || exit 1
  printf '%s\n' "$rendered" | write_config "${PROM_DIR}/scrape.d/blackbox-origin.yml" root root 0644
  if grep -qF '{{' "${PROM_DIR}/blackbox.yml" "${PROM_DIR}"/scrape.d/*.yml; then
    die "unfilled placeholder left in a rendered file. Template/renderer drift."
  fi

  write_config "${PROM_DIR}/prometheus.yml" root root 0644 < "${MON}/prometheus.yml"
  write_config "${PROM_DIR}/rules/padyar.rules.yml" root root 0644 < "${MON}/rules/padyar.rules.yml"
  write_config "${PROM_DIR}/tests/padyar_rules_test.yml" root root 0644 < "${MON}/tests/padyar_rules_test.yml"
  write_config "${PROM_DIR}/alertmanager.yml" root root 0644 < "${MON}/alertmanager.yml"

  # One assignment per hash: a failed hash then stops the run (set -e)
  # instead of leaving an empty password in the file.
  local hash_prometheus hash_watchdog hash_operator h
  hash_prometheus=$(bcrypt_hash prometheus "$PROMETHEUS_PASS")
  hash_watchdog=$(bcrypt_hash watchdog "$WATCHDOG_PASS")
  hash_operator=$(bcrypt_hash operator "$OPERATOR_PASS")
  for h in "$hash_prometheus" "$hash_watchdog" "$hash_operator"; do
    [[ "$h" =~ $BCRYPT_RE ]] || die "${HASH_PY} did not return a bcrypt hash."
  done
  printf '# Written by deploy/55-monitoring.sh. bcrypt hashes of the three API users.\nbasic_auth_users:\n  prometheus: %s\n  watchdog: %s\n  operator: %s\n' \
    "$hash_prometheus" "$hash_watchdog" "$hash_operator" | write_config "$WEB_CONFIG" root prometheus 0640

  # The packages read their flags from /etc/default/<package> ($ARGS). The
  # Debian builds already default the config and data paths.
  local header='# Written by deploy/55-monitoring.sh; re-running it rewrites this file.'
  printf '%s\nARGS="--web.listen-address=127.0.0.1:9090 --storage.tsdb.retention.time=30d --storage.tsdb.retention.size=%sMB"\n' \
    "$header" "$retention_mb" | write_config /etc/default/prometheus root root 0644
  # --cluster.listen-address= (empty) turns off HA gossip, which otherwise
  # listens on 0.0.0.0:9094.
  printf '%s\nARGS="--web.listen-address=127.0.0.1:9093 --cluster.listen-address= --web.config.file=%s"\n' \
    "$header" "$WEB_CONFIG" | write_config /etc/default/prometheus-alertmanager root root 0644
  printf '%s\nARGS="--web.listen-address=127.0.0.1:9100"\n' \
    "$header" | write_config /etc/default/prometheus-node-exporter root root 0644
  printf '%s\nDATA_SOURCE_NAME="user=prometheus host=/var/run/postgresql dbname=postgres sslmode=disable"\nARGS="--web.listen-address=127.0.0.1:9187"\n' \
    "$header" | write_config /etc/default/prometheus-postgres-exporter root prometheus 0640
  printf '%s\nARGS="--web.listen-address=127.0.0.1:9115 --config.file=%s"\n' \
    "$header" "${PROM_DIR}/blackbox.yml" | write_config /etc/default/prometheus-blackbox-exporter root root 0644

  # Memory caps are estimates, not measurements (SPEC REQ-017, spike D5).
  local unit limit
  for row in "prometheus 1G" "prometheus-alertmanager 256M" "prometheus-node-exporter 128M" \
             "prometheus-postgres-exporter 128M" "prometheus-blackbox-exporter 128M"; do
    read -r unit limit <<< "$row"
    install -d -m 0755 "/etc/systemd/system/${unit}.service.d"
    printf '# Written by deploy/55-monitoring.sh. An estimate, reviewed after 30 days of data.\n[Service]\nMemoryMax=%s\n' \
      "$limit" | write_config "/etc/systemd/system/${unit}.service.d/padyar-limits.conf" root root 0644
  done

  printf 'alertmanager.url: http://operator:%s@127.0.0.1:9093\n' "$(cat "$OPERATOR_PASS")" \
    | write_file "$AMTOOL_CONFIG" root root 0600

  # ── 6. Check before any restart (REQ-019).
  log "Checking the configuration before any restart"
  local ok=1
  promtool check config "${PROM_DIR}/prometheus.yml" || ok=0
  promtool check rules "${PROM_DIR}"/rules/*.yml || ok=0
  promtool test rules "${PROM_DIR}/tests/padyar_rules_test.yml" || ok=0
  promtool check web-config "$WEB_CONFIG" || ok=0
  amtool check-config "${PROM_DIR}/alertmanager.yml" || ok=0
  (( ok )) || die "a check failed (output above). No service was restarted with the new files."
  CONFIG_ACCEPTED=1

  # ── 7. Restart.
  log "Restarting the monitoring services"
  systemctl daemon-reload
  systemctl enable "${PACKAGES[@]}"
  systemctl restart "${PACKAGES[@]}"

  if [[ "$metrics_state" == absent ]]; then
    log "Adding the metrics port to the tunnel config"
    local cf_backup
    cf_backup="${BACKUP_ROOT}/cloudflared-config.yml.$(date -u +%Y%m%dT%H%M%SZ)"
    cp -p "$CF_CONFIG" "$cf_backup"
    insert_metrics_line "$CF_CONFIG" || exit 1
    if ! cloudflared --config "$CF_CONFIG" ingress validate; then
      cp -p "$cf_backup" "$CF_CONFIG"
      die "cloudflared rejected the config with the metrics line. The original is back (${cf_backup})."
    fi
    warn "restarting cloudflared now: the tunnel of EVERY install on this host drops for a few seconds. This only happens on the first run."
    systemctl restart cloudflared
  fi

  # ── 8. Nothing may listen beyond loopback (REQ-020).
  log "Checking that every monitoring port listens on 127.0.0.1 only"
  local port listeners violations
  for _ in $(seq 1 30); do
    listeners=$(ss -ltnH)
    local all_up=1
    for port in "${LOOPBACK_PORTS[@]}"; do
      grep -qE ":${port}[[:space:]]" <<< "$listeners" || all_up=0
    done
    (( all_up )) && break
    sleep 1
  done
  for port in "${LOOPBACK_PORTS[@]}"; do
    grep -qE ":${port}[[:space:]]" <<< "$listeners" || warn "nothing listens on port ${port} yet. Check its service with systemctl status."
  done
  if ! violations=$(public_listeners <<< "$listeners"); then
    echo "$violations" >&2
    echo "55-monitoring: a monitoring port is reachable beyond this host. Stop that service and fix its /etc/default file." >&2
    exit 2
  fi
  echo "  every monitoring port is on 127.0.0.1, and nothing listens on ${CLUSTER_PORT}"

  # ── 9. Live checks. They print, they never fail the run.
  log "Waiting 60 seconds for the first scrapes, then listing rule metrics with no series"
  sleep 60
  local rules_json names_json missing name
  rules_json=$(mktemp); names_json=$(mktemp)
  if curl -sf --max-time 10 -o "$rules_json" http://127.0.0.1:9090/api/v1/rules \
     && curl -sf --max-time 10 -o "$names_json" http://127.0.0.1:9090/api/v1/label/__name__/values; then
    missing=$(python3 "${MON}/promql_names.py" "$rules_json" "$names_json") || missing="(name check failed)"
    if [[ -z "$missing" ]]; then
      echo "  every metric the rules read has a series"
    fi
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      if [[ " ${WAITING_FOR_EVENT[*]} " == *" ${name} "* ]]; then
        echo "  waiting for an event: ${name} (no series until it first changes)"
      else
        warn "no series for ${name}: a rule that reads it cannot fire."
      fi
    done <<< "$missing"
  else
    warn "Prometheus did not answer on 127.0.0.1:9090. Check: sudo systemctl status prometheus"
  fi
  rm -f "$rules_json" "$names_json"

  read_install_records "$INSTALLS_DIR"
  local i code line
  for i in "${!INSTALL_SLUGS[@]}"; do
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
      --resolve "${INSTALL_DOMAINS[$i]}:443:127.0.0.1" "https://${INSTALL_DOMAINS[$i]}/metrics") || true
    if [[ "$code" != 404 ]]; then
      warn "https://${INSTALL_DOMAINS[$i]}/metrics answers ${code:-000} through nginx, not 404: /metrics is reachable from the internet. Re-render the site:
  sudo MAINTENANCE_TITLE='<install name for visitors>' bash deploy/17-watchdog.sh ${INSTALL_SLUGS[$i]} ${INSTALL_PORTS[$i]} ${INSTALL_DOMAINS[$i]}
  (MAINTENANCE_TITLE is required: without it the visitors' maintenance page goes back to the default title.)"
    fi
  done

  if [[ -n "$sms_warnings" ]]; then
    while IFS= read -r line; do warn "$line"; done <<< "$sms_warnings"
  fi

  # ── 10. Summary (REQ-025).
  local host
  host=$(hostname -I 2>/dev/null | awk '{print $1}')
  {
    echo
    echo "------------------------------------------------------------"
    echo " MONITORING IS INSTALLED"
    echo "------------------------------------------------------------"
    for package in "${PACKAGES[@]}"; do
      printf '  %-30s %s\n' "$package" "$(dpkg-query -W -f='${Version}' "$package")"
    done
    printf '  %-30s %s\n' cloudflared "$(cloudflared --version 2>/dev/null | head -n 1)"
    echo
    echo " Installs:           ${registered[*]}"
    echo " Host alerts owner:  ${owner} (texts disk, database, tunnel, certificate, site)"
    echo " TSDB retention:     30 days or ${retention_mb} MB, whichever comes first"
    echo " Config backups:     ${RUN_BACKUP}"
    echo
    echo " Open the UIs from your own machine through SSH, never a public URL:"
    echo "   ssh -N -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 ${SUDO_USER:-gpu}@${host:-<host>}"
    echo "   then http://127.0.0.1:9090 (Prometheus) and http://127.0.0.1:9093 (Alertmanager,"
    echo "   user operator, password in ${OPERATOR_PASS})."
    echo " Silence alerts before maintenance:  sudo amtool silence add alertname=<name> install=<slug> --duration=2h --comment='...'"
    echo "------------------------------------------------------------"
  }
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
