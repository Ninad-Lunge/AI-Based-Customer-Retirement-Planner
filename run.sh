#!/usr/bin/env bash
#
# run.sh — Start the AI-Based Customer Retirement Planner locally.
#
# Two modes:
#
#   ./run.sh                 Lightweight / STATELESS mode (default).
#                            Starts the Flask backend + React frontend only.
#                            No database, so this exercises the LEGACY engine
#                            path (anonymous recommendations via the historical
#                            yfinance + LSTM method). /v1 auth + CRUD return 503
#                            and the MPT engine is NOT active (it needs the store).
#
#   ./run.sh --with-db       FULL-STACK mode. Additionally starts a local
#                            PostgreSQL (Docker), applies Alembic migrations, and
#                            (unless --no-pipeline) runs the offline jobs
#                            (seed -> ingest -> analyze) to populate the model/
#                            metrics store so the Phase 3 MPT engine, auth, saved
#                            plans, and the audit log are all live.
#
# For a production-like container run instead, use:  docker compose up --build
#
# Options:
#   --with-db        Start Postgres + migrate + populate the store (full stack).
#   --no-pipeline    With --with-db: skip the seed/ingest/analyze jobs. The DB is
#                    migrated (auth/CRUD work) but the MPT store stays empty, so
#                    recommendations fall back to the legacy path.
#   --no-install     Skip dependency installation (venv + npm).
#   --db-port N      Host port to publish Postgres on (default: 55432).
#   -h, --help       Show this help.
#
# Press Ctrl+C to stop everything cleanly (and remove the DB container).

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
WITH_DB=false
RUN_PIPELINE=true
DB_PORT="${DB_PORT:-55432}"

# Local-dev Postgres container settings (ephemeral; removed on exit).
PG_CONTAINER="crp-rundb"
PG_USER="crp"
PG_PASSWORD="crp_local_dev"
PG_DB="crp"

# --------------------------------------------------------------------------- #
# Colours
# --------------------------------------------------------------------------- #
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  BLUE='\033[0;34m'; CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; MAGENTA=''; BOLD=''; NC=''
fi

log()   { echo -e "${CYAN}[run]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
err()   { echo -e "${RED}[err]${NC} $*" >&2; }
db()    { echo -e "${MAGENTA}[db]${NC} $*"; }

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
  case "$1" in
    --with-db)     WITH_DB=true ;;
    --no-pipeline) RUN_PIPELINE=false ;;
    --no-install)  INSTALL_DEPS=false ;;
    --db-port)
      shift
      [ $# -gt 0 ] || { err "--db-port requires a value"; exit 1; }
      DB_PORT="$1"
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^#//' | sed '1d'
      exit 0
      ;;
    *)
      err "Unknown argument: $1 (use --help)"
      exit 1
      ;;
  esac
  shift
done

# --------------------------------------------------------------------------- #
# Process / resource tracking & cleanup
# --------------------------------------------------------------------------- #
BACKEND_PID=""
FRONTEND_PID=""
DB_STARTED_BY_US=false

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
  if [ "$DB_STARTED_BY_US" = true ]; then
    db "Removing local Postgres container ($PG_CONTAINER)..."
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    ok "Database container removed."
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
if [ "$WITH_DB" = true ]; then
  command -v docker >/dev/null 2>&1 || { err "--with-db requires Docker, which is not installed."; exit 1; }
  docker info >/dev/null 2>&1 || { err "--with-db requires a running Docker daemon."; exit 1; }
fi

# Pick a TensorFlow-compatible Python interpreter.
#
# TensorFlow (a backend dependency via keras) does not publish wheels for the
# very latest Python releases right away.  As of this writing it supports
# CPython 3.9 - 3.12, so we must avoid defaulting to a newer interpreter
# (e.g. 3.13 / 3.14) that would make `pip install tensorflow` fail.
SUPPORTED_PY_VERSIONS=("3.12" "3.11" "3.10" "3.9")
PYTHON="${PYTHON_BIN:-}"

_py_version_supported() {
  local bin="$1"
  "$bin" - <<'PYEOF' >/dev/null 2>&1
import sys
major, minor = sys.version_info[:2]
sys.exit(0 if (major == 3 and 9 <= minor <= 12) else 1)
PYEOF
}

if [ -n "$PYTHON" ]; then
  if ! command -v "$PYTHON" >/dev/null 2>&1; then
    err "PYTHON_BIN='$PYTHON' not found on PATH."
    exit 1
  fi
  if ! _py_version_supported "$PYTHON"; then
    warn "PYTHON_BIN='$PYTHON' is outside TensorFlow's supported range (3.9-3.12)."
    warn "Continuing anyway because you set it explicitly."
  fi
else
  for v in "${SUPPORTED_PY_VERSIONS[@]}"; do
    if command -v "python$v" >/dev/null 2>&1; then
      PYTHON="python$v"
      break
    fi
  done
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
if [ "$WITH_DB" = true ]; then
  echo -e "Mode: ${BOLD}full-stack${NC} (Postgres + migrations$([ "$RUN_PIPELINE" = true ] && echo " + store pipeline"))"
else
  echo -e "Mode: ${BOLD}stateless${NC} (legacy engine path; use --with-db for the full MPT stack)"
fi
echo "-------------------------------------------"

# --------------------------------------------------------------------------- #
# Backend setup (venv + deps)
# --------------------------------------------------------------------------- #
log "Preparing backend (Flask)..."
cd "$BACKEND_DIR"

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

VENV_PY="$VENV_DIR/bin/python"

# --------------------------------------------------------------------------- #
# Optional: database bring-up + migrations + store pipeline (--with-db)
# --------------------------------------------------------------------------- #
export DATABASE_URL=""
if [ "$WITH_DB" = true ]; then
  # Reuse an existing container of the same name if present, else start one.
  if docker ps -a --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$"; then
    db "Reusing existing Postgres container ($PG_CONTAINER)."
    docker start "$PG_CONTAINER" >/dev/null 2>&1 || true
  else
    db "Starting Postgres container ($PG_CONTAINER) on host port ${DB_PORT}..."
    docker run -d --name "$PG_CONTAINER" \
      -e POSTGRES_USER="$PG_USER" \
      -e POSTGRES_PASSWORD="$PG_PASSWORD" \
      -e POSTGRES_DB="$PG_DB" \
      -p "${DB_PORT}:5432" \
      postgres:16-alpine >/dev/null
    DB_STARTED_BY_US=true
  fi

  db "Waiting for Postgres to become ready..."
  _ready=false
  for _ in $(seq 1 30); do
    if docker exec "$PG_CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
      _ready=true
      break
    fi
    sleep 1
  done
  if [ "$_ready" != true ]; then
    err "Postgres did not become ready in time."
    exit 1
  fi
  ok "Postgres is ready."

  export DATABASE_URL="postgresql+psycopg://${PG_USER}:${PG_PASSWORD}@127.0.0.1:${DB_PORT}/${PG_DB}"
  # A stable local dev JWT secret so tokens survive restarts within a session.
  export JWT_SECRET="${JWT_SECRET:-local-dev-insecure-secret}"

  db "Applying database migrations (alembic upgrade head)..."
  cd "$BACKEND_DIR"
  "$VENV_DIR/bin/alembic" upgrade head
  ok "Migrations applied."

  if [ "$RUN_PIPELINE" = true ]; then
    db "Populating the model/metrics store (seed -> ingest -> analyze)..."
    db "  This fetches market data via yfinance and may take a few minutes."
    "$VENV_PY" -m jobs seed    | sed "s/^/${MAGENTA}[jobs]${NC} /"
    "$VENV_PY" -m jobs ingest  | sed "s/^/${MAGENTA}[jobs]${NC} /"
    "$VENV_PY" -m jobs analyze | sed "s/^/${MAGENTA}[jobs]${NC} /"
    ok "Store populated: the MPT engine is now active."
  else
    warn "Skipping store pipeline (--no-pipeline): MPT engine inactive, recommendations use the legacy path."
  fi
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
# DATABASE_URL / JWT_SECRET are exported above (empty in stateless mode, so the
# backend runs exactly as before; set in --with-db mode to enable persistence).
FLASK_HOST="$BACKEND_HOST" FLASK_PORT="$BACKEND_PORT" "$VENV_PY" server.py \
  2>&1 | sed "s/^/${BLUE}[backend]${NC} /" &
BACKEND_PID=$!

sleep 3
if command -v curl >/dev/null 2>&1; then
  if curl -sf "http://${BACKEND_HOST}:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    ok "Backend health check passed."
    if [ "$WITH_DB" = true ]; then
      if curl -sf "http://${BACKEND_HOST}:${BACKEND_PORT}/ready" >/dev/null 2>&1; then
        ok "Backend readiness check passed."
      fi
    fi
  else
    warn "Backend health check did not respond yet (it may still be starting)."
  fi
fi

log "Starting frontend (React dev server)..."
cd "$FRONTEND_DIR"
BROWSER=none npm start 2>&1 | sed "s/^/${GREEN}[frontend]${NC} /" &
FRONTEND_PID=$!

echo "-------------------------------------------"
ok "Services are starting."
log "Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}"
log "Frontend: http://localhost:3000"
if [ "$WITH_DB" = true ]; then
  log "Database: postgresql://${PG_USER}:***@127.0.0.1:${DB_PORT}/${PG_DB}"
  if [ "$RUN_PIPELINE" = true ]; then
    log "Engine:   MPT (store populated) — /v1 auth, saved plans, and audit log active."
  else
    log "Engine:   legacy (store empty) — auth/CRUD active, MPT inactive."
  fi
else
  log "Engine:   legacy / stateless — for the full MPT stack, run: ./run.sh --with-db"
fi
log "Press ${BOLD}Ctrl+C${NC} to stop everything."
echo "-------------------------------------------"

wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || wait
