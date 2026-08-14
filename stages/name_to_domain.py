#!/usr/bin/env python3
import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Set

# Hypothetical shared library imports to prepare for pipeline modularity
from lib.common import setup_logging, slugify_company, generate_company_abbreviation
from lib.json_utils import save_json
from lib.network import (
    domain_matches_company,
    is_valid_domain_syntax,
    normalize_domain,
    resolves_dns,
)
from lib.search import query_searxng
from lib.db import get_db_connection, upsert_records

# Constants
SEARXNG_URL = "http://localhost:8080"

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

# Initialize logger for this module
logger = logging.getLogger(__name__)


# =====================================================================
# SEARCH SOURCE EXECUTORS
# =====================================================================


def search_searxng(company_name: str, abbreviation: str = "") -> Set[str]:
    """Queries a local SearXNG instance for the company's official website/domain."""
    logger.info(f"Querying local SearXNG ({SEARXNG_URL}) for '{company_name}'...")
    domains: Set[str] = set()

    search_results = query_searxng(
        base_url=SEARXNG_URL,
        query=f"{company_name} official site",
        limit=10,
    )

    if not search_results:
        return domains

    for result in search_results:
        url_str = result.get("url", "")
        if not url_str:
            continue

        norm = normalize_domain(url_str)

        if not is_valid_domain_syntax(norm) or norm.endswith(DENYLIST_DOMAINS):
            continue

        # Pass abbreviation down to domain_matches_company
        if domain_matches_company(norm, company_name, abbreviation):
            domains.add(norm)
        else:
            logger.debug(
                f"Discarding '{norm}' - does not lexically match '{company_name}' or '{abbreviation}'"
            )

    return domains


# =====================================================================
# PIPELINE HELPER FUNCTIONS
# =====================================================================


def parse_arguments() -> argparse.Namespace:
    """Parses command line arguments for Stage 1."""
    parser = argparse.ArgumentParser(
        description="OSINT Stage 1: Name-to-Domain Candidate Discovery"
    )
    parser.add_argument(
        "--company",
        required=True,
        help="Target company name (e.g. 'Example Corp' or 'Example-Corp')",
    )
    # Added optional abbreviation CLI argument
    parser.add_argument(
        "--abbreviation",
        required=False,
        help="Optional explicitly-supplied abbreviation (e.g. 'BTS'). Overrides auto-generation.",
    )
    return parser.parse_args()


def validate_candidates(domain_origins: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Validates candidate domains via DNS lookup."""
    logger.info("Validating candidate domains via DNS resolution...")
    validated_domains: List[Dict[str, Any]] = []

    for domain, sources in domain_origins.items():
        if resolves_dns(domain):
            validated_domains.append({"domain": domain, "sources": sorted(sources)})
        else:
            logger.debug(
                f"Candidate '{domain}' failed DNS resolution. Discarding false positive."
            )

    validated_domains.sort(key=lambda x: x["domain"])
    return validated_domains


def print_summary(
    company_name: str,
    candidates_count: int,
    validated_domains: List[Dict[str, Any]],
    output_file: Path,
) -> None:
    """Prints the console execution summary table."""
    print("\n" + "=" * 50)
    print("      NAME-TO-DOMAIN DISCOVERY SUMMARY")
    print("=" * 50)
    print(f"Target Company           : {company_name}")
    print(f"Candidates Found         : {candidates_count}")
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
    print(f"\nCandidate domain list written to: {output_file.resolve()}")


# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================


def main() -> None:
    setup_logging()
    args = parse_arguments()

    company_name = args.company
    company_slug = slugify_company(company_name)

    # Auto-derive abbreviation if not explicitly supplied
    abbreviation = (
        args.abbreviation
        if args.abbreviation
        else generate_company_abbreviation(company_name)
    )

    output_dir = Path("output") / company_slug
    output_file = output_dir / "candidate_domains.json"

    logger.info(
        f"Starting name-to-domain candidate discovery for company: {company_name} (Abbrev: {abbreviation})"
    )

    domain_origins: Dict[str, List[str]] = {}

    # Query sources (passing the abbreviation)
    searxng_domains = search_searxng(company_name, abbreviation)
    for d in searxng_domains:
        domain_origins.setdefault(d, []).append("SearXNG")

    logger.info(
        f"Collected {len(domain_origins)} lexically validated candidate domains."
    )

    # DNS Validation
    validated_domains = validate_candidates(domain_origins)

    # Save Output
    output_dir.mkdir(parents=True, exist_ok=True)
    save_json(output_file, validated_domains, indent=2)

    try:
        with get_db_connection() as conn:
            upsert_records(
                conn, "raw_domains", company_slug, validated_domains, "domain"
            )
    except Exception as e:
        logger.warning(f"Database sync failed for raw_domains: {e}")

    # Console Summary
    print_summary(
        company_name,
        len(domain_origins),
        validated_domains,
        output_file,
    )


if __name__ == "__main__":
    main()
