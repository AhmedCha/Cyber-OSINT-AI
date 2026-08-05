#!/usr/bin/env python3
import argparse
import json
import logging
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Set

# =====================================================================
# CONFIGURATION & LOGGING SETUP
# =====================================================================

CONFIG_FILE = Path("config/api_status.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================


def slugify_company(name: str) -> str:
    """Lowercase, replace non-alphanumeric runs with a single hyphen, strip leading/trailing hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def log_api_status_summary():
    """Logs an informational warning about missing API keys without blocking execution."""
    if not CONFIG_FILE.exists():
        logger.info(
            f"No API config file found at {CONFIG_FILE}. Tools will rely on built-in fallbacks."
        )
        return

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        missing_keys = list(data.get("missing", {}).keys())
        if missing_keys:
            logger.info(
                f"Note: {len(missing_keys)} API keys are marked as missing in api_status.json. "
                f"Tools will automatically skip or degrade functionality for those sources."
            )
    except Exception as e:
        logger.warning(f"Could not read {CONFIG_FILE} for reporting: {e}")


def normalize_domain(raw_domain: str) -> str:
    """Strips protocols, www prefixes, trailing slashes, and lowercases domain strings."""
    domain = raw_domain.strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain)
    domain = domain.split("/")[0].split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.rstrip(". /")


def is_valid_domain_syntax(domain: str) -> bool:
    """Basic regex validation for domain name syntax."""
    pattern = r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
    return bool(re.match(pattern, domain))


def resolves_dns(domain: str) -> bool:
    """Performs a simple DNS lookup to confirm the domain resolves."""
    try:
        socket.gethostbyname(domain)
        return True
    except (socket.gaierror, socket.timeout):
        return False


# =====================================================================
# DOCKER TOOL EXECUTORS & PARSERS
# =====================================================================


def run_theharvester(target_domain: str) -> Dict[str, Set[str]]:
    """Runs theHarvester against a target DOMAIN using '-b all'.

    Uses a temporary directory bind mount so the output file persists after '--rm'.
    """
    logger.info(f"[{target_domain}] Running theHarvester (-b all)...")
    domains_found = set()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Mount host tmpdir to /tmp inside the container to capture the file
        cmd = [
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
        ]

        try:
            subprocess.run(
                cmd, capture_output=True, text=True, timeout=450, check=False
            )

            # Check both possible file names (.json extension is added by theHarvester automatically)
            output_file = Path(tmpdir) / "harvester_out.json"
            if not output_file.exists():
                output_file = Path(tmpdir) / "harvester_out"

            if output_file.exists():
                with open(output_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for host in data.get("hosts", []):
                    if isinstance(host, str):
                        domains_found.add(host)
                    elif isinstance(host, dict) and "hostname" in host:
                        domains_found.add(host["hostname"])
            else:
                logger.warning(
                    f"[{target_domain}] theHarvester finished but no JSON output file was found."
                )

        except subprocess.TimeoutExpired:
            logger.error(
                f"[{target_domain}] theHarvester container timed out (450s). Continuing pipeline."
            )
        except Exception as e:
            logger.error(
                f"[{target_domain}] Error executing or parsing theHarvester: {e}"
            )

    return {"theHarvester": domains_found}


def run_spiderfoot(target_domain: str) -> Dict[str, Set[str]]:
    """Runs SpiderFoot domain-oriented modules (dnsresolve, whois, crt) against a target DOMAIN."""
    logger.info(
        f"[{target_domain}] Running SpiderFoot domain enrichment modules (sfp_dnsresolve,sfp_whois,sfp_crt)..."
    )

    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "spiderfoot",
        "-s",
        target_domain,
        "-m",
        "sfp_dnsresolve,sfp_whois,sfp_crt",
        "-o",
        "json",
        "-q",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
        domains_found = set()

        try:
            events = json.loads(result.stdout)
            for event in events:
                event_type = event.get("type", "")
                data = event.get("data", "")
                if event_type in [
                    "DOMAIN_NAME",
                    "INTERNET_NAME",
                    "CO_HOSTED_SITE",
                    "SIMILAR_DOMAIN",
                ]:
                    domains_found.add(data)
        except json.JSONDecodeError:
            for line in result.stdout.splitlines():
                matches = re.findall(
                    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b",
                    line.lower(),
                )
                domains_found.update(matches)

        return {"SpiderFoot": domains_found}

    except subprocess.TimeoutExpired:
        logger.error(
            f"[{target_domain}] SpiderFoot container timed out (300s). Continuing pipeline."
        )
    except Exception as e:
        logger.error(f"[{target_domain}] Error executing or parsing SpiderFoot: {e}")

    return {}


# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================


def main():
    parser = argparse.ArgumentParser(
        description="OSINT Stage 2: Domain Discovery & Enrichment"
    )
    parser.add_argument(
        "--domain",
        required=False,
        help="Direct target domain override (e.g. 'example.com')",
    )
    parser.add_argument(
        "--company",
        required=False,
        help="Target company name for output folder organization",
    )
    args = parser.parse_args()

    if args.company:
        company_slug = slugify_company(args.company)
    elif args.domain:
        company_slug = slugify_company(args.domain.split(".")[0])
    else:
        company_slug = "default"

    output_dir = Path("output") / company_slug
    input_file = output_dir / "candidate_domains.json"
    output_file = output_dir / "domains.json"

    if not input_file.exists() and Path("output/candidate_domains.json").exists():
        input_file = Path("output/candidate_domains.json")

    # 1. Resolve Target Domain(s)
    target_domains = []
    if args.domain:
        target_domains = [args.domain]
    elif input_file.exists():
        logger.info(f"Reading candidate domains from Stage 1 output ({input_file})...")
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                target_domains = [item["domain"] for item in data if "domain" in item]
        except Exception as e:
            logger.error(f"Failed to read {input_file}: {e}")
            return
    else:
        logger.error(
            f"No domain specified and {input_file} not found. Run name_to_domain.py first or pass --domain."
        )
        return

    if not target_domains:
        logger.warning("No target domains found to process.")
        return

    # 2. Log API status summary for reporting
    log_api_status_summary()

    # 3. Run Tools & Collect Outputs across all target domains
    domain_origins: Dict[str, List[str]] = {}
    raw_candidates_count = 0

    for target in target_domains:
        logger.info(f"--- Processing Candidate Domain: {target} ---")
        # Ensure the root domain itself is included in candidates
        domain_origins.setdefault(target, []).append("Stage1-Root")

        tool_results = {}
        tool_results.update(run_theharvester(target))
        tool_results.update(run_spiderfoot(target))

        for tool_name, domains in tool_results.items():
            for raw_domain in domains:
                raw_candidates_count += 1
                norm_domain = normalize_domain(raw_domain)

                if is_valid_domain_syntax(norm_domain):
                    if norm_domain not in domain_origins:
                        domain_origins[norm_domain] = []
                    if tool_name not in domain_origins[norm_domain]:
                        domain_origins[norm_domain].append(tool_name)

    unique_candidates_count = len(domain_origins)
    logger.info(
        f"Collected {raw_candidates_count} raw candidates ({unique_candidates_count} unique normalized domains)."
    )

    # 4. DNS Resolution Validation
    logger.info("Validating discovered domains via DNS resolution...")
    validated_domains = []

    for domain, sources in domain_origins.items():
        if resolves_dns(domain):
            validated_domains.append({"domain": domain, "sources": sorted(sources)})
        else:
            logger.debug(
                f"Domain '{domain}' failed DNS resolution. Discarding false positive."
            )

    validated_domains.sort(key=lambda x: x["domain"])

    # 5. Write Final JSON Output
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(validated_domains, f, indent=2)

    # 6. Console Summary
    print("\n" + "=" * 50)
    print("           DOMAIN DISCOVERY SUMMARY")
    print("=" * 50)
    print(f"Target Root Domain(s)   : {', '.join(target_domains)}")
    print(f"Raw Candidates Found    : {raw_candidates_count}")
    print(f"Unique Candidate Domains: {unique_candidates_count}")
    print(f"Validated (DNS Passed)  : {len(validated_domains)}")
    print("-" * 50)
    if validated_domains:
        print(f"{'DOMAIN':<30} | {'FOUND BY'}")
        print("-" * 50)
        for item in validated_domains:
            print(f"{item['domain']:<30} | {', '.join(item['sources'])}")
    else:
        print("No resolving domains were discovered.")
    print("=" * 50)
    print(f"\nFinal output written to: {output_file.resolve()}")


if __name__ == "__main__":
    main()
