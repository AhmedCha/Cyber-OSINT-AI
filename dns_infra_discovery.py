#!/usr/bin/env python3
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set

# =====================================================================
# CONFIGURATION & LOGGING SETUP
# =====================================================================

INPUT_FILE = Path("output/domains.json")
OUTPUT_DIR = Path("output")
OUTPUT_FILE = OUTPUT_DIR / "dns_infra.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================


def normalize_subdomain(raw_subdomain: str, root_domain: str) -> str:
    """Strips protocols, lowercases, and ensures the string belongs to the root domain."""
    sub = raw_subdomain.strip().lower()
    sub = re.sub(r"^[a-z]+://", "", sub)
    sub = sub.split("/")[0].split(":")[0].rstrip(". /")

    # Check that it's actually a subdomain or match of the root domain
    if sub == root_domain or sub.endswith(f".{root_domain}"):
        return sub
    return ""


# =====================================================================
# DOCKER TOOL EXECUTORS & PARSERS
# =====================================================================


def run_certspotter(domain: str) -> Set[str]:
    """Runs CertSpotter CLI against a target domain via Docker."""
    logger.info(
        f"[{domain}] Running CertSpotter for Certificate Transparency subdomains..."
    )

    cmd = ["docker", "compose", "run", "--rm", "certspotter", "-domain", domain]

    found = set()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
        for line in result.stdout.splitlines():
            clean = normalize_subdomain(line, domain)
            if clean:
                found.add(clean)
    except subprocess.TimeoutExpired:
        logger.error(
            f"[{domain}] CertSpotter container timed out (300s). Continuing pipeline."
        )
    except Exception as e:
        logger.error(f"[{domain}] Error executing or parsing CertSpotter: {e}")

    return found


def run_amass(domain: str) -> Set[str]:
    """Runs Amass passive enumeration against a target domain via Docker."""
    logger.info(f"[{domain}] Running Amass passive enumeration (-passive)...")

    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "amass",
        "enum",
        "-passive",
        "-d",
        domain,
    ]

    found = set()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, check=False
        )
        for line in result.stdout.splitlines():
            clean = normalize_subdomain(line, domain)
            if clean:
                found.add(clean)
    except subprocess.TimeoutExpired:
        logger.error(
            f"[{domain}] Amass container timed out (600s). Continuing pipeline."
        )
    except Exception as e:
        logger.error(f"[{domain}] Error executing or parsing Amass: {e}")

    return found


# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================


def main():
    logger.info(
        "Starting DNS infrastructure & subdomain discovery pipeline (Stage 2)..."
    )

    # 1. Read input from Stage 1
    if not INPUT_FILE.exists():
        logger.error(
            f"Input file {INPUT_FILE} not found! Please run domain_discovery.py first."
        )
        return

    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            validated_domains = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read or parse {INPUT_FILE}: {e}")
        return

    if not validated_domains:
        logger.warning("No domains found in input file. Nothing to enumerate.")
        return

    # 2. Iterate through each parent domain and collect subdomains
    infra_results: Dict[str, List[Dict[str, any]]] = {}
    summary_stats: Dict[str, Dict[str, int]] = {}

    for item in validated_domains:
        parent_domain = item["domain"]
        logger.info(f"--- Processing Domain: {parent_domain} ---")

        certspotter_subs = run_certspotter(parent_domain)
        amass_subs = run_amass(parent_domain)

        # Merge, deduplicate, and tag
        subdomain_map: Dict[str, List[str]] = {}

        for sub in certspotter_subs:
            subdomain_map.setdefault(sub, []).append("CertSpotter")

        for sub in amass_subs:
            subdomain_map.setdefault(sub, []).append("Amass")

        # Format output per domain
        formatted_subdomains = []
        for sub, tools in sorted(subdomain_map.items()):
            formatted_subdomains.append(
                {"subdomain": sub, "sources": sorted(list(set(tools)))}
            )

        infra_results[parent_domain] = formatted_subdomains
        summary_stats[parent_domain] = {
            "total": len(formatted_subdomains),
            "certspotter": len(certspotter_subs),
            "amass": len(amass_subs),
        }

    # 3. Write Final JSON Output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(infra_results, f, indent=2)

    # 4. Console Summary
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
    print(f"\nFinal output written to: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
