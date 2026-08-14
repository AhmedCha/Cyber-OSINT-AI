#!/usr/bin/env python3
import argparse
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# Shared library imports
from lib.common import setup_logging, slugify_company, strip_accents
from lib.config import load_env_file
from lib.docker_runner import run_docker_service_up, run_docker_tool
from lib.json_utils import load_json, save_json
from lib.search import generate_search_query_variants
from lib.apify_utils import run_apify_actor
from lib.db import get_db_connection, upsert_records

# =====================================================================
# CONFIGURATION & CONSTANTS
# =====================================================================

ENV_FILE = Path(".env")

DEFAULT_APIFY_ACTOR_ID = "qXMa8kADnUQdmz18G"
DEFAULT_MAX_ITEMS_PER_DOMAIN = 50

USERNAME_PATTERN_NAMES = ["firstlast", "first.last", "flast"]

JINA_READER_BASE = "https://r.jina.ai/"
JINA_TIMEOUT_SECS = 20

INVALID_PAGE_MARKERS = [
    # --- Generic markers ---
    "404",
    "page not found",
    "user not found",
    "account not found",
    "profile not found",
    "content not found",
    "sorry, this page",
    "page you're looking for",
    "page you are looking for",
    "doesn't exist",
    "does not exist",
    "no longer available",
    "no longer exists",
    "couldn't find",
    "could not find this page",
    "account suspended",
    "this account doesn't exist",
    # --- Instagram ---
    "sorry, this page isn't available",
    "the link you followed may be broken",
    "the page may have been removed",
    # --- Twitter / X ---
    "this account doesn't exist",
    "this profile doesn't exist",
    "caution: this account is temporarily restricted",
    "account has been suspended",
    "this page doesn't exist. try searching for something else",
    # --- Facebook ---
    "this content isn't available right now",
    "this content isn't available at the moment",
    "the page you requested cannot be displayed",
    "the link may be broken, or the page may have been removed",
    # --- TikTok ---
    "couldn't find this account",
    "this account is private",
    "this user's videos are currently unavailable",
    # --- LinkedIn ---
    "this linkedin page doesn't exist",
    "the profile you're looking for is no longer available",
    "profile can't be displayed",
    # --- Reddit ---
    "sorry, nobody on reddit goes by that name",
    "page not found. the page you requested does not exist",
    "this community has been banned",
    "this community has been set to private",
    # --- YouTube ---
    "this channel doesn't exist",
    "this page isn't available",
    "404 error. this page isn't available",
    # --- Pinterest ---
    "we couldn't find those pins",
    "this page isn't available",
    "the user may have deleted their account",
    # --- Snapchat ---
    "sorry, that page doesn't exist",
    "the snapchat account you're looking for can't be found",
    # --- Telegram ---
    "if you have telegram, you can contact",
    "channel not found",
    # --- Threads ---
    "sorry, this page isn't available",
    "the link may be broken, or the profile may have been removed",
    # --- Discord ---
    "instant invite invalid",
    "this invite may be expired, or you might not have permission",
    # --- GitHub ---
    "not found",
    "this is not the web page you are looking for",
    # --- Tumblr ---
    "there's nothing here",
    "this tumblr doesn't exist",
    # --- VK ---
    "this page is blocked",
    "this page has been deleted or has not been created",
    "user not found",
]

DEFAULT_POPULAR_SITES = [
    "Instagram",
    "Twitter",
    "Facebook",
    "TikTok",
    "LinkedIn",
    "Reddit",
    "YouTube",
    "Pinterest",
    "Snapchat",
    "Telegram",
    "Threads",
    "Discord",
    "VK",
    "Tumblr",
    "GitHub",
]

MAIGRET_PROXY_ENV_VAR = "MAIGRET_PROXY_URL"
MAIGRET_TOR_PROXY_ENV_VAR = "MAIGRET_TOR_PROXY_URL"

# Initialize module logger
logger = logging.getLogger(__name__)

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================


def split_name(
    first_name: Optional[str], last_name: Optional[str], full_name: Optional[str]
) -> Optional[Dict[str, str]]:
    """Prefers the actor's own firstName/lastName fields; falls back to
    splitting the display name. Returns None if nothing usable is found."""

    def clean(part: str) -> str:
        part = strip_accents(part).lower()
        return re.sub(r"[^a-z-]", "", part)

    if first_name and last_name and last_name.lower() != "undefined":
        first, last = clean(first_name), clean(last_name)
        if first and last:
            return {"first": first, "last": last}

    if not full_name or not full_name.strip():
        return None

    cleaned = strip_accents(full_name).lower()
    cleaned = re.sub(r"[^a-z\s-]", "", cleaned)
    parts = [p for p in cleaned.split() if p]
    if len(parts) < 2:
        return None

    return {"first": parts[0], "last": parts[-1]}


def generate_username_candidates(
    first_name: Optional[str], last_name: Optional[str], full_name: Optional[str]
) -> Dict[str, str]:
    """Derives a handful of likely username variants from a person's name."""
    name_parts = split_name(first_name, last_name, full_name)
    if not name_parts:
        return {}

    first, last = name_parts["first"], name_parts["last"]

    return {
        "firstlast": f"{first}{last}",
        "first.last": f"{first}.{last}",
    }


def load_target_domains(input_file: Path) -> List[Dict[str, Any]]:
    """Reads validated domains from Stage 2 output."""
    if not input_file.exists():
        logger.error(f"{input_file} not found. Run domain_discovery.py first.")
        return []

    data = load_json(input_file)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict) and "domain" in item]
    return []


def derive_company_search_query(domain: str) -> str:
    """Derives a candidate search string from a domain name."""
    label = domain.split(".")[0]
    label = re.sub(r"[-_]+", " ", label)
    return label.title()


def resolve_target_label(domain: Optional[str], company_name: Optional[str]) -> str:
    """Picks whichever of domain/company_name is set as a non-optional string."""
    label = domain or company_name
    if label is None:
        raise ValueError(
            "resolve_target_label needs at least a domain or a company_name"
        )
    return label


# =====================================================================
# STAGE A - EMPLOYEE DISCOVERY VIA APIFY
# =====================================================================


def run_apify_linkedin_search(
    search_query: str, max_items: int = DEFAULT_MAX_ITEMS_PER_DOMAIN
) -> List[Dict[str, Any]]:
    """Calls Apify LinkedIn profile search actor for a given query."""
    logger.info(f"[{search_query}] Preparing Apify LinkedIn profile search...")

    actor_id = os.environ.get("APIFY_ACTOR_ID", DEFAULT_APIFY_ACTOR_ID)

    run_input = {
        "profileScraperMode": "Full",
        "search": search_query,
        "maxItems": max_items,
        "locations": [
            # Country-level
            "tunisia",
            # Greater Tunis (highest density hub)
            "tunis",
            "ariana",
            "ben arous",
            "manouba",
            # Northern cities
            "nabeul",
            "hammamet",
            # Central / Sahel corridor (major industrial & tech presence)
            "sousse",
            "monastir",
            "sfax",
        ],
    }

    return run_apify_actor(actor_id, run_input)


def parse_experience_entry(exp: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizes one experience entry."""
    return {
        "title": exp.get("position"),
        "company_name": exp.get("companyName"),
        "company_linkedin_url": exp.get("companyLinkedinUrl"),
        "employment_type": exp.get("employmentType"),
        "workplace_type": exp.get("workplaceType"),
        "location": exp.get("location"),
        "start_date": (exp.get("startDate") or {}).get("text"),
        "end_date": (exp.get("endDate") or {}).get("text"),
        "duration": exp.get("duration"),
        "description": exp.get("description"),
    }


def parse_education_entry(edu: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizes one education entry."""
    return {
        "school_name": edu.get("schoolName"),
        "school_linkedin_url": edu.get("schoolLinkedinUrl"),
        "degree": edu.get("degree"),
        "field_of_study": edu.get("fieldOfStudy"),
        "start_date": (edu.get("startDate") or {}).get("text"),
        "end_date": (edu.get("endDate") or {}).get("text"),
        "period": edu.get("period"),
    }


def parse_employee_item(
    item: Dict[str, Any], matched_domain: str
) -> Optional[Dict[str, Any]]:
    """Extracts public fields from raw actor result."""
    name = (
        item.get("name")
        or " ".join(filter(None, [item.get("firstName"), item.get("lastName")])).strip()
    )
    if not name:
        return None

    location = item.get("location") or {}
    parsed_location = location.get("parsed") or {}

    current_positions = []
    for pos in item.get("currentPosition") or []:
        company = pos.get("company") or {}
        current_positions.append(
            {
                "title": pos.get("position"),
                "company_name": pos.get("companyName") or company.get("name"),
                "company_linkedin_url": pos.get("companyLinkedinUrl"),
                "company_website": company.get("website"),
                "employment_type": pos.get("employmentType"),
                "workplace_type": pos.get("workplaceType"),
                "duration": pos.get("duration"),
            }
        )

    all_positions = [
        parse_experience_entry(exp) for exp in (item.get("experience") or [])
    ]
    education = [parse_education_entry(edu) for edu in (item.get("education") or [])]

    emails = [e.get("email") for e in (item.get("emails") or []) if e.get("email")]
    personal_websites = item.get("websites") or []
    company_websites = [
        w.get("url") for w in (item.get("companyWebsites") or []) if w.get("url")
    ]

    return {
        "name": name,
        "first_name": item.get("firstName"),
        "last_name": item.get("lastName"),
        "job_title": item.get("position") or item.get("headline"),
        "linkedin_url": item.get("linkedinUrl"),
        "linkedin_profile_url": item.get("linkedinProfileUrl"),
        "public_identifier": item.get("publicIdentifier"),
        "location": {
            "text": parsed_location.get("text") or location.get("linkedinText"),
            "city": parsed_location.get("city"),
            "country": parsed_location.get("countryFull")
            or parsed_location.get("country"),
        },
        "about": item.get("about") or item.get("summary"),
        "emails": emails,
        "personal_websites": personal_websites,
        "company_websites": company_websites,
        "current_position": current_positions,
        "all_positions": all_positions,
        "education": education,
        "top_skills": item.get("topSkills") or [],
        "connections_count": item.get("connectionsCount"),
        "follower_count": item.get("followerCount"),
        "open_to_work": item.get("openToWork"),
        "services_offered": (item.get("services") or {}).get("servicesList") or [],
        "matched_domain": matched_domain,
        "source": "Apify-LinkedIn-ProfileSearch",
    }


def normalize_dedup_key(employee: Dict[str, Any]) -> str:
    """Builds normalized key for deduplication."""
    public_id = (employee.get("public_identifier") or "").strip().lower()
    if public_id:
        return f"pid:{public_id}"

    linkedin_url = (
        employee.get("linkedin_url") or employee.get("linkedin_profile_url") or ""
    )
    if linkedin_url:
        normalized_url = linkedin_url.strip().lower().split("?")[0].rstrip("/")
        if normalized_url:
            return f"url:{normalized_url}"

    return f"name:{(employee.get('name') or '').strip().lower()}"


def discover_employees(
    domain: Optional[str], company_name: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Runs Stage A for one target: queries actor, parses and filters results by company name."""
    label = resolve_target_label(domain, company_name)

    if company_name:
        base_query = company_name
    else:
        assert domain is not None
        base_query = derive_company_search_query(domain)

    search_queries = generate_search_query_variants(base_query)

    employees: List[Dict[str, Any]] = []
    raw_items_tagged: List[Dict[str, Any]] = []
    total_raw_count = 0

    target_filter = company_name or base_query

    for search_query in search_queries:
        raw_items = run_apify_linkedin_search(search_query)
        total_raw_count += len(raw_items)
        for item in raw_items:
            raw_items_tagged.append(
                {"search_target": label, "search_query": search_query, "raw": item}
            )

            parsed = parse_employee_item(item, matched_domain=label)
            if parsed:
                employees.append(parsed)

    logger.info(
        f"[{label}] {len(employees)}/{total_raw_count} result(s) matched the company name filter '{target_filter}' (across {len(search_queries)} query variant(s))."
    )
    return employees, raw_items_tagged


# =====================================================================
# STAGE B - USERNAME/PROFILE DISCOVERY VIA MAIGRET
# =====================================================================


def verify_profile_with_jina(
    url: str, first_name: Optional[str] = None, last_name: Optional[str] = None
) -> bool:
    """Verifies a maigret hit using Jina Reader to eliminate dead pages or false positives."""
    token = os.environ.get("JINA_API_KEY")
    headers = {"Accept": "text/plain"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(
            f"{JINA_READER_BASE}{url}", headers=headers, timeout=JINA_TIMEOUT_SECS
        )
    except requests.exceptions.RequestException as e:
        logger.debug(
            f"    [jina] Could not verify {url} ({e}) - keeping it, unverified."
        )
        return True

    if not response.ok:
        logger.debug(
            f"    [jina] Reader returned HTTP {response.status_code} for {url} - keeping it, unverified."
        )
        return True

    text = response.text.strip()
    if len(text) < 40:
        return False

    text_lower = text.lower()
    if any(marker in text_lower for marker in INVALID_PAGE_MARKERS):
        return False

    if first_name or last_name:
        name_fragments = [
            strip_accents(n).lower()
            for n in [first_name, last_name]
            if n and len(n) >= 3
        ]
        if name_fragments and not any(
            fragment in text_lower for fragment in name_fragments
        ):
            logger.debug(
                f"    [jina] Neither name fragment {name_fragments} appears on {url} - likely a different person."
            )
            return False

    return True


def ensure_tor_proxy_running() -> None:
    """Starts the 'tor' Compose service if MAIGRET_TOR_PROXY_URL is configured."""
    if not os.environ.get(MAIGRET_TOR_PROXY_ENV_VAR):
        return

    logger.info("MAIGRET_TOR_PROXY_URL is set - ensuring the 'tor' service is up...")
    success = run_docker_service_up("tor", timeout=60)
    if not success:
        logger.warning(
            "Could not start the 'tor' service. maigret calls using --tor-proxy will likely fail."
        )
    else:
        logger.info("'tor' service is up.")


def build_maigret_command(username: str, reports_dir: str) -> List[str]:
    """Builds arguments list for maigret execution via docker compose."""
    site_list_raw = os.environ.get("MAIGRET_SITES")
    sites = (
        [s.strip() for s in site_list_raw.split(",")]
        if site_list_raw
        else DEFAULT_POPULAR_SITES
    )

    cmd = [
        "--no-deps",
        "-v",
        f"{reports_dir}:/app/reports",
        "maigret",
        username,
        "--json",
        "simple",
        "--folderoutput",
        "/app/reports",
        "--no-progressbar",
        "--retries",
        "1",
    ]

    for site in sites:
        cmd += ["--site", site]

    proxy_url = os.environ.get(MAIGRET_PROXY_ENV_VAR)
    if proxy_url:
        cmd += ["--proxy", proxy_url]

    tor_proxy_url = os.environ.get(MAIGRET_TOR_PROXY_ENV_VAR)
    if tor_proxy_url:
        cmd += ["--tor-proxy", tor_proxy_url]

    cmd.append("--cloudflare-bypass")

    return cmd


def run_maigret(
    username: str, first_name: Optional[str] = None, last_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Runs maigret for a username candidate and verifies hits."""
    logger.info(f"  [{username}] Running maigret (popular sites only)...")
    verified_hits: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd_args = build_maigret_command(username, tmpdir)

        # Execute container using shared runner wrapper
        run_docker_tool(
            tool_name="maigret",
            extra_args=cmd_args,
            timeout=300,
            target_identifier=username,
        )

        report_file = Path(tmpdir) / f"report_{username}_simple.json"
        if not report_file.exists():
            logger.warning(
                f"  [{username}] maigret finished but no report file was found."
            )
            return verified_hits

        data = load_json(report_file)
        if not isinstance(data, dict):
            return verified_hits

        claimed_hits = []
        for site_name, site_data in data.items():
            if not isinstance(site_data, dict):
                continue
            status_info = site_data.get("status") or {}
            if status_info.get("status") == "Claimed":
                claimed_hits.append({"site": site_name, "url": status_info.get("url")})

        logger.info(
            f"  [{username}] maigret claims {len(claimed_hits)} hit(s) - verifying via Jina Reader..."
        )
        for hit in claimed_hits:
            if not hit["url"]:
                verified_hits.append(hit)
                continue
            if verify_profile_with_jina(
                hit["url"], first_name=first_name, last_name=last_name
            ):
                verified_hits.append(hit)
            else:
                logger.info(
                    f"  [{username}] Discarded likely-mismatched hit on {hit['site']}: {hit['url']}"
                )

    return verified_hits


def discover_profiles_for_employee(employee: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generates username candidates and runs maigret enrichment for one employee."""
    candidates = generate_username_candidates(
        employee.get("first_name"), employee.get("last_name"), employee.get("name")
    )
    if not candidates:
        logger.info(
            f"  Could not derive username candidates for '{employee['name']}'. Skipping maigret."
        )
        return []

    results: List[Dict[str, Any]] = []
    for pattern_name, username in candidates.items():
        hits = run_maigret(
            username,
            first_name=employee.get("first_name"),
            last_name=employee.get("last_name"),
        )
        if hits:
            results.append(
                {
                    "username": username,
                    "matched_pattern": pattern_name,
                    "platforms": sorted(hits, key=lambda h: h["site"]),
                    "source": "maigret",
                }
            )

    return results


# =====================================================================
# PIPELINE HELPER FUNCTIONS
# =====================================================================


def parse_arguments() -> argparse.Namespace:
    """Parses command line arguments for Stage 3."""
    parser = argparse.ArgumentParser(
        description="OSINT Stage 3: Employee Discovery & Profile Enrichment"
    )
    parser.add_argument(
        "--company",
        required=False,
        help="Target company name for output folder organization",
    )
    parser.add_argument(
        "--domain",
        required=False,
        help="Direct target domain override (e.g. 'example.com'). Used both as the Apify "
        "search query (unless --company-name is also given).",
    )
    parser.add_argument(
        "--company-name",
        required=False,
        help="Company name to search for and strictly filter by within LinkedIn user experience.",
    )
    return parser.parse_args()


def determine_company_slug(args: argparse.Namespace) -> str:
    """Derives company slug based on CLI flags."""
    if args.company:
        return slugify_company(args.company)
    if args.company_name:
        return slugify_company(args.company_name)
    if args.domain:
        return slugify_company(args.domain)
    return "default"


def resolve_targets(
    args: argparse.Namespace, input_file: Path
) -> List[Dict[str, Optional[str]]]:
    """Resolves target definitions from CLI arguments or Stage 2 output."""
    if args.domain or args.company_name:
        return [{"domain": args.domain, "company_name": args.company_name}]

    domain_entries = load_target_domains(input_file)
    return [
        {"domain": entry["domain"], "company_name": args.company}
        for entry in domain_entries
    ]


def run_employee_discovery_stage(
    targets: List[Dict[str, Optional[str]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Executes Stage A employee discovery across targets with deduplication."""
    all_employees: List[Dict[str, Any]] = []
    all_raw_items: List[Dict[str, Any]] = []
    seen_identifiers: Set[str] = set()

    for target in targets:
        label = resolve_target_label(target["domain"], target["company_name"])
        logger.info(f"--- Processing Candidate Target: {label} ---")
        employees, raw_items_tagged = discover_employees(
            target["domain"], target["company_name"]
        )
        all_raw_items.extend(raw_items_tagged)
        for employee in employees:
            dedup_key = normalize_dedup_key(employee)
            if dedup_key in seen_identifiers:
                continue
            seen_identifiers.add(dedup_key)
            all_employees.append(employee)

    return all_employees, all_raw_items


def run_enrichment_stage(
    all_employees: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Executes Stage B username enrichment across discovered employees."""
    enriched_employees: List[Dict[str, Any]] = []
    employees_with_extra_profiles = 0

    for employee in all_employees:
        logger.info(f"--- Enriching Employee: {employee['name']} ---")
        maigret_results = discover_profiles_for_employee(employee)

        if maigret_results:
            employees_with_extra_profiles += 1

        employee_record = dict(employee)
        employee_record["additional_profiles"] = maigret_results
        enriched_employees.append(employee_record)

    enriched_employees.sort(key=lambda x: x["name"].lower())
    return enriched_employees, employees_with_extra_profiles


def print_summary(
    target_labels: List[str],
    employees_found_count: int,
    employees_with_extra_profiles: int,
    raw_items_count: int,
    enriched_employees: List[Dict[str, Any]],
    output_file: Path,
    output_raw_file: Path,
) -> None:
    """Prints the console execution summary table."""
    print("\n" + "=" * 50)
    print("          EMPLOYEE DISCOVERY SUMMARY")
    print("=" * 50)
    print(f"Target(s)                      : {', '.join(target_labels)}")
    print(f"Employees Found (Apify)         : {employees_found_count}")
    print(f"With Additional Profiles Found  : {employees_with_extra_profiles}")
    print(f"Raw Apify Results Saved         : {raw_items_count}")
    print("-" * 50)
    if enriched_employees:
        print(f"{'NAME':<25} | {'TITLE':<30} | {'EMAIL':<28} | {'EXTRA'}")
        print("-" * 50)
        for emp in enriched_employees:
            extra_count = len(emp["additional_profiles"])
            title = (emp.get("job_title") or "-")[:30]
            email = (emp.get("emails") or ["-"])[0][:28]
            print(f"{emp['name']:<25} | {title:<30} | {email:<28} | {extra_count}")
    else:
        print("No employees were discovered.")
    print("=" * 50)
    print(f"\nFinal output written to: {output_file.resolve()}")
    print(f"Raw data written to    : {output_raw_file.resolve()}")


# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================


def main() -> None:
    setup_logging()
    args = parse_arguments()

    company_slug = determine_company_slug(args)

    output_dir = Path("output") / company_slug
    input_file = output_dir / "domains.json"
    output_file = output_dir / "employees.json"
    output_raw_file = output_dir / "employees_raw.json"

    if not input_file.exists() and Path("output/domains.json").exists():
        input_file = Path("output/domains.json")

    load_env_file()
    ensure_tor_proxy_running()

    # 1. Resolve Target(s)
    targets = resolve_targets(args, input_file)
    if not targets:
        logger.warning("No target domains or company names found to process.")
        return

    target_labels = [
        resolve_target_label(t["domain"], t["company_name"]) for t in targets
    ]

    # 2. Stage A - Employee discovery per target
    all_employees, all_raw_items = run_employee_discovery_stage(targets)
    employees_found_count = len(all_employees)

    if not all_employees:
        logger.warning(
            "No employees discovered via Apify. Nothing to enrich with maigret."
        )

    # 3. Stage B - Username/profile discovery per employee
    enriched_employees, employees_with_extra_profiles = run_enrichment_stage(
        all_employees
    )

    # 4. Write Final JSON Outputs
    output_dir.mkdir(parents=True, exist_ok=True)

    save_json(output_file, enriched_employees, indent=2, ensure_ascii=False)
    save_json(output_raw_file, all_raw_items, indent=2, ensure_ascii=False)

    # 5. Write to Database (Updated)
    conn = get_db_connection()
    # Using 'name' as the unique key field; update to 'public_identifier' or 'email' if preferred.
    upsert_records(
        conn=conn,
        table="raw_employees",
        company_slug=company_slug,
        records=enriched_employees,
        key_field="name",
    )
    conn.close()

    # 6. Console Summary
    print_summary(
        target_labels,
        employees_found_count,
        employees_with_extra_profiles,
        len(all_raw_items),
        enriched_employees,
        output_file,
        output_raw_file,
    )


if __name__ == "__main__":
    main()
