#!/usr/bin/env bash
#
# run.sh — Start both the Flask backend and the React frontend together.
#
# Usage:
#   ./run.sh                 Start both services
#   ./run.sh --no-install    Skip dependency installation
#   ./run.sh --help          Show this help
#
# Press Ctrl+C to stop both services cleanly.

set -euo pipefail

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/flask-server"
FRONTEND_DIR="$SCRIPT_DIR/customer_retirement_planner"

BACKEND_HOST="${FLASK_HOST:-127.0.0.1}"
BACKEND_PORT="${FLASK_PORT:-5000}"

INSTALL_DEPS=true

# --------------------------------------------------------------------------- #
# Colours
# --------------------------------------------------------------------------- #
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

log()   { echo -e "${CYAN}[run]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
err()   { echo -e "${RED}[err]${NC} $*" >&2; }

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
for arg in "$@"; do
  case "$arg" in
    --no-install) INSTALL_DEPS=false ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^#//' | sed '1d'
      exit 0
      ;;
    *)
      err "Unknown argument: $arg (use --help)"
      exit 1
      ;;
  esac
done

# --------------------------------------------------------------------------- #
# Process tracking & cleanup
# --------------------------------------------------------------------------- #
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo
  log "Shutting down..."
  if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
    # Kill the whole process group of the frontend (react-scripts spawns children).
    pkill -P "$FRONTEND_PID" 2>/dev/null || true
    ok "Frontend stopped."
  fi
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    pkill -P "$BACKEND_PID" 2>/dev/null || true
    ok "Backend stopped."
  fi
  wait 2>/dev/null || true
  log "Done."
}
trap cleanup INT TERM EXIT

# --------------------------------------------------------------------------- #
# Pre-flight checks
# --------------------------------------------------------------------------- #
command -v node >/dev/null 2>&1 || { err "node is not installed."; exit 1; }
command -v npm  >/dev/null 2>&1 || { err "npm is not installed.";  exit 1; }

# Pick a TensorFlow-compatible Python interpreter.
#
# TensorFlow (a backend dependency via keras) does not publish wheels for the
# very latest Python releases right away.  As of this writing it supports
# CPython 3.9 - 3.12, so we must avoid defaulting to a newer interpreter
# (e.g. 3.13 / 3.14) that would make `pip install tensorflow` fail.
#
# Strategy: look for an explicitly-versioned interpreter in the supported
# range (preferring newer), and only fall back to a bare `python3`/`python`
# if its version is within range.

# Highest-to-lowest preference within TensorFlow's supported range.
SUPPORTED_PY_VERSIONS=("3.12" "3.11" "3.10" "3.9")

# Allow the user to override with PYTHON_BIN=/path/to/python ./run.sh
PYTHON="${PYTHON_BIN:-}"

_py_version_supported() {
  # Returns 0 if the given interpreter's version is in 3.9 - 3.12 inclusive.
  local bin="$1"
  "$bin" - <<'PYEOF' >/dev/null 2>&1
import sys
major, minor = sys.version_info[:2]
sys.exit(0 if (major == 3 and 9 <= minor <= 12) else 1)
PYEOF
}

if [ -n "$PYTHON" ]; then
  # User-provided interpreter — validate it.
  if ! command -v "$PYTHON" >/dev/null 2>&1; then
    err "PYTHON_BIN='$PYTHON' not found on PATH."
    exit 1
  fi
  if ! _py_version_supported "$PYTHON"; then
    warn "PYTHON_BIN='$PYTHON' is outside TensorFlow's supported range (3.9-3.12)."
    warn "Continuing anyway because you set it explicitly."
  fi
else
  # Search for a versioned interpreter in the supported range.
  for v in "${SUPPORTED_PY_VERSIONS[@]}"; do
    if command -v "python$v" >/dev/null 2>&1; then
      PYTHON="python$v"
      break
    fi
  done

  # Fall back to a generic python3/python ONLY if its version is supported.
  if [ -z "$PYTHON" ]; then
    for candidate in python3 python; do
      if command -v "$candidate" >/dev/null 2>&1 && _py_version_supported "$candidate"; then
        PYTHON="$candidate"
        break
      fi
    done
  fi

  if [ -z "$PYTHON" ]; then
    err "No TensorFlow-compatible Python found (need CPython 3.9-3.12)."
    DEFAULT_VER="$(python3 --version 2>&1 || echo 'none')"
    err "Your default 'python3' is: ${DEFAULT_VER}."
    err "Install a supported version, e.g.:  brew install python@3.12"
    err "Then re-run, or point to it directly:  PYTHON_BIN=python3.12 ./run.sh"
    exit 1
  fi
fi

SELECTED_PY_VERSION="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
ok "Using Python interpreter: $PYTHON (${SELECTED_PY_VERSION})"

[ -d "$BACKEND_DIR" ]  || { err "Backend dir not found: $BACKEND_DIR";  exit 1; }
[ -d "$FRONTEND_DIR" ] || { err "Frontend dir not found: $FRONTEND_DIR"; exit 1; }

echo -e "${BOLD}AI-Based Customer Retirement Planner${NC}"
echo "-------------------------------------------"

# --------------------------------------------------------------------------- #
# Backend setup
# --------------------------------------------------------------------------- #
log "Preparing backend (Flask)..."
cd "$BACKEND_DIR"

# Create / reuse a virtual environment.
# If an existing venv was built with a different Python version, recreate it.
VENV_DIR="$BACKEND_DIR/.venv"
_venv_needs_rebuild=false
if [ -d "$VENV_DIR" ]; then
  if [ -x "$VENV_DIR/bin/python" ]; then
    VENV_PY_VER="$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
    if [ "$VENV_PY_VER" != "$SELECTED_PY_VERSION" ]; then
      warn "Existing venv uses Python ${VENV_PY_VER}, but we need ${SELECTED_PY_VERSION}."
      _venv_needs_rebuild=true
    fi
  else
    _venv_needs_rebuild=true
  fi
fi

if $_venv_needs_rebuild; then
  log "Removing incompatible venv and recreating..."
  rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
  log "Creating Python virtual environment with $PYTHON..."
  "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if [ "$INSTALL_DEPS" = true ]; then
  log "Installing backend dependencies (this may take a while on first run)..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  ok "Backend dependencies installed."
else
  warn "Skipping backend dependency install (--no-install)."
fi

# --------------------------------------------------------------------------- #
# Frontend setup
# --------------------------------------------------------------------------- #
log "Preparing frontend (React)..."
cd "$FRONTEND_DIR"

if [ "$INSTALL_DEPS" = true ]; then
  if [ ! -d "node_modules" ]; then
    log "Installing frontend dependencies (npm install)..."
    npm install --silent
    ok "Frontend dependencies installed."
  else
    ok "Frontend dependencies already present (node_modules exists)."
  fi
else
  warn "Skipping frontend dependency install (--no-install)."
fi

# Ensure the frontend knows where the API lives.
if [ ! -f ".env" ] && [ ! -f ".env.local" ]; then
  echo "REACT_APP_API_URL=http://${BACKEND_HOST}:${BACKEND_PORT}" > .env.local
  log "Created .env.local with REACT_APP_API_URL=http://${BACKEND_HOST}:${BACKEND_PORT}"
fi

# --------------------------------------------------------------------------- #
# Start services
# --------------------------------------------------------------------------- #
echo "-------------------------------------------"

log "Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT} ..."
cd "$BACKEND_DIR"
FLASK_HOST="$BACKEND_HOST" FLASK_PORT="$BACKEND_PORT" "$VENV_DIR/bin/python" server.py \
  2>&1 | sed "s/^/${BLUE}[backend]${NC} /" &
BACKEND_PID=$!

# Give the backend a moment and verify it came up via the /health endpoint.
sleep 3
if command -v curl >/dev/null 2>&1; then
  if curl -sf "http://${BACKEND_HOST}:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    ok "Backend health check passed."
  else
    warn "Backend health check did not respond yet (it may still be starting)."
  fi
fi

log "Starting frontend (React dev server)..."
cd "$FRONTEND_DIR"
# BROWSER=none prevents CRA from auto-opening a browser tab.
BROWSER=none npm start 2>&1 | sed "s/^/${GREEN}[frontend]${NC} /" &
FRONTEND_PID=$!

echo "-------------------------------------------"
ok "Both services are starting."
log "Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}"
log "Frontend: http://localhost:3000"
log "Press ${BOLD}Ctrl+C${NC} to stop both."
echo "-------------------------------------------"

# Wait for either process to exit; cleanup trap handles the rest.
wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || wait
