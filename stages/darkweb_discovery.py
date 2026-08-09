#!/usr/bin/env python3
"""
Stage 8: Dark web mention lookup for validated domains, the company name,
and known employees.

SENSITIVITY NOTE: this script searches dark web indexes/leak sites for
mentions of real people and real infrastructure belonging to the target
company. Treat output/{company}/darkweb.json with at least the same care
as the rest of this pipeline's personal-info stages (see breach_lookup.py)
- arguably more so: dark web search results can surface genuinely
disturbing or illegal content references (leak site listings, extremist
forum content, csam-adjacent index noise, etc.) that are simply part of
what these indexes crawl. Restrict access to this output, don't commit it
to a public repo, don't forward raw hits to anyone without review, and
purge it once it's no longer needed for the engagement (see README/
data-handling notes).
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from lib.common import setup_logging, slugify_company
from lib.config import load_env_file, log_api_status_summary
from lib.docker_runner import run_docker_tool
from lib.json_utils import load_json, save_json

logger = logging.getLogger(__name__)

SPIDERFOOT_MODULES = "sfp_ahmia,sfp_torch,sfp_onionsearchengine"

# NOTE: exact SpiderFoot event type names emitted by these three dark-web
# modules weren't confirmed against a live run at the time of writing -
# these are SpiderFoot's documented conventions for darknet-sourced
# content, but verify against your first real run's JSON output and
# adjust this list if the actual type(s) differ (e.g. some SpiderFoot
# versions emit DARKNET_MENTION_URL / DARKNET_MENTION_CONTENT separately).
DARKWEB_EVENT_TYPES = ("DARKNET_MENTION_CONTENT", "DARKNET_MENTION_URL")

# NOTE: unlike breach_lookup.py's email targets (which SpiderFoot
# auto-detects as EMAILADDR from the -s string alone), domain/company/
# person strings are ambiguous enough that we pass an explicit -t type.
# These type names are SpiderFoot's documented target types but weren't
# confirmed against a live run - verify on first use and adjust if
# SpiderFoot rejects one of them.
TARGET_TYPE_DOMAIN = "INTERNET_NAME"
TARGET_TYPE_COMPANY = "COMPANY_NAME"
TARGET_TYPE_PERSON = "HUMAN_NAME"

REQUEST_DELAY_SECONDS = 1.5  # pacing between requests


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OSINT Stage: Dark Web Mention Lookup")
    parser.add_argument("--company", required=True, help="Target company name folder")
    return parser.parse_args()


def query_spiderfoot_darkweb(target: str, target_type: str) -> List[Dict[str, Any]]:
    """Runs SpiderFoot's sfp_ahmia + sfp_torch + sfp_onionsearchengine
    modules against a single target (domain, company name, or person
    name) and returns any dark-web-mention events found.

    Reuses the same command-construction and defensive event-parsing
    pattern as breach_lookup.py's query_spiderfoot_breaches(): the
    spiderfoot service needs "sf.py" as the first argument after the
    service name (bare python entrypoint), and output is parsed
    defensively since -o json can mix log lines with JSON events on
    stdout, and the exact schema wasn't confirmed before this was written.
    """
    logger.debug(
        f"Querying SpiderFoot ({SPIDERFOOT_MODULES}) for "
        f"{target_type} target '{target}'..."
    )

    cmd_args = [
        "spiderfoot",
        "sf.py",
        "-s",
        target,
        "-t",
        target_type,
        "-m",
        SPIDERFOOT_MODULES,
        "-o",
        "json",
    ]

    try:
        raw_output = run_docker_tool(
            tool_name="spiderfoot",
            extra_args=cmd_args,
            timeout=120,
            capture_stdout=True,
        )
    except Exception as e:
        logger.error(f"SpiderFoot execution failed for '{target}': {e}")
        return []

    darkweb_events: List[Dict[str, Any]] = []
    output_str = raw_output if isinstance(raw_output, str) else "\n".join(raw_output)

    # TEMP DEBUG: dump raw stdout for this target so we can see exactly
    # what SpiderFoot is sending on the wire (log lines vs. JSON events,
    # whether stderr is merged in, etc.) before deciding how to parse it.
    # Remove once the real event shape/type names are confirmed.
    debug_file = Path(
        f"/tmp/spiderfoot_raw_{target_type}_{slugify_company(target)}.log"
    )
    debug_file.write_text(output_str)
    logger.debug(f"Wrote raw SpiderFoot output for '{target}' to {debug_file}")

    for line in output_str.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # SpiderFoot sometimes mixes log lines with JSON events on stdout -
            # fall back to a loose text match rather than dropping the line.
            if any(t in line for t in DARKWEB_EVENT_TYPES) or "darknet" in line.lower():
                darkweb_events.append({"type": "unparsed", "data": line})
            continue

        # NOTE: schema not yet confirmed against real output - handling both
        # a dict-shaped event ({"type": ..., "data": ...}) and a positional
        # list-shaped event, since SpiderFoot's -o json format wasn't
        # verified before this was written. Once confirmed, simplify this.
        if isinstance(event, dict):
            if event.get("type") in DARKWEB_EVENT_TYPES:
                darkweb_events.append(event)
        elif isinstance(event, list):
            # SpiderFoot's -o json output apparently emits each line as a
            # single-item list wrapping a dict (rather than a bare dict),
            # and that includes SpiderFoot's own scan-seed event (module
            # "SpiderFoot UI", type "Username"/"Human Name") which just
            # echoes the target string back - NOT a hit from sfp_ahmia /
            # sfp_torch / sfp_onionsearchengine. Unwrap and apply the same
            # type filter as the dict branch instead of accepting every
            # list-shaped line unconditionally.
            for item in event:
                if isinstance(item, dict) and item.get("type") in DARKWEB_EVENT_TYPES:
                    darkweb_events.append(item)
                else:
                    logger.debug(f"Skipping non-matching list-shaped event: {item}")
        else:
            logger.debug(f"Unexpected event shape ({type(event)}): {event}")

    return darkweb_events


def load_domain_targets(company_dir: Path) -> List[str]:
    """Loads validated domains from domains.json. Returns an empty list
    (with a warning, not a hard failure) if the file is missing, since
    dark web discovery can still run against the company name and
    employee names alone.

    NOTE: exact schema of domains.json wasn't confirmed at the time of
    writing - assuming a list of dicts with a "domain" key and some kind
    of validation/status flag, matching the pattern used for
    validated_emails.json in breach_lookup.py. Adjust the filter below if
    the real schema differs (e.g. no status field, or all entries should
    be treated as valid).
    """
    domains_file = company_dir / "domains.json"
    if not domains_file.exists():
        logger.warning(f"{domains_file} not found - skipping domain-scoped search.")
        return []

    data = load_json(domains_file)
    if not isinstance(data, list):
        logger.warning(
            "domains.json is not a JSON list - skipping domain-scoped search."
        )
        return []

    domains: List[str] = []
    for d in data:
        if isinstance(d, dict) and d.get("domain"):
            status = d.get("status") or d.get("validation_status")
            if status is None or status in ("valid", "validated", "confirmed"):
                domains.append(d["domain"])
        elif isinstance(d, str):
            domains.append(d)

    return domains


def load_employee_targets(company_dir: Path) -> List[str]:
    """Loads employee full names from employees.json.

    NOTE: exact schema of employees.json wasn't confirmed at the time of
    writing - assuming a list of dicts exposing either a "full_name" key
    or separate "first_name"/"last_name" keys, mirroring the kind of
    output employee_discovery.py would produce. Adjust below if the real
    field names differ.
    """
    employees_file = company_dir / "employees.json"
    if not employees_file.exists():
        logger.warning(f"{employees_file} not found - skipping employee-scoped search.")
        return []

    data = load_json(employees_file)
    if not isinstance(data, list):
        logger.warning(
            "employees.json is not a JSON list - skipping employee-scoped search."
        )
        return []

    names: List[str] = []
    for e in data:
        if not isinstance(e, dict):
            continue
        full_name = e.get("full_name")
        if not full_name:
            first = e.get("first_name", "")
            last = e.get("last_name", "")
            full_name = f"{first} {last}".strip()
        if full_name:
            names.append(full_name)

    return names


def print_summary(checked: int, hits: int, output_file: Path) -> None:
    print("\n" + "=" * 55)
    print("              DARK WEB MENTION LOOKUP SUMMARY")
    print("=" * 55)
    print(f"Targets Checked        : {checked}")
    print(f"Targets With Mentions   : {hits}")
    print(f"Service Used            : spiderfoot ({SPIDERFOOT_MODULES})")
    print("=" * 55)
    print(f"\nFinal output written to: {output_file.resolve()}")


def main() -> None:
    setup_logging()
    load_env_file()
    args = parse_arguments()

    company_slug = slugify_company(args.company)
    company_dir = Path("output") / company_slug
    output_file = company_dir / "darkweb.json"

    if not company_dir.exists():
        logger.error(
            f"Output directory {company_dir} not found! Run earlier stages first."
        )
        return

    domains = load_domain_targets(company_dir)
    employees = load_employee_targets(company_dir)

    # Build the ordered list of (target, target_type) pairs to query.
    targets: List[Tuple[str, str]] = []
    for domain in domains:
        targets.append((domain, TARGET_TYPE_DOMAIN))
    targets.append((args.company, TARGET_TYPE_COMPANY))
    for name in employees:
        targets.append((name, TARGET_TYPE_PERSON))

    logger.info(
        f"Checking {len(domains)} domain(s), the company name, and "
        f"{len(employees)} employee name(s) via SpiderFoot "
        f"({SPIDERFOOT_MODULES})..."
    )
    # Reminder, not a blocker: if the dark-web module API keys/config were
    # added to .env after the last seed_spiderfoot_db.py run, re-run that
    # (container stopped) before this, or the modules may skip silently.
    log_api_status_summary()

    results: List[Dict[str, Any]] = []
    hit_count = 0

    for target, target_type in targets:
        events = query_spiderfoot_darkweb(target, target_type)

        mentions = [
            {
                "type": e.get("type"),
                "data": e.get("data"),
                "module_hint": SPIDERFOOT_MODULES,
                "triggering_target": target,
                "target_type": target_type,
            }
            for e in events
        ]

        entry = {
            "target": target,
            "target_type": target_type,
            "mentions": mentions,
            "modules_checked": SPIDERFOOT_MODULES.split(","),
        }
        if mentions:
            hit_count += 1

        results.append(entry)
        time.sleep(REQUEST_DELAY_SECONDS)

    save_json(output_file, results)
    print_summary(len(results), hit_count, output_file)


if __name__ == "__main__":
    main()
