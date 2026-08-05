#!/usr/bin/env python3
import argparse
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from lib.common import setup_logging, slugify_company
from lib.json_utils import load_json, save_json
from lib.email_normalizer import is_valid_email, normalize_email
from lib.email_patterns import deduce_patterns, generate_candidate_emails

logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OSINT Stage: Email Asset Discovery")
    parser.add_argument("--company", required=True, help="Target company name folder")
    return parser.parse_args()


def run_theharvester_emails(target_domain: str) -> Set[str]:
    logger.info(f"[{target_domain}] Running theHarvester for emails...")
    emails: Set[str] = set()

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    "-v",
                    f"{tmpdir}:/tmp",
                    "theharvester",
                    "-d",
                    target_domain,
                    "-b",
                    "all",
                    "-f",
                    "/tmp/harvester_out",
                ],
                capture_output=True,
                text=True,
                timeout=450,
                check=False,
            )

            output_file = Path(tmpdir) / "harvester_out.json"
            if not output_file.exists():
                output_file = Path(tmpdir) / "harvester_out"

            if output_file.exists():
                data = load_json(output_file)
                if isinstance(data, dict):
                    for email in data.get("emails", []):
                        emails.add(normalize_email(email))
        except subprocess.TimeoutExpired:
            logger.error(f"[{target_domain}] theHarvester execution timed out.")
        except Exception as e:
            logger.error(f"[{target_domain}] Failed to parse theHarvester output: {e}")

    return emails


def run_spiderfoot_emails(target_domain: str) -> Set[str]:
    # Expanded SpiderFoot modules for deeper email enumeration
    modules = "sfp_email,sfp_hunter,sfp_skymem,sfp_clearbit,sfp_github_code,sfp_spider"
    logger.info(f"[{target_domain}] Running SpiderFoot for emails ({modules})...")
    emails: Set[str] = set()

    try:
        process = subprocess.run(
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "spiderfoot",
                "-s",
                target_domain,
                "-m",
                modules,
                "-o",
                "json",
                "-q",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

        for line in process.stdout.splitlines():
            try:
                event = json.loads(line)
                if event.get("type") == "EMAILADDR":
                    emails.add(normalize_email(event.get("data", "")))
            except json.JSONDecodeError:
                matches = re.findall(
                    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", line
                )
                for m in matches:
                    emails.add(normalize_email(m))

    except subprocess.TimeoutExpired:
        logger.error(f"[{target_domain}] SpiderFoot execution timed out.")
    except Exception as e:
        logger.error(f"[{target_domain}] Unexpected error running SpiderFoot: {e}")

    return set(e for e in emails if e.endswith(f"@{target_domain}"))


def discover_active_emails(
    target_domains: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], Set[str]]:
    email_inventory: Dict[str, Dict[str, Any]] = {}
    discovered_emails: Set[str] = set()

    for domain in target_domains:
        logger.info(f"--- Processing Domain for Emails: {domain} ---")

        th_emails = run_theharvester_emails(domain)
        sf_emails = run_spiderfoot_emails(domain)

        for email in th_emails:
            if is_valid_email(email):
                if email not in email_inventory:
                    email_inventory[email] = {"email": email, "sources": []}
                email_inventory[email]["sources"].append("theHarvester")
                discovered_emails.add(email)

        for email in sf_emails:
            if is_valid_email(email):
                if email not in email_inventory:
                    email_inventory[email] = {"email": email, "sources": []}
                if "SpiderFoot" not in email_inventory[email]["sources"]:
                    email_inventory[email]["sources"].append("SpiderFoot")
                discovered_emails.add(email)

    return email_inventory, discovered_emails


def apply_inferred_patterns(
    employees: List[Dict[str, Any]],
    target_domains: List[str],
    discovered_emails: Set[str],
    email_inventory: Dict[str, Dict[str, Any]],
) -> None:
    if not employees:
        return

    logger.info("Applying inferred patterns to employee inventory...")
    patterns = deduce_patterns(discovered_emails, target_domains)
    inferred = generate_candidate_emails(employees, target_domains, patterns)

    for item in inferred:
        email = item.get("email")
        if email and is_valid_email(email):
            if email not in email_inventory:
                email_inventory[email] = item
            else:
                if "Inferred-Pattern" not in email_inventory[email]["sources"]:
                    email_inventory[email]["sources"].append("Inferred-Pattern")
                email_inventory[email]["employee"] = item.get("employee")


def print_discovery_summary(
    target_count: int, employee_count: int, candidate_count: int, output_file: Path
) -> None:
    print("\n" + "=" * 65)
    print("               EMAIL ASSET DISCOVERY SUMMARY")
    print("=" * 65)
    print(f"Target Domains Processed : {target_count}")
    print(f"Employees Processed      : {employee_count}")
    print(f"Total Candidates Found   : {candidate_count}")
    print("=" * 65)
    print(f"\nFinal output written to: {output_file.resolve()}")


def main() -> None:
    setup_logging()
    args = parse_arguments()

    company_slug = slugify_company(args.company)
    output_dir = Path("output") / company_slug

    domains_file = output_dir / "domains.json"
    employees_file = output_dir / "employees.json"
    output_file = output_dir / "candidate_emails.json"

    if not domains_file.exists():
        logger.error(f"Missing {domains_file}. Please run domain discovery first.")
        return

    domains_data = load_json(domains_file)
    target_domains = [
        d["domain"] for d in domains_data if isinstance(d, dict) and "domain" in d
    ]

    employees = load_json(employees_file)
    if not isinstance(employees, list) or not employees:
        logger.warning(
            f"{employees_file} missing or empty. Skipping employee pattern inference."
        )
        employees = []

    email_inventory, discovered_emails = discover_active_emails(target_domains)
    apply_inferred_patterns(
        employees, target_domains, discovered_emails, email_inventory
    )

    final_emails = []
    for email, data in email_inventory.items():
        data["sources"] = sorted(list(set(data.get("sources", []))))
        final_emails.append(data)

    final_emails.sort(key=lambda x: x["email"])
    save_json(output_file, final_emails)
    print_discovery_summary(
        len(target_domains), len(employees), len(final_emails), output_file
    )


if __name__ == "__main__":
    main()
