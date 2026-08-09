#!/usr/bin/env python3
import os
import requests
import argparse
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from lib.common import setup_logging, slugify_company
from lib.docker_runner import run_docker_tool
from lib.json_utils import load_json, save_json
from lib.network import normalize_subdomain

logger = logging.getLogger(__name__)


# =====================================================================
# DOCKER TOOL EXECUTORS
# =====================================================================


def extract_subdomains(output_lines: List[str], root_domain: str) -> Set[str]:
    found = set()
    if not output_lines:
        return found
    for line in output_lines:
        clean = normalize_subdomain(line, root_domain)
        if clean:
            found.add(clean)
    return found


def run_certspotter(domain: str) -> Set[str]:
    logger.info(
        f"[{domain}] Querying CertSpotter API for Certificate Transparency subdomains..."
    )
    headers = {}
    key = os.getenv("CERTSPOTTER_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        resp = requests.get(
            "https://api.certspotter.com/v1/issuances",
            params={
                "domain": domain,
                "include_subdomains": "true",
                "expand": "dns_names",
            },
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        issuances = resp.json()
    except Exception as e:
        logger.error(f"[{domain}] CertSpotter API request failed: {e}")
        return set()

    found = set()
    for cert in issuances:
        for name in cert.get("dns_names", []):
            clean = normalize_subdomain(name, domain)
            if clean:
                found.add(clean)
    return found


def run_amass(domain: str) -> Set[str]:
    logger.info(f"[{domain}] Running Amass passive enumeration (-passive)...")
    lines = run_docker_tool(
        "amass", ["amass", "enum", "-passive", "-d", domain], timeout=600
    )
    if isinstance(lines, str):
        lines = lines.splitlines()
    return extract_subdomains(lines or [], domain)


# =====================================================================
# PIPELINE HELPER FUNCTIONS
# =====================================================================


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OSINT Stage: DNS Infrastructure & Subdomain Discovery"
    )
    parser.add_argument(
        "--company",
        required=False,
        help="Target company name for output folder organization",
    )
    return parser.parse_args()


def get_input_file(company_slug: str) -> Path:
    output_dir = Path("output") / company_slug
    input_file = output_dir / "domains.json"

    if not input_file.exists() and Path("output/domains.json").exists():
        return Path("output/domains.json")
    return input_file


def process_domain(parent_domain: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    logger.info(f"--- Processing Domain: {parent_domain} ---")

    certspotter_subs = run_certspotter(parent_domain)
    amass_subs = run_amass(parent_domain)

    subdomain_map: Dict[str, List[str]] = {}

    for sub in certspotter_subs:
        subdomain_map.setdefault(sub, []).append("CertSpotter")

    for sub in amass_subs:
        subdomain_map.setdefault(sub, []).append("Amass")

    formatted_subdomains = [
        {"subdomain": sub, "sources": sorted(list(set(tools)))}
        for sub, tools in sorted(subdomain_map.items())
    ]

    stats = {
        "total": len(formatted_subdomains),
        "certspotter": len(certspotter_subs),
        "amass": len(amass_subs),
    }

    return formatted_subdomains, stats


def print_summary(summary_stats: Dict[str, Dict[str, int]], output_file: Path) -> None:
    print("\n" + "=" * 65)
    print("               DNS INFRASTRUCTURE DISCOVERY SUMMARY")
    print("=" * 65)
    print(f"{'PARENT DOMAIN':<25} | {'TOTAL':<8} | {'CERTSPOTTER':<11} | {'AMASS'}")
    print("-" * 65)
    for domain, stats in summary_stats.items():
        print(
            f"{domain:<25} | {stats['total']:<8} | {stats['certspotter']:<11} | {stats['amass']}"
        )
    print("=" * 65)
    print(f"\nFinal output written to: {output_file.resolve()}")


# =====================================================================
# MAIN EXECUTION
# =====================================================================


def main() -> None:
    setup_logging()
    args = parse_arguments()

    company_slug = slugify_company(args.company) if args.company else "default"

    output_dir = Path("output") / company_slug
    input_file = get_input_file(company_slug)
    output_file = output_dir / "dns_infra.json"

    logger.info(
        "Starting DNS infrastructure & subdomain discovery pipeline (Stage 2)..."
    )

    if not input_file.exists():
        logger.error(
            f"Input file {input_file} not found! Please run domain_discovery.py first."
        )
        return

    validated_domains = load_json(input_file)
    if not validated_domains:
        logger.warning("No domains found in input file. Nothing to enumerate.")
        return

    infra_results: Dict[str, List[Dict[str, Any]]] = {}
    summary_stats: Dict[str, Dict[str, int]] = {}

    for item in validated_domains:
        parent_domain = item.get("domain")
        if not parent_domain:
            continue

        subdomains, stats = process_domain(parent_domain)
        infra_results[parent_domain] = subdomains
        summary_stats[parent_domain] = stats

    output_dir.mkdir(parents=True, exist_ok=True)
    save_json(output_file, infra_results, indent=2)

    print_summary(summary_stats, output_file)


if __name__ == "__main__":
    main()
