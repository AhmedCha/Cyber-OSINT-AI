#!/usr/bin/env python3
import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

try:
    from apify_client import ApifyClient
except ImportError:  # pragma: no cover
    ApifyClient = None

# =====================================================================
# CONFIGURATION & LOGGING SETUP
# =====================================================================

ENV_FILE = Path(".env")

# harvestapi/linkedin-profile-search-by-services - confirmed actor ID from
# your script. It's a keyword/services search, NOT a company-page-scoped
# scraper, so there's no native "give me this company's employees" input.
# We approximate that by searching on a company-name string derived from
# the domain (or an explicit --company-name override) and keeping only
# results whose current/company website or LinkedIn company URL lines up
# with the target domain. Override the actor via APIFY_ACTOR_ID in .env.
DEFAULT_APIFY_ACTOR_ID = "qXMa8kADnUQdmz18G"
DEFAULT_MAX_ITEMS_PER_DOMAIN = 50

# A few common first/last name -> username patterns. Kept intentionally
# simple per spec; extend later if maigret hit-rate is low.
USERNAME_PATTERN_NAMES = ["firstlast", "first.last", "flast"]

# Jina AI Reader (https://r.jina.ai/) - free text-extraction proxy used to
# double-check maigret "Claimed" hits before trusting them. Works without
# a key at a lower rate limit; set JINA_API_KEY in .env for higher limits.
JINA_READER_BASE = "https://r.jina.ai/"
JINA_TIMEOUT_SECS = 20

# Heuristic markers for "this is a dead-end / not-found page", checked
# against the lowercased extracted text. Deliberately conservative - a
# false "looks valid" is cheaper than a false "discarded a real hit".
INVALID_PAGE_MARKERS = [
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
]

# Curated list of high-traffic, well-maintained social platforms - checking
# only these instead of maigret's default top-500 (which includes lots of
# small/dead/irrelevant sites) is both faster and lower-noise. Names must
# match maigret's site database exactly (case-sensitive) - verified against
# the installed maigret 0.6.3 database. Override via MAIGRET_SITES in .env
# (comma-separated) if you want a different set.
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

# Optional: route maigret's own requests through a proxy (helps evade
# per-site rate limits). Set MAIGRET_PROXY_URL in .env, e.g.
# socks5://127.0.0.1:1080 or http://user:pass@host:port. Left unset by
# default (no --proxy flag passed).
MAIGRET_PROXY_ENV_VAR = "MAIGRET_PROXY_URL"

# Free alternative to a paid proxy: a local Tor SOCKS5 gateway, passed via
# maigret's dedicated --tor-proxy flag (separate from --proxy above). Set
# MAIGRET_TOR_PROXY_URL in .env, e.g. socks5://127.0.0.1:9050 - but you
# need an actual Tor daemon (or tor docker container) listening there;
# maigret doesn't start one for you.
MAIGRET_TOR_PROXY_ENV_VAR = "MAIGRET_TOR_PROXY_URL"

# NOTE on custom headers: maigret's CLI/settings.json don't expose a
# generic "override headers for every site" option - headers are defined
# per-site inside its own data.json. There's no flag to add here for that;
# doing it would mean patching maigret's site database directly, which is
# out of scope for this script.

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


def load_env_file():
    """Minimal .env loader (KEY=VALUE per line) so we don't add a hard
    dependency on python-dotenv. Existing environment variables win."""
    if not ENV_FILE.exists():
        logger.info(
            f"No {ENV_FILE} found. Relying on already-exported environment variables."
        )
        return

    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if not key:
                    continue
                if key in os.environ:
                    logger.info(
                        f"{key} already set in the environment - keeping that value over {ENV_FILE}."
                    )
                    continue
                os.environ[key] = value
    except Exception as e:
        logger.warning(f"Could not read {ENV_FILE}: {e}")


def strip_accents(text: str) -> str:
    """Removes diacritics so 'Émilie' -> 'Emilie' for username derivation."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def split_name(
    first_name: Optional[str], last_name: Optional[str], full_name: Optional[str]
) -> Optional[Dict[str, str]]:
    """Prefers the actor's own firstName/lastName fields; falls back to
    splitting the display name. Returns None if nothing usable is found
    (e.g. the actor returned 'undefined' for a missing field)."""

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
    """Derives a handful of likely username variants from a person's name.
    Returns a dict of {pattern_name: username}."""
    name_parts = split_name(first_name, last_name, full_name)
    if not name_parts:
        return {}

    first, last = name_parts["first"], name_parts["last"]

    return {
        "firstlast": f"{first}{last}",
        "first.last": f"{first}.{last}",
        "flast": f"{first[0]}{last}",
    }


def load_target_domains(input_file: Path) -> List[Dict]:
    """Reads validated domains from Stage 2 output, same pattern used
    across the pipeline's earlier stages."""
    if not input_file.exists():
        logger.error(f"{input_file} not found. Run domain_discovery.py first.")
        return []

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [item for item in data if "domain" in item]
    except Exception as e:
        logger.error(f"Failed to read {input_file}: {e}")
        return []


def derive_company_search_query(domain: str) -> str:
    """Turns 'societegenerale.com' into a rough 'Societe Generale' guess
    to use as the actor's free-text 'search' input. Crude on purpose -
    pass --company-name to override when working a single domain."""
    label = domain.split(".")[0]
    label = re.sub(r"[-_]+", " ", label)
    return label.title()


def resolve_target_label(domain: Optional[str], company_name: Optional[str]) -> str:
    """Picks whichever of domain/company_name is set, as a plain non-optional
    string. Raises if neither is given - callers should already guarantee
    at least one is present."""
    label = domain or company_name
    if label is None:
        raise ValueError(
            "resolve_target_label needs at least a domain or a company_name"
        )
    return label


def generate_search_query_variants(base_query: str) -> List[str]:
    """For a multi-word company name, tries both a space-separated and a
    hyphenated form, since we don't know which way the actor's search
    matches better (e.g. 'Societe Generale' vs 'Societe-Generale')."""
    space_version = re.sub(r"[-_]+", " ", base_query).strip()
    hyphen_version = re.sub(r"\s+", "-", base_query).strip()

    variants = []
    for variant in [space_version, hyphen_version]:
        if variant and variant not in variants:
            variants.append(variant)
    return variants


def domain_matches_company(domain: str, item: Dict) -> bool:
    """Cross-checks a result's employer website/company LinkedIn URL
    against the target domain, to filter out keyword-search noise."""
    for current in item.get("currentPosition") or []:
        company = current.get("company") or {}
        website = (company.get("website") or "").lower()
        if domain in website:
            return True
    for site in item.get("companyWebsites") or []:
        if domain in (site.get("domain") or "").lower():
            return True
    return False


# =====================================================================
# STAGE A - EMPLOYEE DISCOVERY VIA APIFY (LINKEDIN PROFILE SEARCH)
# =====================================================================


def run_apify_linkedin_search(
    search_query: str, max_items: int = DEFAULT_MAX_ITEMS_PER_DOMAIN
) -> List[Dict]:
    """Calls the harvestapi LinkedIn 'profile search by services' Apify
    actor for a given free-text query and returns the raw dataset items."""
    logger.info(f"[{search_query}] Running Apify LinkedIn profile search...")

    if ApifyClient is None:
        logger.error(
            "apify_client package not installed. Run: pip install apify-client --break-system-packages"
        )
        return []

    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        logger.warning(
            f"[{search_query}] APIFY_API_TOKEN not set. Skipping Apify employee discovery."
        )
        return []

    masked = (
        f"{token[:6]}...{token[-4:]}"
        if len(token) > 10
        else "(too short to mask safely)"
    )
    logger.info(f"[{search_query}] Using APIFY_API_TOKEN {masked} (len={len(token)}).")

    actor_id = os.environ.get("APIFY_ACTOR_ID", DEFAULT_APIFY_ACTOR_ID)
    client = ApifyClient(token)

    run_input = {
        "profileScraperMode": "Full",
        "search": search_query,
        "maxItems": max_items,
        "locations": [],  # actor requires an array here - null/None is rejected
        "startPage": 1,
        "takePages": 1,  # actor requires an integer here too - null/None is rejected
    }

    try:
        run = client.actor(actor_id).call(run_input=run_input)
        if run is None:
            logger.error(
                f"[{search_query}] Apify actor call() returned no run (it may have failed to start)."
            )
            return []
        items = list(client.dataset(run.default_dataset_id).iterate_items())
    except Exception as e:
        logger.error(
            f"[{search_query}] Error calling Apify actor or fetching dataset: {e}"
        )
        return []

    logger.info(f"[{search_query}] Apify returned {len(items)} raw profile(s).")
    return items


def parse_experience_entry(exp: Dict) -> Dict:
    """Normalizes one 'experience' (full job history) entry."""
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


def parse_education_entry(edu: Dict) -> Dict:
    """Normalizes one 'education' entry."""
    return {
        "school_name": edu.get("schoolName"),
        "school_linkedin_url": edu.get("schoolLinkedinUrl"),
        "degree": edu.get("degree"),
        "field_of_study": edu.get("fieldOfStudy"),
        "start_date": (edu.get("startDate") or {}).get("text"),
        "end_date": (edu.get("endDate") or {}).get("text"),
        "period": edu.get("period"),
    }


def parse_employee_item(item: Dict, matched_domain: str) -> Optional[Dict]:
    """Extracts the relevant public fields from one raw actor result:
    identity, account URL, contact info, full job/education history, and
    other public profile details."""
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


def normalize_dedup_key(employee: Dict) -> str:
    """Builds a normalized key so the same person found via different
    search-query variants (e.g. hyphen vs space) collapses to a single
    entry before maigret runs, even if the actor returned slightly
    different URL formatting (trailing slash, query params, casing)
    between the two calls."""
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
) -> "tuple[List[Dict], List[Dict]]":
    """Runs Stage A for one target: builds a search query (from an explicit
    company name, or guessed from the domain), calls the actor, and - only
    when a domain is available - filters results down to ones plausibly
    tied to that domain. Company-name-only runs skip that filter since
    there's no domain to cross-check against.

    Returns (parsed_employees, raw_items_tagged) - the second list keeps
    every raw actor result untouched (including ones filtered out of
    employees.json for not matching the domain), tagged with which
    search target produced them, for later ad-hoc processing.
    """
    label = resolve_target_label(domain, company_name)

    if company_name:
        base_query = company_name
    else:
        assert domain is not None  # guaranteed by resolve_target_label above
        base_query = derive_company_search_query(domain)

    search_queries = generate_search_query_variants(base_query)

    employees = []
    raw_items_tagged = []
    total_raw_count = 0

    for search_query in search_queries:
        raw_items = run_apify_linkedin_search(search_query)
        total_raw_count += len(raw_items)
        for item in raw_items:
            raw_items_tagged.append(
                {"search_target": label, "search_query": search_query, "raw": item}
            )
            if domain and not domain_matches_company(domain, item):
                continue
            parsed = parse_employee_item(item, matched_domain=label)
            if parsed:
                employees.append(parsed)

    if domain:
        logger.info(
            f"[{label}] {len(employees)}/{total_raw_count} result(s) matched the target domain (across {len(search_queries)} query variant(s))."
        )
    else:
        logger.info(
            f"[{label}] {len(employees)}/{total_raw_count} result(s) kept (no domain to filter against, across {len(search_queries)} query variant(s))."
        )
    return employees, raw_items_tagged


# =====================================================================
# STAGE B - USERNAME/PROFILE DISCOVERY VIA MAIGRET
# =====================================================================


def verify_profile_with_jina(
    url: str, first_name: Optional[str] = None, last_name: Optional[str] = None
) -> bool:
    """Uses Jina AI Reader (a free URL-to-text proxy) to fetch a lightweight
    text extraction of a maigret 'Claimed' hit and checks two things:

    1. It doesn't look like a 404/"not found" placeholder page.
    2. If we have the employee's real first/last name, at least one of
       them actually shows up on the page - this is what catches the
       'ahichem' (first-initial+last-name) false-positive case, where the
       username matches a *different* person who happens to share the
       same last name (e.g. 'Abadou Hichem' claiming a hit meant for
       'Ammous Hichem'). A last-name-only match on a short pattern isn't
       enough on its own; requiring the actual page text to contain the
       real name filters that out.

    Returns True to keep the result, False to discard it. Fails OPEN
    (keeps the result) on network errors or non-2xx responses from Jina
    itself - a Jina hiccup isn't proof the profile is dead or mismatched,
    and a false positive you can eyeball is cheaper than silently losing
    a real hit.
    """
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
        # An almost-empty extraction is itself a strong dead-page signal.
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
    """Starts the 'tor' Compose service once, up front, if
    MAIGRET_TOR_PROXY_URL is configured - so individual maigret calls
    (which now pass --no-deps) can assume it's already there instead of
    each trying to reconcile/recreate it themselves."""
    if not os.environ.get(MAIGRET_TOR_PROXY_ENV_VAR):
        return

    logger.info("MAIGRET_TOR_PROXY_URL is set - ensuring the 'tor' service is up...")
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "tor"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                f"Could not start the 'tor' service (exit code {result.returncode}): "
                f"{result.stderr.strip()[-500:]}. maigret calls using --tor-proxy will likely fail."
            )
        else:
            logger.info("'tor' service is up.")
    except Exception as e:
        logger.warning(
            f"Error starting the 'tor' service: {e}. maigret calls using --tor-proxy will likely fail."
        )


def build_maigret_command(username: str, reports_dir: str) -> List[str]:
    """Builds the 'docker compose run' invocation for one username, scoped
    to DEFAULT_POPULAR_SITES (or MAIGRET_SITES override) instead of
    maigret's default top-500, plus optional proxy / Cloudflare-bypass
    flags read from the environment."""
    site_list_raw = os.environ.get("MAIGRET_SITES")
    sites = (
        [s.strip() for s in site_list_raw.split(",")]
        if site_list_raw
        else DEFAULT_POPULAR_SITES
    )

    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
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
) -> List[Dict]:
    """Runs maigret against a single username candidate (scoped to
    DEFAULT_POPULAR_SITES), parses the 'Claimed' hits out of its JSON
    report, and verifies each one through Jina Reader - including an
    identity cross-check against the employee's real name. Returns a
    list of {"site": ..., "url": ...} for hits that survived verification.

    Uses a temporary directory bind mount so the JSON report persists
    after '--rm', matching the theHarvester/SpiderFoot pattern.
    """
    logger.info(f"  [{username}] Running maigret (popular sites only)...")
    verified_hits: List[Dict] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = build_maigret_command(username, tmpdir)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300, check=False
            )

            report_file = Path(tmpdir) / f"report_{username}_simple.json"
            if not report_file.exists():
                logger.warning(
                    f"  [{username}] maigret finished (exit code {result.returncode}) but no report file was found."
                )
                if result.stdout.strip():
                    logger.warning(
                        f"  [{username}] maigret stdout (tail): {result.stdout.strip()[-800:]}"
                    )
                if result.stderr.strip():
                    logger.warning(
                        f"  [{username}] maigret stderr (tail): {result.stderr.strip()[-800:]}"
                    )
                return verified_hits

            with open(report_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            claimed_hits = []
            for site_name, site_data in data.items():
                if not isinstance(site_data, dict):
                    continue
                status_info = site_data.get("status") or {}
                if status_info.get("status") == "Claimed":
                    claimed_hits.append(
                        {"site": site_name, "url": status_info.get("url")}
                    )

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

        except subprocess.TimeoutExpired:
            logger.error(
                f"  [{username}] maigret container timed out (300s). Continuing pipeline."
            )
        except Exception as e:
            logger.error(f"  [{username}] Error executing or parsing maigret: {e}")

    return verified_hits


def discover_profiles_for_employee(employee: Dict) -> List[Dict]:
    """Generates username candidates for one employee, runs maigret against
    each, and returns a tagged list of {username, pattern, platforms}."""
    candidates = generate_username_candidates(
        employee.get("first_name"), employee.get("last_name"), employee.get("name")
    )
    if not candidates:
        logger.info(
            f"  Could not derive username candidates for '{employee['name']}'. Skipping maigret."
        )
        return []

    results = []
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
# MAIN PIPELINE EXECUTION
# =====================================================================


def main():
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
        "search query (unless --company-name is also given) and as the post-search "
        "domain-match filter.",
    )
    parser.add_argument(
        "--company-name",
        required=False,
        help="Company name to search for. Can be used on its own (no domain filtering "
        "applied to results) or together with --domain to override the guessed query "
        "while still filtering results against that domain.",
    )
    args = parser.parse_args()

    if args.company:
        company_slug = slugify_company(args.company)
    elif args.company_name:
        company_slug = slugify_company(args.company_name)
    elif args.domain:
        company_slug = slugify_company(args.domain)
    else:
        company_slug = "default"

    output_dir = Path("output") / company_slug
    input_file = output_dir / "domains.json"
    output_file = output_dir / "employees.json"
    output_raw_file = output_dir / "employees_raw.json"

    if not input_file.exists() and Path("output/domains.json").exists():
        input_file = Path("output/domains.json")

    load_env_file()
    ensure_tor_proxy_running()

    # 1. Resolve Target(s): either an explicit --domain and/or --company-name,
    # or (if neither given) every validated domain from Stage 2's output.
    targets: List[Dict[str, Optional[str]]] = []
    if args.domain or args.company_name:
        targets = [{"domain": args.domain, "company_name": args.company_name}]
    else:
        domain_entries = load_target_domains(input_file)
        targets = [
            {"domain": entry["domain"], "company_name": None}
            for entry in domain_entries
        ]

    if not targets:
        logger.warning("No target domains or company names found to process.")
        return

    target_labels = [
        resolve_target_label(t["domain"], t["company_name"]) for t in targets
    ]

    # 2. Stage A - Employee discovery per target
    all_employees: List[Dict] = []
    all_raw_items: List[Dict] = []
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

    employees_found_count = len(all_employees)

    if not all_employees:
        logger.warning(
            "No employees discovered via Apify. Nothing to enrich with maigret."
        )

    # 3. Stage B - Username/profile discovery per employee
    enriched_employees: List[Dict] = []
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

    # 4. Write Final JSON Outputs
    output_dir.mkdir(parents=True, exist_ok=True)

    # output/{company-slug}/employees.json - parsed/deduped, for downstream pipeline stages
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(enriched_employees, f, indent=2, ensure_ascii=False)

    # output/{company-slug}/employees_raw.json - every raw Apify result untouched
    with open(output_raw_file, "w", encoding="utf-8") as f:
        json.dump(all_raw_items, f, indent=2, ensure_ascii=False)

    # 5. Console Summary
    print("\n" + "=" * 50)
    print("          EMPLOYEE DISCOVERY SUMMARY")
    print("=" * 50)
    print(f"Target(s)                      : {', '.join(target_labels)}")
    print(f"Employees Found (Apify)         : {employees_found_count}")
    print(f"With Additional Profiles Found  : {employees_with_extra_profiles}")
    print(f"Raw Apify Results Saved         : {len(all_raw_items)}")
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


if __name__ == "__main__":
    main()
