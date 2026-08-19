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
  # Defensively attempt to stop all managed persistent services
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

# Service mapping matching required persistent services per script
# Note: searxng intrinsically depends on valkey and tor in docker-compose.yaml,
# so 'docker compose up -d searxng' auto-starts them too.
declare -A STAGE_SERVICES=(
  ["name_to_domain.py"]="searxng"
  ["domain_discovery.py"]="tor"
  ["dns_infra_discovery.py"]=""
  ["employee_discovery.py"]="tor"
  ["email_discovery.py"]="tor"
  ["email_validation.py"]="reacher"
  ["document_discovery.py"]=""
  ["breach_lookup.py"]="tor"
  ["darkweb_discovery.py"]="tor"
  ["aggregate_results.py"]=""
  ["llm_filter.py"]=""
  ["generate_report.py"]=""
  ["translate_report.py"]=""
)

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================

wait_for_reacher() {
  echo -e "${BLUE}[INFO] Waiting for Reacher API service readiness...${NC}"
  until curl -s http://localhost:8081/v0/check_email -X POST -H "Content-Type: application/json" -d '{"to_email":"test@example.com"}' >/dev/null 2>&1; do
    sleep 1
  done
  echo -e "${GREEN}[INFO] Reacher service is ready!${NC}\n"
}

wait_for_searxng() {
  echo -e "${BLUE}[INFO] Waiting for SearXNG service readiness...${NC}"
  until curl -s http://localhost:8080/healthz >/dev/null 2>&1; do
    sleep 1
  done
  echo -e "${GREEN}[INFO] SearXNG service is ready!${NC}\n"
}

wait_for_tor() {
  echo -e "${BLUE}[INFO] Waiting for Tor SOCKS proxy readiness (can take longer than other services while circuits build)...${NC}"
  until curl -s --socks5-hostname 127.0.0.1:9052 --max-time 15 \
    http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/ >/dev/null 2>&1; do
    sleep 2
  done
  echo -e "${GREEN}[INFO] Tor SOCKS proxy is ready!${NC}\n"
}

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
  shift 5
  # Any remaining positional args are passed through to the stage script
  # verbatim (e.g. --language FR for translate_report.py).
  local extra_args=("$@")

  echo -e "${BLUE}====================================================${NC}"
  echo -e "${BLUE}Running Stage ${stage_num}/${total_stages}${NC}"
  echo -e "${BLUE}${stage_name}${NC}"
  echo -e "${BLUE}====================================================${NC}"

  local stage_start
  stage_start=$(date +%s)

  # Execute the python script with the company argument (plus any extras)
  MODULE_NAME="${script_name%.py}"
  if ! python3 -m "stages.${MODULE_NAME}" --company "${company}" "${extra_args[@]}"; then
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
LANGUAGE_CODE=""
COMPANY=""

# Parse arguments to support the --pause and --language flags
while [[ $# -gt 0 ]]; do
  case "$1" in
  --pause)
    PAUSE_FLAG=true
    shift
    ;;
  --language)
    if [[ -z "${2:-}" ]]; then
      echo -e "${RED}--language requires a value, e.g. --language FR${NC}"
      exit 1
    fi
    LANGUAGE_CODE="$2"
    shift 2
    ;;
  -*)
    echo -e "${RED}Unknown option: $1${NC}"
    echo -e "${YELLOW}Usage: $0 [--pause] [--language <CODE>] \"Company Name\"${NC}"
    exit 1
    ;;
  *)
    if [[ -z "$COMPANY" ]]; then
      COMPANY="$1"
    else
      echo -e "${RED}Too many arguments. Only one company name is allowed.${NC}"
      echo -e "${YELLOW}Usage: $0 [--pause] [--language <CODE>] \"Company Name\"${NC}"
      exit 1
    fi
    shift
    ;;
  esac
done

if [[ -z "$COMPANY" ]]; then
  echo -e "${YELLOW}Usage: $0 [--pause] [--language <CODE>] \"Company Name\"${NC}"
  exit 1
fi

TOTAL_STAGES=${#STAGES[@]}

echo -e "${GREEN}Starting OSINT Pipeline for company: ${COMPANY}${NC}\n"
if [[ "${PAUSE_FLAG}" == true ]]; then
  echo -e "${YELLOW}Manual inspection mode enabled. The pipeline will pause after each stage.${NC}\n"
fi
if [[ -n "${LANGUAGE_CODE}" ]]; then
  echo -e "${YELLOW}Report translation enabled. A ${LANGUAGE_CODE} translation will be generated after the report.${NC}\n"
fi

PIPELINE_START=$(date +%s)
RUNNING_SERVICES=""

# =====================================================================
# RUN PIPELINE STAGES
# =====================================================================

# Automatically load environment variables if .env exists
if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

for i in "${!STAGES[@]}"; do
  STAGE_INFO="${STAGES[$i]}"
  STAGE_NAME="${STAGE_INFO%%:*}"
  SCRIPT_NAME="${STAGE_INFO#*:}"
  STAGE_NUM=$((i + 1))

  REQ_SERVICES="${STAGE_SERVICES[$SCRIPT_NAME]:-}"

  # Look ahead to see if the next sequential stage shares the exact same service requirements
  NEXT_SERVICES=""
  if [[ $((i + 1)) -lt ${#STAGES[@]} ]]; then
    NEXT_STAGE_INFO="${STAGES[$((i + 1))]}"
    NEXT_SCRIPT_NAME="${NEXT_STAGE_INFO#*:}"
    NEXT_SERVICES="${STAGE_SERVICES[$NEXT_SCRIPT_NAME]:-}"
  fi

  # 1. Startup phase
  if [[ -n "$REQ_SERVICES" && "$RUNNING_SERVICES" != "$REQ_SERVICES" ]]; then
    echo -e "${BLUE}[INFO] Starting required services for ${STAGE_NAME}: ${REQ_SERVICES}${NC}"
    docker compose up -d $REQ_SERVICES

    if [[ "$REQ_SERVICES" == *"reacher"* ]]; then wait_for_reacher; fi
    if [[ "$REQ_SERVICES" == *"searxng"* ]]; then wait_for_searxng; fi
    if [[ "$REQ_SERVICES" == *"tor"* ]]; then wait_for_tor; fi

    RUNNING_SERVICES="$REQ_SERVICES"
  fi

  # 2. Run the current stage
  run_stage "$STAGE_NUM" "$TOTAL_STAGES" "$STAGE_NAME" "$SCRIPT_NAME" "$COMPANY"

  # 3. Optional: translate the report conditionally
  if [[ "$SCRIPT_NAME" == "generate_report.py" && -n "${LANGUAGE_CODE}" ]]; then
    run_stage "$STAGE_NUM" "$TOTAL_STAGES" "Translate Report (${LANGUAGE_CODE})" \
      "translate_report.py" "$COMPANY" --language "${LANGUAGE_CODE}"
  fi

  # 4. Teardown phase (avoids unnecessary stop/starts by checking next stage)
  if [[ -n "$RUNNING_SERVICES" && "$RUNNING_SERVICES" != "$NEXT_SERVICES" ]]; then
    echo -e "${BLUE}[INFO] Stopping services (no longer required by next stage): ${RUNNING_SERVICES}${NC}"
    docker compose stop $RUNNING_SERVICES
    RUNNING_SERVICES=""
  fi

  # 5. Manual inspection pause
  pause_step
done

PIPELINE_END=$(date +%s)
TOTAL_ELAPSED=$((PIPELINE_END - PIPELINE_START))
FORMATTED_TOTAL=$(format_time "$TOTAL_ELAPSED")

echo -e "${GREEN}Pipeline completed successfully.${NC}"
echo -e "${GREEN}Total Runtime: ${FORMATTED_TOTAL} (${TOTAL_ELAPSED}s)${NC}"
