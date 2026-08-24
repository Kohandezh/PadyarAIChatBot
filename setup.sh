#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  PadyarAIChatbot (INOTEX Assistant) — Interactive Installer
# ──────────────────────────────────────────────────────────────
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors (respect NO_COLOR) ──────────────────────────────────
setup_colors() {
  if [[ -t 2 ]] && [[ -z "${NO_COLOR-}" ]] && [[ "${TERM-}" != "dumb" ]]; then
    RED='\033[0;31m'    GREEN='\033[0;32m'   YELLOW='\033[0;33m'
    BLUE='\033[0;34m'   CYAN='\033[0;36m'    BOLD='\033[1m'
    DIM='\033[2m'       RESET='\033[0m'
  else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' DIM='' RESET=''
  fi
}

# ── Output helpers ─────────────────────────────────────────────
msg()    { echo -e "${BOLD}${*}${RESET}"; }
info()   { echo -e "${BLUE}ℹ${RESET}  ${*}"; }
ok()     { echo -e "${GREEN}✔${RESET}  ${*}"; }
warn()   { echo -e "${YELLOW}⚠${RESET}  ${*}"; }
err()    { echo -e "${RED}✖${RESET}  ${*}" >&2; }
die()    { err "${*}"; exit 1; }

step() {
  local n="$1"; shift
  echo ""
  echo -e "${CYAN}${BOLD}── Step ${n}: ${*} ──${RESET}"
}

banner() {
  echo ""
  echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
  echo -e "${CYAN}${BOLD}║       PadyarAIChatbot — INOTEX Assistant        ║${RESET}"
  echo -e "${CYAN}${BOLD}║             Interactive Installer                 ║${RESET}"
  echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
  echo ""
}

# ── Spinner ────────────────────────────────────────────────────
_spin_pid=""

spin_start() {
  local msg="${1:-Working...}"
  bash -c "
    spinner='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    while :; do
      for i in \$(seq 0 9); do
        printf \"\r  \${spinner:\$i:1} ${msg}\"
        sleep 0.1
      done
    done
  " &
  _spin_pid=$!
}

spin_stop() {
  if [[ -n "${_spin_pid}" ]]; then
    kill "$_spin_pid" 2>/dev/null || true
    wait "$_spin_pid" 2>/dev/null || true
    _spin_pid=""
    printf "\r%*s\r" 40 ""  # clear the spinner line
  fi
}

# ── Progress ───────────────────────────────────────────────────
progress() {
  local current="$1" total="$2" label="${3:-}"
  local width=40
  local pct=$(( current * 100 / total ))
  local filled=$(( current * width / total ))
  local empty=$(( width - filled ))
  printf "\r  ${DIM}[${RESET}"
  printf "%${filled}s" | tr ' ' '█'
  printf "%${empty}s" | tr ' ' '░'
  printf "${DIM}]${RESET} %3d%% %s" "$pct" "$label"
  if [[ "$current" -eq "$total" ]]; then echo ""; fi
}

# ── Validation helpers ─────────────────────────────────────────
validate_api_key() {
  local key="$1"
  [[ "$key" =~ ^sk-[A-Za-z0-9_-]+$ ]] || [[ ${#key} -ge 20 ]]
}

validate_url() {
  local url="$1"
  [[ "$url" =~ ^https?://[a-zA-Z0-9] ]]
}

# ── Cleanup trap ───────────────────────────────────────────────
cleanup() {
  spin_stop
  if [[ -n "${_tmp_env:-}" ]] && [[ -f "${_tmp_env}" ]]; then
    rm -f "${_tmp_env}"
  fi
}
trap cleanup SIGINT SIGTERM ERR EXIT

# ── Module definitions ─────────────────────────────────────────
declare -A MODULE_DESC=(
  [voice]="Voice input via Whisper API"
  [video]="Video response upload and serving"
)

MODULE_NAMES=("voice" "video")

# ── State ──────────────────────────────────────────────────────
INSTALL_TYPE=""
ENABLED_MODULES=()
OPENAI_API_KEY=""
VIDEO_BASE_URL="/media/videos"
VENV_DIR=".venv"

# ────────────────────────────────────────────────────────────────
# Main flow
# ────────────────────────────────────────────────────────────────
main() {
  setup_colors
  banner

  # ── Step 1: Install type ────────────────────────────────────
  step 1 "Installation Type"
  echo ""
  echo -e "  ${BOLD}1)${RESET} Full Install    — all features enabled"
  echo -e "  ${BOLD}2)${RESET} Custom Install  — choose which modules to enable"
  echo -e "  ${BOLD}3)${RESET} Exit"
  echo ""
  while true; do
    read -rp "  Enter your choice [1-3]: " choice
    case "$choice" in
      1) INSTALL_TYPE="full"; ENABLED_MODULES=("${MODULE_NAMES[@]}"); break ;;
      2) INSTALL_TYPE="custom"; break ;;
      3) msg "Goodbye!"; exit 0 ;;
      *) warn "Please enter 1, 2, or 3" ;;
    esac
  done

  # ── Step 2: Module selection (custom only) ──────────────────
  if [[ "$INSTALL_TYPE" == "custom" ]]; then
    step 2 "Module Selection"
    echo ""
    info "Core modules (chat, admin, search, dataset) are always enabled."
    info "Select optional modules to install:"
    echo ""

    ENABLED_MODULES=()
    for mod in "${MODULE_NAMES[@]}"; do
      while true; do
        read -rp "  Enable '${mod}' (${MODULE_DESC[$mod]})? [Y/n]: " yn
        case "${yn:-Y}" in
          [Yy]*) ENABLED_MODULES+=("$mod"); break ;;
          [Nn]*) break ;;
          *) warn "Please enter Y or N" ;;
        esac
      done
    done

    if [[ ${#ENABLED_MODULES[@]} -eq 0 ]]; then
      warn "No optional modules selected. Only core features will be installed."
    fi
  else
    step 2 "Module Selection"
    echo ""
    ok "All modules will be enabled."
  fi

  # ── Step 3: Prerequisites ───────────────────────────────────
  step 3 "Prerequisites"
  echo ""

  # Python 3.10+
  if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
    PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
    if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MINOR" -lt 10 ]]; then
      die "Python 3.10+ is required (found ${PY_VERSION})"
    fi
    ok "Python ${PY_VERSION} found"
  else
    die "Python 3 is not installed. Please install it first."
  fi

  # pip
  if python3 -m pip --version &>/dev/null; then
    ok "pip is available"
  else
    die "pip is not available. Install it with: python3 -m ensurepip"
  fi

  # venv module
  if python3 -c "import venv" &>/dev/null; then
    ok "venv module is available"
  else
    die "venv module is not available. Install it with: sudo apt install python3-venv (or equivalent)"
  fi

  # ── Step 4: Environment configuration ───────────────────────
  step 4 "Configuration"
  echo ""

  # Check for existing .env
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    info "Found existing .env file."
    read -rp "  Use existing configuration? [Y/n]: " use_existing
    case "${use_existing:-Y}" in
      [Yy]*)
        # Extract existing values
        OPENAI_API_KEY=$(grep -oP '^OPENAI_API_KEY=\K.*' "${SCRIPT_DIR}/.env" 2>/dev/null || echo "")
        VIDEO_BASE_URL=$(grep -oP '^VIDEO_BASE_URL=\K.*' "${SCRIPT_DIR}/.env" 2>/dev/null || echo "/media/videos")
        ok "Using existing configuration"
        ;;
      *)
        OPENAI_API_KEY=""
        VIDEO_BASE_URL=""
        ;;
    esac
  fi

  # API Key
  if [[ -z "$OPENAI_API_KEY" ]]; then
    echo ""
    info "You need an OpenAI API key (or GapGPT proxy key)."
    while true; do
      read -rp "  Enter your API key: " OPENAI_API_KEY
      if [[ -z "$OPENAI_API_KEY" ]]; then
        warn "API key cannot be empty."
        continue
      fi
      if validate_api_key "$OPENAI_API_KEY"; then
        ok "API key accepted"
        break
      else
        warn "Key format looks unusual. Continue anyway? [Y/n]"
        read -rp "  " confirm
        case "${confirm:-Y}" in
          [Yy]*) break ;;
        esac
      fi
    done
  fi

  # Video Base URL
  if [[ -z "$VIDEO_BASE_URL" ]]; then
    echo ""
    read -rp "  Video base URL [${VIDEO_BASE_URL}]: " input_url
    VIDEO_BASE_URL="${input_url:-$VIDEO_BASE_URL}"
  fi

  # ── Step 5: Create virtual environment ───────────────────────
  step 5 "Virtual Environment"
  echo ""

  if [[ -f "${SCRIPT_DIR}/${VENV_DIR}/bin/activate" ]]; then
    ok "Virtual environment already exists at ${VENV_DIR}/"
  else
    spin_start "Creating virtual environment..."
    python3 -m venv "${SCRIPT_DIR}/${VENV_DIR}" 2>/dev/null
    spin_stop
    ok "Virtual environment created at ${VENV_DIR}/"
  fi

  # Activate venv
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/${VENV_DIR}/bin/activate"
  ok "Virtual environment activated"

  # ── Step 6: Install dependencies ────────────────────────────
  step 6 "Dependencies"
  echo ""
  spin_start "Installing Python packages..."

  if python3 -m pip install -q -r "${SCRIPT_DIR}/requirements.txt" 2>/dev/null; then
    spin_stop
    ok "All dependencies installed successfully"
  else
    spin_stop
    warn "Some dependencies failed to install. Trying with --upgrade..."
    python3 -m pip install -q --upgrade -r "${SCRIPT_DIR}/requirements.txt" || {
      err "Failed to install dependencies."
      die "Please check your internet connection and try again."
    }
    ok "Dependencies installed (with upgrade)"
  fi

  # ── Step 7: Generate .env file ──────────────────────────────
  step 7 "Configuration File"
  echo ""

  # Build ENABLED_MODULES string
  modules_str=""
  for mod in "${ENABLED_MODULES[@]}"; do
    if [[ -n "$modules_str" ]]; then modules_str+=","; fi
    modules_str+="$mod"
  done

  _tmp_env=$(mktemp)
  {
    echo "# PadyarAIChatbot (INOTEX Assistant) Configuration"
    echo "# Generated by setup.sh on $(date '+%Y-%m-%d %H:%M')"
    echo ""
    echo "OPENAI_API_KEY=${OPENAI_API_KEY}"
    echo "VIDEO_BASE_URL=${VIDEO_BASE_URL}"
    echo "ENABLED_MODULES=${modules_str}"
  } > "${_tmp_env}"

  mv "${_tmp_env}" "${SCRIPT_DIR}/.env"
  chmod 600 "${SCRIPT_DIR}/.env"
  ok ".env file generated with your settings"

  # ── Step 8: Initialize database ─────────────────────────────
  step 8 "Database"
  echo ""

  spin_start "Initializing database..."
  cd "${SCRIPT_DIR}"
  python3 -c "
from app.db.connection import init_db
init_db()
print('OK')
" 2>/dev/null | tail -1 > /dev/null
  spin_stop
  ok "Database initialized"

  # ── Step 9: Knowledge graph (graphify) — developer tool, opt-in ──
  step 9 "Knowledge Graph (graphify) — optional dev tool"
  echo ""

  info "graphify maps the codebase into a queryable knowledge graph for AI"
  info "assistants (Claude Code / OpenCode). It is a DEVELOPER tool — a"
  info "production server does not need it. The graph itself (graphify-out/)"
  info "already arrives with git clone either way."
  read -rp "  Install graphify CLI + auto-rebuild git hooks on this machine? [y/N]: " want_graphify
  case "${want_graphify:-N}" in
    [Yy]*) ;;
    *) ok "Skipped graphify (dev tool) — production installs don't need it" ;;
  esac

  if [[ "${want_graphify:-N}" == [Yy]* ]]; then
  # Per machine we only need the CLI and the git hooks that rebuild the
  # graph automatically on commit/checkout.
  if command -v graphify &>/dev/null; then
    ok "graphify CLI already installed"
  elif command -v uv &>/dev/null; then
    info "Installing graphify CLI via uv (isolated tool env)..."
    if uv tool install "graphifyy[sql]" >/dev/null 2>&1; then
      case ":${PATH}:" in
        *":${HOME}/.local/bin:"*) ;;
        *) export PATH="${HOME}/.local/bin:${PATH}" ;;
      esac
      if command -v graphify &>/dev/null; then
        ok "graphify installed"
      else
        warn "graphify installed but not on PATH — run 'uv tool update-shell' and reopen your terminal"
      fi
    else
      warn "graphify CLI install failed — skipping (optional dev tool)"
    fi
  else
    warn "uv not found — skipping graphify CLI (optional dev tool; docs: https://github.com/Graphify-Labs/graphify)"
  fi

  if command -v graphify &>/dev/null; then
    if graphify hook install >/dev/null 2>&1; then
      ok "git hooks installed — knowledge graph auto-rebuilds on every commit/checkout"
    else
      warn "Could not install graphify git hooks"
    fi
    if [[ -f "${SCRIPT_DIR}/graphify-out/graph.json" ]]; then
      ok "Committed knowledge graph found at graphify-out/"
    else
      info "Building initial code graph (local AST parsing, no API key needed)..."
      if (cd "${SCRIPT_DIR}" && graphify extract . --code-only >/dev/null 2>&1); then
        ok "Knowledge graph built at graphify-out/"
      else
        warn "Graph build failed — run manually: graphify extract . --code-only"
      fi
    fi
  fi
  fi

  # ── Done ─────────────────────────────────────────────────────
  echo ""
  echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
  echo -e "${GREEN}${BOLD}║           ✓ Installation Complete!               ║${RESET}"
  echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
  echo ""
  msg "  Modules enabled:"
  echo -e "    ${DIM}core:${RESET} chat, admin, search, dataset"
  if [[ ${#ENABLED_MODULES[@]} -gt 0 ]]; then
    echo -e "    ${DIM}optional:${RESET} ${ENABLED_MODULES[*]}"
  fi
  echo ""
  msg "  To start the server:"
  echo ""
  echo -e "    ${CYAN}source .venv/bin/activate${RESET}"
  echo -e "    ${CYAN}python main.py${RESET}"
  echo ""
  msg "  The app will be available at: ${CYAN}http://127.0.0.1:8000${RESET}"
  echo ""
  msg "  To reconfigure, run: ${CYAN}./setup.sh${RESET}"
  echo ""
  msg "  Knowledge graph: ${DIM}graphify-out/ — rebuilds automatically on every git commit${RESET}"
  echo ""
}

main "$@"
