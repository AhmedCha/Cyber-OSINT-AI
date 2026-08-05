#!/usr/bin/env python3
import argparse
import json
import logging
import requests
from pathlib import Path
from typing import Any, Dict, List

from lib.common import setup_logging, slugify_company
from lib.json_utils import load_json, save_json

# Initialize module logger
logger = logging.getLogger(__name__)

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================


def parse_arguments() -> argparse.Namespace:
    """Parses command line arguments for the Email Validation stage."""
    parser = argparse.ArgumentParser(description="OSINT Stage: Email Asset Validation")
    parser.add_argument("--company", required=True, help="Target company name folder")
    return parser.parse_args()


def run_check_if_email_exists(email: str) -> Dict[str, Any]:
    """
    Validates an email address by querying the local reacherhq/backend HTTP service.
    Ensure the docker container is running: docker compose up -d reacher
    """
    logger.debug(f"Validating {email}...")
    result = {"status": "unknown", "confidence": 0.0, "is_catch_all": False}

    url = "http://localhost:8080/v0/check_email"
    payload = {"to_email": email}

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

        is_reachable = data.get("is_reachable", "unknown")

        # Check for catch-all according to reacher's JSON schema
        if data.get("smtp", {}).get("is_catch_all", False):
            result["is_catch_all"] = True

        if is_reachable == "safe":
            result["status"] = "deliverable"
            result["confidence"] = 1.0
        elif is_reachable == "risky":
            result["status"] = "risky"
            result["confidence"] = 0.5
        elif is_reachable == "invalid":
            result["status"] = "invalid"
            result["confidence"] = 0.0

    except requests.exceptions.ConnectionError:
        logger.error(
            f"Failed to connect to validation API for {email}. Is the reacher container running on port 8080?"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Validation request failed for {email}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error validating {email}: {e}")

    return result


def validate_emails(
    candidate_emails: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Processes a list of candidate emails and aggregates validation statistics."""
    validated_results = []
    stats = {"deliverable": 0, "risky": 0, "invalid": 0, "unknown": 0}

    for item in candidate_emails:
        email = item.get("email")
        if not email:
            continue

        validation = run_check_if_email_exists(email)

        # Merge validation evidence
        item["validation_status"] = validation["status"]
        item["confidence"] = validation["confidence"]
        item["is_catch_all"] = validation["is_catch_all"]

        stats[validation["status"]] += 1
        validated_results.append(item)

    # Sort by highest confidence first
    validated_results.sort(key=lambda x: x["confidence"], reverse=True)
    return validated_results, stats


def print_validation_summary(
    validated_count: int, stats: Dict[str, int], output_file: Path
) -> None:
    """Prints the console execution summary table for email validation."""
    print("\n" + "=" * 50)
    print("           EMAIL VALIDATION SUMMARY")
    print("=" * 50)
    print(f"Total Candidates Processed: {validated_count}")
    print("-" * 50)
    print(f"Deliverable               : {stats['deliverable']}")
    print(f"Risky (Catch-all/Greylist): {stats['risky']}")
    print(f"Invalid                   : {stats['invalid']}")
    print(f"Unknown (Timeout/Error)   : {stats['unknown']}")
    print("=" * 50)
    print(f"\nFinal output written to: {output_file.resolve()}")


# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================


def main() -> None:
    setup_logging()
    args = parse_arguments()

    company_slug = slugify_company(args.company)
    output_dir = Path("output") / company_slug

    input_file = output_dir / "candidate_emails.json"
    output_file = output_dir / "validated_emails.json"

    if not input_file.exists():
        logger.error(
            f"Input file {input_file} not found! Please run email_discovery.py first."
        )
        return

    candidate_emails = load_json(input_file)
    if not isinstance(candidate_emails, list):
        logger.error("Invalid input format. Expected a JSON list of candidate emails.")
        return

    logger.info(f"Loaded {len(candidate_emails)} candidates for validation.")

    # 1. Validation Execution
    validated_results, stats = validate_emails(candidate_emails)

    # 2. Write Final JSON Output
    save_json(output_file, validated_results)

    # 3. Console Summary
    print_validation_summary(len(validated_results), stats, output_file)


if __name__ == "__main__":
    main()
