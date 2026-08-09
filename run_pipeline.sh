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
# CLEANUP TRAP
# =====================================================================
cleanup() {
  echo -e "\n${BLUE}[INFO] Pipeline finished or interrupted. Cleaning up persistent background services...${NC}"
  docker compose stop reacher tor searxng valkey 2>/dev/null || true
}
# Catch normal exit, CTRL+C (SIGINT), and script errors (SIGTERM/ERR)
trap cleanup EXIT INT TERM

# =====================================================================
# PIPELINE CONFIGURATION
# =====================================================================
# Define stages in the format "Stage Name:script_filename"
STAGES=(
  "Name to Domain:name_to_domain.py"
  "Domain Discovery:domain_discovery.py"
  "DNS Infrastructure Discovery:dns_infra_discovery.py"
  "Employee Discovery:employee_discovery.py"
  "Email Discovery:email_discovery.py"
  "Email Validation:email_validation.py"
  "Document Discovery:document_discovery.py"
  "Breach Lookup:breach_lookup.py"
  "Darkweb Discovery:darkweb_discovery.py"
  "Aggregate Results:aggregate_results.py"
  "LLM Filter:llm_filter.py"
  "Generate Report:generate_report.py"
)

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================

# Format time in MM:SS for better readability
format_time() {
  local total_seconds=$1
  local mins=$((total_seconds / 60))
  local secs=$((total_seconds % 60))
  printf "%02d:%02d\n" "$mins" "$secs"
}

run_stage() {
  local stage_num="$1"
  local total_stages="$2"
  local stage_name="$3"
  local script_name="$4"
  local company="$5"

  echo -e "${BLUE}====================================================${NC}"
  echo -e "${BLUE}Running Stage ${stage_num}/${total_stages}${NC}"
  echo -e "${BLUE}${stage_name}${NC}"
  echo -e "${BLUE}====================================================${NC}"

  local stage_start
  stage_start=$(date +%s)

  # Execute the python script with the company argument
  MODULE_NAME="${script_name%.py}"
  if ! python3 -m "stages.${MODULE_NAME}" --company "${company}"; then
    echo -e "${RED}Stage failed:${NC}"
    echo -e "${RED}${stage_name}${NC}"
    exit 1
  fi

  local stage_end
  stage_end=$(date +%s)
  local elapsed=$((stage_end - stage_start))
  local formatted_elapsed
  formatted_elapsed=$(format_time "$elapsed")

  echo -e "${GREEN}Stage completed in ${formatted_elapsed} (${elapsed}s).${NC}\n"
}

pause_step() {
  if [[ "${PAUSE_FLAG}" == true ]]; then
    echo -e "${YELLOW}[PAUSED] Stage complete. Press 'c' to continue to the next stage...${NC}"
    while true; do
      read -r -s -n 1 key
      if [[ "$key" == "c" || "$key" == "C" ]]; then
        echo -e "${GREEN}Continuing pipeline...${NC}\n"
        break
      else
        echo -e "\n${YELLOW}[PAUSED] Invalid key. Press 'c' to continue...${NC}"
      fi
    done
  fi
}

# =====================================================================
# MAIN EXECUTION
# =====================================================================

PAUSE_FLAG=false
COMPANY=""

# Parse arguments to support the --pause flag
while [[ $# -gt 0 ]]; do
  case "$1" in
  --pause)
    PAUSE_FLAG=true
    shift
    ;;
  -*)
    echo -e "${RED}Unknown option: $1${NC}"
    echo -e "${YELLOW}Usage: $0 [--pause] \"Company Name\"${NC}"
    exit 1
    ;;
  *)
    if [[ -z "$COMPANY" ]]; then
      COMPANY="$1"
    else
      echo -e "${RED}Too many arguments. Only one company name is allowed.${NC}"
      echo -e "${YELLOW}Usage: $0 [--pause] \"Company Name\"${NC}"
      exit 1
    fi
    shift
    ;;
  esac
done

if [[ -z "$COMPANY" ]]; then
  echo -e "${YELLOW}Usage: $0 [--pause] \"Company Name\"${NC}"
  exit 1
fi

TOTAL_STAGES=${#STAGES[@]}

echo -e "${GREEN}Starting OSINT Pipeline for company: ${COMPANY}${NC}\n"
if [[ "${PAUSE_FLAG}" == true ]]; then
  echo -e "${YELLOW}Manual inspection mode enabled. The pipeline will pause after each stage.${NC}\n"
fi

PIPELINE_START=$(date +%s)

# =====================================================================
# START PERSISTENT SERVICES GLOBALLY
# =====================================================================
echo -e "${BLUE}[INFO] Starting persistent backend services (Reacher, Tor, SearXNG)...${NC}"
docker compose up -d reacher tor searxng

# Wait briefly for Reacher HTTP server to start listening
echo -e "${BLUE}[INFO] Waiting for Reacher API service readiness...${NC}"
until curl -s http://localhost:8081/v0/check_email -X POST -H "Content-Type: application/json" -d '{"to_email":"test@example.com"}' >/dev/null 2>&1; do
  sleep 1
done
echo -e "${GREEN}[INFO] Reacher service is ready!${NC}\n"

# Wait briefly for SearXNG HTTP server to start listening
echo -e "${BLUE}[INFO] Waiting for SearXNG service readiness...${NC}"
until curl -s http://localhost:8080/healthz >/dev/null 2>&1; do
  sleep 1
done
echo -e "${GREEN}[INFO] SearXNG service is ready!${NC}\n"

# Wait for the Tor SOCKS proxy to be genuinely usable
echo -e "${BLUE}[INFO] Waiting for Tor SOCKS proxy readiness (can take longer than other services while circuits build)...${NC}"
until curl -s --socks5-hostname 127.0.0.1:9052 --max-time 15 \
  http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/ >/dev/null 2>&1; do
  sleep 2
done
echo -e "${GREEN}[INFO] Tor SOCKS proxy is ready!${NC}\n"

# =====================================================================
# RUN PIPELINE STAGES
# =====================================================================
for i in "${!STAGES[@]}"; do
  STAGE_INFO="${STAGES[$i]}"
  STAGE_NAME="${STAGE_INFO%%:*}"
  SCRIPT_NAME="${STAGE_INFO#*:}"
  STAGE_NUM=$((i + 1))

  run_stage "$STAGE_NUM" "$TOTAL_STAGES" "$STAGE_NAME" "$SCRIPT_NAME" "$COMPANY"

  # Trigger the manual inspection pause function
  pause_step
done

PIPELINE_END=$(date +%s)
TOTAL_ELAPSED=$((PIPELINE_END - PIPELINE_START))
FORMATTED_TOTAL=$(format_time "$TOTAL_ELAPSED")

echo -e "${GREEN}Pipeline completed successfully.${NC}"
echo -e "${GREEN}Total Runtime: ${FORMATTED_TOTAL} (${TOTAL_ELAPSED}s)${NC}"
