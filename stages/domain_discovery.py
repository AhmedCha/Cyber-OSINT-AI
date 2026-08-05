#!/usr/bin/env python3
import argparse
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lib.common import setup_logging, slugify_company
from lib.config import log_api_status_summary
from lib.docker_runner import run_docker_tool
from lib.json_utils import load_json, save_json
from lib.network import is_valid_domain_syntax, normalize_domain, resolves_dns

logger = logging.getLogger(__name__)


# =====================================================================
# DOCKER TOOL EXECUTORS & PARSERS
# =====================================================================


def run_theharvester(target_domain: str) -> Dict[str, Set[str]]:
    logger.info(f"[{target_domain}] Running theHarvester (-b all)...")
    domains_found: Set[str] = set()

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd_args = [
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

        result = run_docker_tool(
            tool_name="theharvester",
            extra_args=cmd_args,
            timeout=450,
            target_identifier=target_domain,
        )

        output_file = Path(tmpdir) / "harvester_out.json"
        if not output_file.exists():
            output_file = Path(tmpdir) / "harvester_out"

        if output_file.exists():
            data = load_json(output_file)
            if data and isinstance(data, dict):
                for host in data.get("hosts", []):
                    if isinstance(host, str):
                        domains_found.add(host)
                    elif isinstance(host, dict) and "hostname" in host:
                        domains_found.add(host["hostname"])
        elif result is not None:
            logger.warning(
                f"[{target_domain}] theHarvester finished but no JSON output file was found."
            )

    return {"theHarvester": domains_found}


def run_spiderfoot(target_domain: str) -> Dict[str, Set[str]]:
    logger.info(
        f"[{target_domain}] Running SpiderFoot domain enrichment modules (sfp_dnsresolve,sfp_whois,sfp_crt)..."
    )

    cmd_args = [
        "spiderfoot",
        "sf.py",
        "-s",
        target_domain,
        "-m",
        "sfp_dnsresolve,sfp_whois,sfp_crt",
        "-o",
        "json",
        "-q",
    ]

    stdout_data = run_docker_tool(
        tool_name="spiderfoot",
        extra_args=cmd_args,
        timeout=300,
        target_identifier=target_domain,
        capture_stdout=True,
    )

    domains_found: Set[str] = set()
    if not stdout_data:
        return {}

    # Handle both List[str] and str return types from run_docker_tool safely
    if isinstance(stdout_data, list):
        raw_json_str = "\n".join(stdout_data)
        lines = stdout_data
    else:
        raw_json_str = stdout_data
        lines = stdout_data.splitlines()

    try:
        events = json.loads(raw_json_str)
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
        for line in lines:
            matches = re.findall(
                r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b",
                line.lower(),
            )
            domains_found.update(matches)

    return {"SpiderFoot": domains_found}


# =====================================================================
# PIPELINE HELPER FUNCTIONS
# =====================================================================


def parse_arguments() -> argparse.Namespace:
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
    return parser.parse_args()


def determine_company_slug(args: argparse.Namespace) -> str:
    if args.company:
        return slugify_company(args.company)
    if args.domain:
        return slugify_company(args.domain.split(".")[0])
    return "default"


def load_target_domains(
    args: argparse.Namespace, input_file: Path
) -> Optional[List[str]]:
    if args.domain:
        return [args.domain]

    if input_file.exists():
        logger.info(f"Reading candidate domains from Stage 1 output ({input_file})...")
        data = load_json(input_file)
        if isinstance(data, list):
            return [
                item["domain"]
                for item in data
                if isinstance(item, dict) and "domain" in item
            ]
        return None

    logger.error(
        f"No domain specified and {input_file} not found. Run name_to_domain.py first or pass --domain."
    )
    return None


def collect_candidates(
    target_domains: List[str],
) -> Tuple[Dict[str, List[str]], int]:
    domain_origins: Dict[str, List[str]] = {}
    raw_candidates_count = 0

    for target in target_domains:
        logger.info(f"--- Processing Candidate Domain: {target} ---")
        domain_origins.setdefault(target, []).append("Stage1-Root")

        tool_results: Dict[str, Set[str]] = {}
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

    return domain_origins, raw_candidates_count


def validate_candidates(domain_origins: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    logger.info("Validating discovered domains via DNS resolution...")
    validated_domains: List[Dict[str, Any]] = []

    for domain, sources in domain_origins.items():
        if resolves_dns(domain):
            validated_domains.append({"domain": domain, "sources": sorted(sources)})
        else:
            logger.debug(
                f"Domain '{domain}' failed DNS resolution. Discarding false positive."
            )

    validated_domains.sort(key=lambda x: x["domain"])
    return validated_domains


def print_summary(
    target_domains: List[str],
    raw_count: int,
    unique_count: int,
    validated_domains: List[Dict[str, Any]],
    output_file: Path,
) -> None:
    print("\n" + "=" * 50)
    print("           DOMAIN DISCOVERY SUMMARY")
    print("=" * 50)
    print(f"Target Root Domain(s)   : {', '.join(target_domains)}")
    print(f"Raw Candidates Found    : {raw_count}")
    print(f"Unique Candidate Domains: {unique_count}")
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


# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================


def main() -> None:
    setup_logging()
    args = parse_arguments()

    company_slug = determine_company_slug(args)
    output_dir = Path("output") / company_slug
    input_file = output_dir / "candidate_domains.json"
    output_file = output_dir / "domains.json"

    if not input_file.exists() and Path("output/candidate_domains.json").exists():
        input_file = Path("output/candidate_domains.json")

    target_domains = load_target_domains(args, input_file)
    if not target_domains:
        if target_domains is not None:
            logger.warning("No target domains found to process.")
        return

    log_api_status_summary()

    domain_origins, raw_candidates_count = collect_candidates(target_domains)
    unique_candidates_count = len(domain_origins)
    logger.info(
        f"Collected {raw_candidates_count} raw candidates ({unique_candidates_count} unique normalized domains)."
    )

    validated_domains = validate_candidates(domain_origins)

    output_dir.mkdir(parents=True, exist_ok=True)
    save_json(output_file, validated_domains, indent=2)

    print_summary(
        target_domains,
        raw_candidates_count,
        unique_candidates_count,
        validated_domains,
        output_file,
    )


if __name__ == "__main__":
    main()
