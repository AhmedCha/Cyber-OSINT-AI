#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status.[cite: 23]
# Treat unset variables as an error.[cite: 23]
# Fail on the first error in a pipeline.[cite: 23]
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
  docker compose stop reacher tor 2>/dev/null || true
}
# Catch normal exit, CTRL+C (SIGINT), and script errors (SIGTERM/ERR)
trap cleanup EXIT INT TERM

# =====================================================================
# PIPELINE CONFIGURATION
# =====================================================================
# Define stages in the format "Stage Name:script_filename"[cite: 23]
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

# Format time in MM:SS for better readability[cite: 23]
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

  # Execute the python script with the company argument[cite: 23]
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

# =====================================================================
# MAIN EXECUTION
# =====================================================================

if [[ $# -eq 0 ]]; then
  echo -e "${YELLOW}Usage: $0 \"Company Name\"${NC}"
  exit 1
fi

COMPANY="$1"
TOTAL_STAGES=${#STAGES[@]}

echo -e "${GREEN}Starting OSINT Pipeline for company: ${COMPANY}${NC}\n"

PIPELINE_START=$(date +%s)

# =====================================================================
# START PERSISTENT SERVICES GLOBALLY
# =====================================================================
echo -e "${BLUE}[INFO] Starting persistent backend services (Reacher, Tor)...${NC}"
docker compose up -d reacher tor

# Wait briefly for Reacher HTTP server to start listening
echo -e "${BLUE}[INFO] Waiting for Reacher API service readiness...${NC}"
until curl -s http://localhost:8080/v0/check_email -X POST -H "Content-Type: application/json" -d '{"to_email":"test@example.com"}' >/dev/null 2>&1; do
  sleep 1
done
echo -e "${GREEN}[INFO] Reacher service is ready!${NC}\n"

# =====================================================================
# RUN PIPELINE STAGES
# =====================================================================
for i in "${!STAGES[@]}"; do
  STAGE_INFO="${STAGES[$i]}"
  STAGE_NAME="${STAGE_INFO%%:*}"
  SCRIPT_NAME="${STAGE_INFO#*:}"
  STAGE_NUM=$((i + 1))

  run_stage "$STAGE_NUM" "$TOTAL_STAGES" "$STAGE_NAME" "$SCRIPT_NAME" "$COMPANY"
done

PIPELINE_END=$(date +%s)
TOTAL_ELAPSED=$((PIPELINE_END - PIPELINE_START))
FORMATTED_TOTAL=$(format_time "$TOTAL_ELAPSED")

echo -e "${GREEN}Pipeline completed successfully.${NC}"
echo -e "${GREEN}Total Runtime: ${FORMATTED_TOTAL} (${TOTAL_ELAPSED}s)${NC}"
