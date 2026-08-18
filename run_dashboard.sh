#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status.
# Treat unset variables as an error.
# Fail on the first error in a pipeline.
set -euo pipefail

# =====================================================================
# ANSI COLORS
# =====================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# =====================================================================
# DEFAULT CONFIGURATION
# =====================================================================
HOST="127.0.0.1"
PORT="8000"
RELOAD=false

# =====================================================================
# CLEANUP TRAP
# =====================================================================
cleanup() {
  echo -e "\n${BLUE}[INFO] Stopping OSINT Review Dashboard server...${NC}"
}
# Catch normal exit, CTRL+C (SIGINT), and script errors (SIGTERM/ERR)
trap cleanup EXIT INT TERM

# =====================================================================
# CLI ARGUMENT PARSING
# =====================================================================
while [[ $# -gt 0 ]]; do
  case "$1" in
  --host)
    if [[ -z "${2:-}" ]]; then
      echo -e "${RED}--host requires a value, e.g. --host 0.0.0.0${NC}"
      exit 1
    fi
    HOST="$2"
    shift 2
    ;;
  --port)
    if [[ -z "${2:-}" ]]; then
      echo -e "${RED}--port requires a value, e.g. --port 8000${NC}"
      exit 1
    fi
    PORT="$2"
    shift 2
    ;;
  --reload)
    RELOAD=true
    shift
    ;;
  -h | --help)
    echo -e "${YELLOW}Usage: $0 [--host <HOST>] [--port <PORT>] [--reload]${NC}"
    exit 0
    ;;
  -*)
    echo -e "${RED}Unknown option: $1${NC}"
    echo -e "${YELLOW}Usage: $0 [--host <HOST>] [--port <PORT>] [--reload]${NC}"
    exit 1
    ;;
  *)
    echo -e "${RED}Unexpected argument: $1${NC}"
    echo -e "${YELLOW}Usage: $0 [--host <HOST>] [--port <PORT>] [--reload]${NC}"
    exit 1
    ;;
  esac
done

# =====================================================================
# MAIN EXECUTION
# =====================================================================
echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE} Starting OSINT Review Dashboard ${NC}"
echo -e "${BLUE}====================================================${NC}"

# Verify python availability
if ! command -v python3 &>/dev/null; then
  echo -e "${RED}[ERROR] python3 could not be found. Please ensure Python is installed.${NC}"
  exit 1
fi

UVICORN_CMD=(python3 -m uvicorn review_dashboard.backend:app --host "${HOST}" --port "${PORT}")

if [[ "${RELOAD}" == true ]]; then
  UVICORN_CMD+=(--reload)
  echo -e "${YELLOW}[INFO] Auto-reload mode enabled.${NC}"
fi

echo -e "${GREEN}[INFO] Dashboard server launching at: http://${HOST}:${PORT}${NC}\n"

# Run Uvicorn server
"${UVICORN_CMD[@]}"
