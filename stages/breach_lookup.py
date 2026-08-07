#!/usr/bin/env python3
"""
Stage 7: Breach / personal-info lookup for validated employee emails.

SENSITIVITY NOTE: this script retrieves real breach-exposure data for real
people (employees of the target company). Treat output/{company}/breaches.json
with the same care as the rest of this pipeline's personal-info stages -
restrict access, don't commit it to a public repo, and purge it once it's
no longer needed for the engagement (see README/data-handling notes).
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from lib.common import setup_logging, slugify_company
from lib.config import load_env_file, log_api_status_summary
from lib.docker_runner import run_docker_tool
from lib.json_utils import load_json, save_json

logger = logging.getLogger(__name__)

# Both providers are native SpiderFoot modules - no separate HTTP client
# needed, we just run SpiderFoot itself scoped to these two modules and
# parse whatever it emits. Confirmed present in this project's SpiderFoot
# module list: sfp_haveibeenpwned, sfp_intelx.
SPIDERFOOT_MODULES = "sfp_haveibeenpwned,sfp_intelx"

# NOTE: exact SpiderFoot event type names for breach data weren't confirmed
# against a live run at the time of writing - these are SpiderFoot's
# documented conventions, but verify against your first real run's JSON
# output and adjust this list if the actual type differs.
BREACH_EVENT_TYPES = ("EMAILADDR_COMPROMISED", "LEAKSITE_CONTENT")

REQUEST_DELAY_SECONDS = 1.5  # pacing between requests


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OSINT Stage: Breach / Personal Info Lookup"
    )
    parser.add_argument("--company", required=True, help="Target company name folder")
    return parser.parse_args()


def query_spiderfoot_breaches(email: str) -> List[Dict[str, Any]]:
    """Runs SpiderFoot's sfp_haveibeenpwned + sfp_intelx modules against a
    single email target and returns any breach/leak-related events found.

    SpiderFoot auto-detects the target type from the string given via -s;
    a well-formed email address is recognized as EMAILADDR automatically.
    """
    logger.debug(f"Querying SpiderFoot ({SPIDERFOOT_MODULES}) for {email}...")

    cmd_args = [
        "spiderfoot",
        "sf.py",
        "-s",
        email,
        "-m",
        SPIDERFOOT_MODULES,
        "-o",
        "json",
        "-q",
    ]

    try:
        raw_output = run_docker_tool(
            tool_name="spiderfoot",
            extra_args=cmd_args,
            timeout=120,
            capture_stdout=True,
        )
    except Exception as e:
        logger.error(f"SpiderFoot execution failed for {email}: {e}")
        return []

    breach_events: List[Dict[str, Any]] = []
    output_str = raw_output if isinstance(raw_output, str) else "\n".join(raw_output)

    for line in output_str.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # SpiderFoot sometimes mixes log lines with JSON events on stdout -
            # fall back to a loose text match rather than dropping the line.
            if any(t in line for t in BREACH_EVENT_TYPES) or "breach" in line.lower():
                breach_events.append({"type": "unparsed", "data": line})
            continue

        # NOTE: schema not yet confirmed against real output - handling both
        # a dict-shaped event ({"type": ..., "data": ...}) and a positional
        # list-shaped event, since SpiderFoot's -o json format wasn't
        # verified before this was written. Once confirmed, simplify this.
        if isinstance(event, dict):
            if event.get("type") in BREACH_EVENT_TYPES:
                breach_events.append(event)
        elif isinstance(event, list):
            logger.debug(f"Unexpected list-shaped event, raw: {event}")
            breach_events.append({"type": "unparsed-list", "data": event})
        else:
            logger.debug(f"Unexpected event shape ({type(event)}): {event}")

    return breach_events


def print_summary(
    checked: int, exposed: int, service_used: str, output_file: Path
) -> None:
    print("\n" + "=" * 55)
    print("           BREACH / PERSONAL INFO LOOKUP SUMMARY")
    print("=" * 55)
    print(f"Emails Checked        : {checked}")
    print(f"Emails With Exposure  : {exposed}")
    print(f"Service Used          : {service_used}")
    print("=" * 55)
    print(f"\nFinal output written to: {output_file.resolve()}")


def main() -> None:
    setup_logging()
    load_env_file()
    args = parse_arguments()

    company_slug = slugify_company(args.company)
    company_dir = Path("output") / company_slug
    input_file = company_dir / "validated_emails.json"
    output_file = company_dir / "breaches.json"

    if not input_file.exists():
        logger.error(
            f"Input file {input_file} not found! Run email_validation.py first."
        )
        return

    candidates = load_json(input_file)
    if not isinstance(candidates, list):
        logger.error("Invalid input format. Expected a JSON list.")
        return

    emails = [
        c["email"]
        for c in candidates
        if isinstance(c, dict)
        and c.get("email")
        and c.get("validation_status") in ("deliverable", "risky")
    ]
    logger.info(
        f"Checking {len(emails)} validated emails via SpiderFoot "
        f"({SPIDERFOOT_MODULES})..."
    )
    # Reminder, not a blocker: if HIBP_KEY/INTELX_KEY were added to .env
    # after the last seed_spiderfoot_db.py run, re-run that (container
    # stopped) before this, or both modules will just skip silently.
    log_api_status_summary()

    results: List[Dict[str, Any]] = []
    exposed_count = 0

    for email in emails:
        events = query_spiderfoot_breaches(email)
        entry = {
            "email": email,
            "breaches": [
                {"type": e.get("type"), "data": e.get("data")} for e in events
            ],
            "service": "spiderfoot",
        }
        if events:
            exposed_count += 1

        results.append(entry)
        time.sleep(REQUEST_DELAY_SECONDS)

    save_json(output_file, results)
    print_summary(len(results), exposed_count, "spiderfoot", output_file)


if __name__ == "__main__":
    main()
