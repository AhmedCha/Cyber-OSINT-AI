#!/usr/bin/env python3
import argparse
import json
import logging
import re
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Set

# =====================================================================
# CONFIGURATION & LOGGING SETUP
# =====================================================================

SEARXNG_URL = "http://localhost:8080/search"
OUTPUT_DIR = Path("output")
OUTPUT_FILE = OUTPUT_DIR / "candidate_domains.json"

# Common non-company platforms to exclude from search results
DENYLIST_DOMAINS = (
    "linkedin.com",
    "wikipedia.org",
    "facebook.com",
    "twitter.com",
    "x.com",
    "crunchbase.com",
    "bloomberg.com",
    "glassdoor.com",
    "indeed.com",
    "youtube.com",
    "instagram.com",
    "microsoft.com",
    "office.com",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================


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


def domain_matches_company(domain: str, company_name: str) -> bool:
    """
    Checks if the domain logically matches the company name by comparing alphanumeric structures.
    """
    # Remove the TLD for stricter comparison (e.g., 'example-corp.com' -> 'example-corp')
    domain_no_tld = domain.rsplit(".", 1)[0] if "." in domain else domain

    # Strip all non-alphanumeric characters (dashes, underscores) from the domain
    domain_alphanum = re.sub(r"[^a-z0-9]", "", domain_no_tld)

    # 1. Clean the target company name (e.g., "Example-Corp" -> "examplecorp")
    company_clean = re.sub(r"[^a-z0-9]", "", company_name.lower())

    # 2. Extract significant individual words (e.g., ["example", "corp"])
    words = [w for w in re.split(r"[^a-z0-9]", company_name.lower()) if len(w) > 2]

    # Check A: Does the concatenated company name appear in the domain?
    if company_clean and company_clean in domain_alphanum:
        return True

    # Check B: Does the primary (first) significant word of the company appear in the domain?
    if words and words[0] in domain_alphanum:
        return True

    return False


# =====================================================================
# SEARCH SOURCE EXECUTORS
# =====================================================================


def search_searxng(company_name: str) -> Set[str]:
    """Queries a local SearXNG instance for the company's official website/domain."""
    logger.info(f"Querying local SearXNG ({SEARXNG_URL}) for '{company_name}'...")
    domains = set()
    try:
        params = urllib.parse.urlencode(
            {"q": f"{company_name} official site", "format": "json"}
        )
        url = f"{SEARXNG_URL}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "OSINT-Pipeline/1.0"})

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        for result in data.get("results", [])[
            :10
        ]:  # Expanded to top 10 since we are filtering strictly
            url_str = result.get("url", "")
            if url_str:
                norm = normalize_domain(url_str)

                # Filter 1: Valid syntax and not in our denylist of social/aggregator platforms
                if is_valid_domain_syntax(norm) and not norm.endswith(DENYLIST_DOMAINS):
                    # Filter 2: The lexical structure of the domain must match the company name
                    if domain_matches_company(norm, company_name):
                        domains.add(norm)
                    else:
                        logger.debug(
                            f"Discarding '{norm}' - does not lexically match '{company_name}'"
                        )

    except Exception as e:
        logger.error(
            f"SearXNG search failed: {e}. Ensure SearXNG is running at {SEARXNG_URL} with JSON format enabled."
        )

    return domains


# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================


def main():
    parser = argparse.ArgumentParser(
        description="OSINT Stage 1: Name-to-Domain Candidate Discovery"
    )
    parser.add_argument(
        "--company",
        required=True,
        help="Target company name (e.g. 'Example Corp' or 'Example-Corp')",
    )
    args = parser.parse_args()

    company_name = args.company
    logger.info(
        f"Starting name-to-domain candidate discovery for company: {company_name}"
    )

    domain_origins: Dict[str, List[str]] = {}

    # 1. Query sources
    searxng_domains = search_searxng(company_name)
    for d in searxng_domains:
        domain_origins.setdefault(d, []).append("SearXNG")

    logger.info(
        f"Collected {len(domain_origins)} lexically validated candidate domains."
    )

    # 2. DNS Validation
    logger.info("Validating candidate domains via DNS resolution...")
    validated_domains = []

    for domain, sources in domain_origins.items():
        if resolves_dns(domain):
            validated_domains.append({"domain": domain, "sources": sorted(sources)})
        else:
            logger.debug(
                f"Candidate '{domain}' failed DNS resolution. Discarding false positive."
            )

    validated_domains.sort(key=lambda x: x["domain"])

    # 3. Save Output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(validated_domains, f, indent=2)

    # 4. Console Summary
    print("\n" + "=" * 50)
    print("      NAME-TO-DOMAIN DISCOVERY SUMMARY")
    print("=" * 50)
    print(f"Target Company           : {company_name}")
    print(f"Candidates Found         : {len(domain_origins)}")
    print(f"Validated Root Domains   : {len(validated_domains)}")
    print("-" * 50)
    if validated_domains:
        print(f"{'DOMAIN':<30} | {'FOUND BY'}")
        print("-" * 50)
        for item in validated_domains:
            print(f"{item['domain']:<30} | {', '.join(item['sources'])}")
    else:
        print("No resolving root domains were discovered.")
    print("=" * 50)
    print(f"\nCandidate domain list written to: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
