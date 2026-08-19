#!/usr/bin/env python3
import os
import requests
import json
import argparse
import logging
import time
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from lib.common import setup_logging, slugify_company
from lib.json_utils import load_json, save_json
from lib.network import normalize_subdomain
from lib.docker_runner import run_docker_tool
from lib.db import get_db_connection, upsert_records

logger = logging.getLogger(__name__)

# Global cache for IP enrichment to avoid re-querying across multiple domains
IP_ENRICHMENT_CACHE: Dict[str, Dict[str, Any]] = {}

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


def run_certspotter(domain: str) -> Tuple[List[str], Any]:
    """
    Fetches CertSpotter issuances.
    Returns: (list_of_subdomains, raw_certificate_records)
    """
    logger.info(f"[*] Running CertSpotter for {domain}...")
    url = f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names&expand=issuer"

    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            raw_records = response.json()
            subdomains: Set[str] = set()
            for cert in raw_records:
                for name in cert.get("dns_names", []):
                    # Clean potential wildcard prefixes
                    clean_name = name.replace("*.", "")
                    subdomains.add(clean_name)
            return list(subdomains), raw_records
        else:
            logger.error(
                f"[!] CertSpotter returned status code: {response.status_code}"
            )
            return [], []
    except Exception as e:
        logger.error(f"[!] CertSpotter error: {e}")
        return [], []


def enrich_ip_with_asn(ip: str) -> Dict[str, Any]:
    """
    Queries ip-api.com for ASN/ISP data to replace the missing Amass address data.
    Respects the 45 RPM limit for the free API tier.
    """
    if ip in IP_ENRICHMENT_CACHE:
        return IP_ENRICHMENT_CACHE[ip]

    url = f"http://ip-api.com/json/{ip}?fields=as,isp,org,query"
    try:
        # Enforce rate limit (~1.33 seconds per request ensures max 45 requests/minute)
        time.sleep(1.35)
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            result = {
                "asn": data.get("as"),
                "description": data.get("isp") or data.get("org"),
                "cidr": None,  # Note: The free ip-api tier does not provide native CIDR blocks
            }
            IP_ENRICHMENT_CACHE[ip] = result
            return result
        else:
            logger.error(
                f"[!] ip-api returned status code {response.status_code} for IP {ip}"
            )
    except Exception as e:
        logger.error(f"[!] Error enriching IP {ip}: {e}")

    # Fallback to empty state on failure
    failed_result = {"asn": None, "description": None, "cidr": None}
    IP_ENRICHMENT_CACHE[ip] = failed_result
    return failed_result


def run_amass(domain: str, temp_dir: str) -> Tuple[List[str], Any, Any]:
    abs_temp_dir = os.path.abspath(temp_dir)
    txt_output_filename = f"amass_{domain}.txt"
    txt_output_path = os.path.join(abs_temp_dir, txt_output_filename)

    cmd_args = [
        "-v",
        f"{abs_temp_dir}:/tmp",
        "amass",
        "enum",
        "-passive",
        "-d",
        domain,
        "-o",
        f"/tmp/{txt_output_filename}",
    ]

    logger.info(f"[*] Running Amass (passive) for {domain}...")

    result = run_docker_tool(
        tool_name="amass",
        extra_args=cmd_args,
        timeout=600,
        target_identifier=domain,
        capture_stdout=True,
    )

    if result is None:
        logger.error(f"[!] Amass run failed or timed out for {domain}.")

    subdomains: Set[str] = set()
    raw_data: List[Dict[str, str]] = []
    surfaced_network_info: Dict[str, Any] = {}

    # Standard Amass v4 graph relation output: sub.domain.com (FQDN) --> a_record --> 1.2.3.4 (IPAddress)
    relation_pattern = re.compile(
        r"^(.*?)\s+\(FQDN\)\s+-->\s+(a_record|aaaa_record)\s+-->\s+(.*?)\s+\(IPAddress\)"
    )

    if os.path.exists(txt_output_path):
        with open(txt_output_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                match = relation_pattern.search(line)
                if match:
                    hostname = match.group(1).strip()
                    record_type = match.group(2).strip()
                    ip_address = match.group(3).strip()

                    subdomains.add(hostname)

                    # Formulate an alternative raw record matching expectation
                    raw_data.append(
                        {
                            "hostname": hostname,
                            "record_type": record_type,
                            "ip": ip_address,
                            "raw_line": line,
                        }
                    )

                    # Accumulate IPs for later enrichment
                    if ip_address not in surfaced_network_info:
                        surfaced_network_info[ip_address] = {
                            "related_hostnames": [hostname]
                        }
                    elif (
                        hostname
                        not in surfaced_network_info[ip_address]["related_hostnames"]
                    ):
                        surfaced_network_info[ip_address]["related_hostnames"].append(
                            hostname
                        )
    else:
        logger.error(
            f"[!] No Amass output file found at {txt_output_path}. Execution may have failed."
        )

    # Enrich extracted IPs with external ASN/ISP context
    if surfaced_network_info:
        logger.info(
            f"[*] Enriching {len(surfaced_network_info)} unique IPs found by Amass..."
        )
        for ip, network_info in surfaced_network_info.items():
            enrichment = enrich_ip_with_asn(ip)
            network_info["asn"] = enrichment.get("asn")
            network_info["description"] = enrichment.get("description")
            network_info["cidr"] = enrichment.get("cidr")

    return list(subdomains), raw_data, surfaced_network_info


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


def process_domain(
    parent_domain: str, temp_dir: str
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, Any]]:
    logger.info(f"--- Processing Domain: {parent_domain} ---")

    certspotter_subs, cert_raw = run_certspotter(parent_domain)
    amass_subs, amass_raw, network_info = run_amass(parent_domain, temp_dir)

    subdomain_map: Dict[str, List[str]] = {}

    for sub in certspotter_subs:
        if sub not in subdomain_map:
            subdomain_map[sub] = []
        subdomain_map[sub].append("CertSpotter")

    for sub in amass_subs:
        if sub not in subdomain_map:
            subdomain_map[sub] = []
        subdomain_map[sub].append("Amass")

    formatted_subdomains = [
        {"subdomain": sub, "sources": sorted(list(set(tools)))}
        for sub, tools in sorted(subdomain_map.items())
    ]

    stats = {
        "total": len(formatted_subdomains),
        "certspotter": len(certspotter_subs),
        "amass": len(amass_subs),
    }

    raw_output = {
        "surfaced_network_footprint": network_info,
        "raw_amass_records": amass_raw,
        "raw_certspotter_issuances": cert_raw,
    }

    return formatted_subdomains, stats, raw_output


def print_summary(
    summary_stats: Dict[str, Dict[str, int]], output_file: Path, raw_output_file: Path
) -> None:
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
    print(f"Raw data preserved in: {raw_output_file.resolve()}")


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
    raw_output_file = output_dir / "dns_infra_raw.json"

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
    raw_results: Dict[str, Any] = {}
    summary_stats: Dict[str, Dict[str, int]] = {}

    output_dir.mkdir(parents=True, exist_ok=True)

    for item in validated_domains:
        parent_domain = item.get("domain")
        if not parent_domain:
            continue

        subdomains, stats, raw_data = process_domain(parent_domain, str(output_dir))

        infra_results[parent_domain] = subdomains
        summary_stats[parent_domain] = stats
        raw_results[parent_domain] = raw_data

    # Save both consolidated subdomains and raw network/tool data
    save_json(output_file, infra_results, indent=2)
    save_json(raw_output_file, raw_results, indent=2)

    # Reshape raw_results into standardized records for raw_dns_infra tier
    db_records = [
        {"domain": domain, **raw_data} for domain, raw_data in raw_results.items()
    ]

    try:
        conn = get_db_connection()
        try:
            upsert_records(conn, "raw_dns_infra", company_slug, db_records, "domain")
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Database sync failed for raw_dns_infra: {e}")

    print_summary(summary_stats, output_file, raw_output_file)


if __name__ == "__main__":
    main()
