#!/usr/bin/env bash
# ==============================================================================
# install_tools.sh - Idempotent OSINT Docker Compose Setup & Verification Runner
# ==============================================================================

set -e
set -u
set -o pipefail

# ANSI color codes for readable output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Initialize empty arrays explicitly to avoid 'unbound variable' errors under set -u
PASSED_TOOLS=()
FAILED_TOOLS=()

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; }

echo -e "${BLUE}=================================================================${NC}"
echo -e "${BLUE}        OSINT Recon Pipeline: Docker Build & Smoke Test          ${NC}"
echo -e "${BLUE}=================================================================${NC}"

# ------------------------------------------------------------------------------
# 1. Pull Official Images & Build Custom Dockerfiles
# ------------------------------------------------------------------------------
log_info "Step 1: Pulling official images and building custom services..."
if ! docker compose build --pull; then
  log_fail "docker compose build failed. Aborting smoke tests to prevent testing stale cache."
  exit 1
fi

# ------------------------------------------------------------------------------
# 2. Define Smoke Tests (Service Name -> Args to verify execution)
# ------------------------------------------------------------------------------
declare -A TOOLS=(
  ["spiderfoot"]="-h"
  ["amass"]="-version"
  ["theharvester"]="-h"
  ["certspotter"]="-help"
  ["maigret"]="--version"
  ["metagoofil"]="-h"
)

# ------------------------------------------------------------------------------
# 3. Execute Verification Smoke Tests
# ------------------------------------------------------------------------------
echo ""
log_info "Step 2: Executing containerized smoke tests..."
echo "-----------------------------------------------------------------"

# Temporarily disable exit-on-error so intentional non-zero tool --help codes don't kill the script
set +e
for service in "${!TOOLS[@]}"; do
  args="${TOOLS[$service]}"
  printf "%-15s | Executing: docker compose run --rm %s %s ... " "$service" "$service" "$args"

  # Capture combined stdout/stderr and exit code
  output=$(docker compose run --rm --no-TTY "$service" $args 2>&1)
  exit_code=$?

  shopt -s nocasematch
  if [[ $exit_code -eq 0 ]] || [[ "$output" =~ (usage|version|spiderfoot|theHarvester|metagoofil|certspotter|Amass) ]]; then
    echo -e "${GREEN}SUCCESS${NC}"
    PASSED_TOOLS+=("$service")
  else
    echo -e "${RED}FAILED (Exit: $exit_code)${NC}"
    first_error_line=$(echo "$output" | grep -v '^$' | head -n 1 | tr -d '\r')
    FAILED_TOOLS+=("$service -> Reason: ${first_error_line:-Unknown execution error}")
  fi
  shopt -u nocasematch
done
set -e

# ------------------------------------------------------------------------------
# 4. Final Summary Report
# ------------------------------------------------------------------------------
echo ""
echo -e "${BLUE}=================================================================${NC}"
echo -e "${BLUE}                    PIPELINE VERIFICATION SUMMARY                ${NC}"
echo -e "${BLUE}=================================================================${NC}"

echo -e "\n${GREEN}Successfully Verified Tools (${#PASSED_TOOLS[@]}):${NC}"
if [ ${#PASSED_TOOLS[@]} -eq 0 ]; then
  echo "  (None)"
else
  for tool in "${PASSED_TOOLS[@]}"; do
    echo -e "  [✓] $tool"
  done
fi

echo -e "\n${RED}Failed Tools (${#FAILED_TOOLS[@]}):${NC}"
if [ ${#FAILED_TOOLS[@]} -eq 0 ]; then
  echo "  (None - All tools ready for recon!)"
else
  for failure in "${FAILED_TOOLS[@]}"; do
    echo -e "  [✗] $failure"
  done
fi

echo ""
echo -e "${YELLOW}Usage Reminder:${NC}"
echo -e "  • Start persistent web UI:  ${GREEN}docker compose up -d spiderfoot${NC}"
echo -e "  • Start email validator:    ${GREEN}docker compose up -d reacher${NC}"
echo -e "  • Run on-demand scan:       ${GREEN}docker compose run --rm amass enum -d example.com${NC}"
echo -e "${BLUE}=================================================================${NC}"

# Exit with failure code if any container failed its smoke test
if [ ${#FAILED_TOOLS[@]} -ne 0 ]; then
  exit 1
fi

echo "================================================================="
echo "        OSINT Recon Pipeline: Host & LLM Setup                   "
echo "================================================================="

# -----------------------------------------------------------------
# 1. Install Ollama (Host Machine)
# -----------------------------------------------------------------
echo "[INFO] Step 1: Checking for Ollama installation..."

if ! command -v ollama >/dev/null 2>&1; then
  echo "[INFO] Ollama not found. Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "[INFO] Ollama is already installed. Skipping installation."
fi

# -----------------------------------------------------------------
# 2. Ensure Ollama Service is Running
# -----------------------------------------------------------------
echo "[INFO] Step 2: Ensuring Ollama daemon is active..."

if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "[WARN] Ollama daemon is not responding. Attempting to start service..."
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files ollama.service >/dev/null 2>&1; then
    sudo systemctl start ollama
  else
    ollama serve >/dev/null 2>&1 &
  fi
  sleep 5
fi

# -----------------------------------------------------------------
# 3. Pull Llama 3.1 8B
# -----------------------------------------------------------------
MODEL_TAG="llama3.1:8b"
echo "[INFO] Step 3: Verifying LLM model '${MODEL_TAG}'..."

if ollama list | grep -E -q "^${MODEL_TAG}([[:space:]]|$)"; then
  echo "[INFO] Model '${MODEL_TAG}' is already present. Skipping pull."
else
  echo "[INFO] Pulling '${MODEL_TAG}'..."
  ollama pull "${MODEL_TAG}"
fi

# -----------------------------------------------------------------
# 4. Smoke Test the Local Model API
# -----------------------------------------------------------------
echo "[INFO] Step 4: Smoke testing Ollama API response..."

TEST_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:11434/api/tags)
if [ "${TEST_RESPONSE}" -eq 200 ]; then
  echo "[SUCCESS] Ollama and '${MODEL_TAG}' are installed and ready for OSINT extraction!"
else
  echo "[FAIL] Ollama API returned HTTP status ${TEST_RESPONSE}."
  exit 1
fi

exit 0
