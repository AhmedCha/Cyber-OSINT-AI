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
import time
from pathlib import Path
from typing import Any, Dict, List

from lib.apify_utils import run_apify_actor
from lib.common import setup_logging, slugify_company
from lib.config import load_env_file, log_api_status_summary
from lib.docker_runner import run_docker_tool
from lib.json_utils import load_json, save_json

logger = logging.getLogger(__name__)

SPIDERFOOT_MODULES = "sfp_haveibeenpwned,sfp_intelx,sfp_citadel"

# NOTE: exact SpiderFoot event type names for breach data weren't confirmed
# against a live run at the time of writing - these are SpiderFoot's
# documented conventions, but verify against your first real run's JSON
# output and adjust this list if the actual type differs.
BREACH_EVENT_TYPES = ("EMAILADDR_COMPROMISED", "LEAKSITE_CONTENT")

APIFY_BREACH_ACTOR_ID = "tsOZE5njcLbdFewtU"

REQUEST_DELAY_SECONDS = 1.5  # pacing between requests

CHECKABLE_VALIDATION_STATUSES = (
    "deliverable",
    "risky",
    "smtp_inconclusive_catchall",
)


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


def query_apify_breach_checker(emails: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Runs the Apify breach-checker actor once for ALL emails in a single
    batched call (its input schema takes a list, unlike SpiderFoot's
    per-target invocation).

    NOTE: output schema not yet confirmed against a real run - only the
    input ({"emails": [...]}) was confirmed from the actor's own example
    script. Each raw item is logged at DEBUG level; check that output on
    first run and adjust the email-matching key below (currently guessing
    "email") if the actor uses a different field name.
    """
    if not emails:
        return {}

    logger.info(f"Querying Apify breach-checker actor for {len(emails)} email(s)...")
    items = run_apify_actor(APIFY_BREACH_ACTOR_ID, {"emails": emails})

    results_by_email: Dict[str, List[Dict[str, Any]]] = {e: [] for e in emails}
    for item in items:
        logger.debug(f"Raw Apify breach item: {item}")
        # Best-guess field names for matching an item back to its email -
        # verify against the debug output above and adjust if wrong.
        email_key = item.get("email") or item.get("Email") or item.get("input")
        if email_key in results_by_email:
            results_by_email[email_key].append(item)
        else:
            logger.warning(
                f"Apify breach item didn't match a known email "
                f"(tried 'email'/'Email'/'input' fields) - raw item: {item}"
            )

    return results_by_email


def print_summary(
    checked: int,
    exposed: int,
    catchall_confirmed: int,
    service_used: str,
    output_file: Path,
) -> None:
    print("\n" + "=" * 55)
    print("           BREACH / PERSONAL INFO LOOKUP SUMMARY")
    print("=" * 55)
    print(f"Emails Checked        : {checked}")
    print(f"Emails With Exposure  : {exposed}")
    print(f"Catch-all Emails Confirmed via Breach : {catchall_confirmed}")
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
        and c.get("validation_status") in CHECKABLE_VALIDATION_STATUSES
    ]
    # Keep the originating validation_status per email so results can
    # distinguish "already confirmed deliverable" from "catch-all, breach
    # data is what's confirming it" - see the catchall_confirmed_by_breach
    # flag below.
    validation_status_by_email = {
        c["email"]: c.get("validation_status")
        for c in candidates
        if isinstance(c, dict) and c.get("email") in emails
    }
    logger.info(
        f"Checking {len(emails)} emails ({', '.join(CHECKABLE_VALIDATION_STATUSES)}) "
        f"via SpiderFoot ({SPIDERFOOT_MODULES}) and Apify (actor {APIFY_BREACH_ACTOR_ID})..."
    )
    # Reminder, not a blocker: if HIBP_KEY/INTELX_KEY were added to .env
    # after the last seed_spiderfoot_db.py run, re-run that (container
    # stopped) before this, or both SpiderFoot modules will just skip
    # silently.
    log_api_status_summary()

    # Apify actor accepts the full email list in one call - run it once
    # up front rather than per-email like SpiderFoot below.
    apify_results = query_apify_breach_checker(emails)

    results: List[Dict[str, Any]] = []
    exposed_count = 0
    catchall_confirmed_count = 0

    for email in emails:
        sf_events = query_spiderfoot_breaches(email)
        ap_items = apify_results.get(email, [])

        breaches = [
            {"type": e.get("type"), "data": e.get("data"), "source": "spiderfoot"}
            for e in sf_events
        ] + [
            {"type": "apify-breach", "data": item, "source": "apify"}
            for item in ap_items
        ]

        entry = {
            "email": email,
            "validation_status": validation_status_by_email.get(email),
            "breaches": breaches,
            "services_checked": ["spiderfoot", "apify"],
        }
        if breaches:
            exposed_count += 1
            # A breach hit against a catch-all/inconclusive address is
            # independent evidence the mailbox is real, since breach data
            # is tied to an exact address rather than an SMTP guess.
            if validation_status_by_email.get(email) == "smtp_inconclusive_catchall":
                entry["catchall_confirmed_by_breach"] = True
                catchall_confirmed_count += 1

        results.append(entry)
        time.sleep(REQUEST_DELAY_SECONDS)

    save_json(output_file, results)
    print_summary(
        len(results),
        exposed_count,
        catchall_confirmed_count,
        "spiderfoot+apify",
        output_file,
    )


if __name__ == "__main__":
    main()
