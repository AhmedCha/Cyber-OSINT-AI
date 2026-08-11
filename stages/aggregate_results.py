#!/usr/bin/env python3
"""
OSINT Stage: Result Aggregation

Walks the per-company `output/{company}/` directory produced by the earlier
pipeline stages (domain_discovery.py, document_discovery.py, email
enrichment, employee enrichment, breach lookups, ...) and merges everything
into a single deduplicated JSON payload intended to be handed to an LLM for
the next processing stage.

Responsibilities:
  1. Load every known artifact file if present (missing files are skipped,
     not fatal).
  2. Resolve the actual on-disk filepath for every document discovered by
     metagoofil / apify-google, since documents.json only stores the
     filename + source_domain + discovery method.
  3. Deduplicate every collection (domains, emails, documents, employees,
     breaches) on a sensible natural key, merging "sources"/"origins"
     instead of dropping information.
  4. Prefer validated/enriched data over raw/candidate data when both exist
     for the same entity (e.g. validated_emails.json over
     candidate_emails.json).
  5. Write a single `aggregate_results.json` (plus an optional slim
     `llm_context.json` if --slim is passed) to the company output folder.

Usage:
    python aggregate_results.py --company "Resys Consultants"
    python aggregate_results.py --company "Resys Consultants" --slim
"""

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib.common import setup_logging, slugify_company, strip_accents
from lib.json_utils import load_json, save_json
from lib.email_patterns import is_infrastructure_hostname

logger = logging.getLogger(__name__)

# Maps documents.json's "discovery_method" value to the on-disk folder name
# that document_discovery.py actually downloads files into.
DISCOVERY_METHOD_TO_FOLDER = {
    "apify-google": "apify",
    "metagoofil": "metagoofil",
}


# =====================================================================
# LOAD HELPERS (all tolerant of missing files)
# =====================================================================


def load_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        logger.debug(f"Skipping missing file: {path}")
        return []
    data = load_json(path)
    if not data:
        return []
    if not isinstance(data, list):
        logger.warning(
            f"Expected a list in {path}, got {type(data).__name__}. Skipping."
        )
        return []
    return data


def load_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        logger.debug(f"Skipping missing file: {path}")
        return {}
    data = load_json(path)
    if not data or not isinstance(data, dict):
        return {}
    return data


# =====================================================================
# DOMAINS
# =====================================================================


def aggregate_domains(company_dir: Path) -> List[Dict[str, Any]]:
    """Merge domains.json (validated) with candidate_domains.json (raw),
    keeping the validated flag and union of sources per domain."""
    validated = load_list(company_dir / "domains.json")
    candidates = load_list(company_dir / "candidate_domains.json")

    merged: Dict[str, Dict[str, Any]] = {}

    for item in candidates:
        domain = item.get("domain")
        if not domain:
            continue
        entry = merged.setdefault(
            domain, {"domain": domain, "sources": [], "dns_validated": False}
        )
        for src in item.get("sources", []):
            if src not in entry["sources"]:
                entry["sources"].append(src)

    for item in validated:
        domain = item.get("domain")
        if not domain:
            continue
        entry = merged.setdefault(
            domain, {"domain": domain, "sources": [], "dns_validated": False}
        )
        entry["dns_validated"] = True
        for src in item.get("sources", []):
            if src not in entry["sources"]:
                entry["sources"].append(src)

    result = list(merged.values())
    for entry in result:
        entry["sources"].sort()
    result.sort(key=lambda x: x["domain"])
    return result


# =====================================================================
# EMAILS
# =====================================================================


def aggregate_emails(company_dir: Path) -> List[Dict[str, Any]]:
    """Merge validated_emails.json (enriched/scored) with
    candidate_emails.json (raw pattern-generated), keyed by lowercased
    email address. Validated data wins on conflicting fields; sources are
    unioned."""
    candidates = load_list(company_dir / "candidate_emails.json")
    validated = load_list(company_dir / "validated_emails.json")

    merged: Dict[str, Dict[str, Any]] = {}

    for item in candidates:
        email = (item.get("email") or "").strip().lower()
        if not email:
            continue
        merged[email] = {
            "email": email,
            "employee": item.get("employee"),
            "sources": list(item.get("sources", [])),
            "validation_status": "unknown",
            "confidence": 0.0,
            "is_catch_all": False,
        }

    for item in validated:
        email = (item.get("email") or "").strip().lower()
        if not email:
            continue
        entry = merged.setdefault(
            email,
            {
                "email": email,
                "employee": item.get("employee"),
                "sources": [],
                "validation_status": "unknown",
                "confidence": 0.0,
                "is_catch_all": False,
            },
        )
        entry["employee"] = item.get("employee", entry.get("employee"))
        entry["validation_status"] = item.get(
            "validation_status", entry["validation_status"]
        )
        entry["confidence"] = item.get("confidence", entry["confidence"])
        entry["is_catch_all"] = item.get("is_catch_all", entry["is_catch_all"])
        for src in item.get("sources", []):
            if src not in entry["sources"]:
                entry["sources"].append(src)

    result = list(merged.values())
    for entry in result:
        entry["sources"].sort()
    # Best emails first: deliverable > unknown, then by confidence desc.
    result.sort(
        key=lambda x: (
            0 if x["validation_status"] == "deliverable" else 1,
            -float(x.get("confidence") or 0.0),
            x["email"],
        )
    )
    return result


def summarize_email_domains(emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per email domain seen among the aggregated candidates,
    noting whether email_validation.py's per-domain catch-all pre-check
    flagged it (validation_status=='smtp_inconclusive_catchall' /
    is_catch_all=True on its candidates) - a single fact instead of only
    being inferable by scanning every individual email's status. Also
    flags domains that are themselves infrastructure hostnames (e.g.
    mail.bts.com.tn), since those shouldn't have had candidates generated
    against them in the first place (see email_patterns.py)."""
    summary: Dict[str, Dict[str, Any]] = {}
    for e in emails:
        email = e.get("email") or ""
        if "@" not in email:
            continue
        domain = email.split("@", 1)[1]
        entry = summary.setdefault(
            domain,
            {
                "domain": domain,
                "candidate_count": 0,
                "confirmed_catch_all": False,
                "is_infrastructure_hostname": is_infrastructure_hostname(domain),
            },
        )
        entry["candidate_count"] += 1
        if e.get("validation_status") == "smtp_inconclusive_catchall" or e.get(
            "is_catch_all"
        ):
            entry["confirmed_catch_all"] = True

    return [summary[d] for d in sorted(summary)]


# =====================================================================
# DOCUMENTS (resolve on-disk paths for apify / metagoofil downloads)
# =====================================================================


def resolve_document_path(
    company_dir: Path, source_domain: str, discovery_method: str, filename: str
) -> Optional[str]:
    folder = DISCOVERY_METHOD_TO_FOLDER.get(discovery_method)
    if not folder:
        return None
    candidate = company_dir / folder / source_domain / filename
    return str(candidate)


def aggregate_documents(company_dir: Path) -> List[Dict[str, Any]]:
    """Attach a resolved filesystem path (and existence flag) to every
    document record, deduplicating on (source_domain, filename)."""
    documents = load_list(company_dir / "documents.json")

    merged: Dict[tuple, Dict[str, Any]] = {}

    for doc in documents:
        filename = doc.get("filename")
        source_domain = doc.get("source_domain", "")
        discovery_method = doc.get("discovery_method", "")
        if not filename:
            continue

        key = (source_domain, filename)
        if key in merged:
            # Same file discovered twice (e.g. by both metagoofil and
            # apify) - keep the one that is verified, skip the weaker dup.
            existing = merged[key]
            if not (
                doc.get("content_verified") and not existing.get("content_verified")
            ):
                continue

        resolved_path = resolve_document_path(
            company_dir, source_domain, discovery_method, filename
        )
        file_exists = Path(resolved_path).exists() if resolved_path else False

        merged[key] = {
            "filename": filename,
            "source_domain": source_domain,
            "discovery_method": discovery_method,
            "filepath": resolved_path,
            "file_exists": file_exists,
            "extracted_metadata": doc.get("extracted_metadata", {}),
            "content_verified": doc.get("content_verified", False),
        }

    result = list(merged.values())
    result.sort(key=lambda x: (x["source_domain"], x["filename"]))
    return result


# =====================================================================
# EMPLOYEES
# =====================================================================

# Subdomain labels that don't identify the company itself (e.g. the
# search-matched domain "mail.bts.com.tn" should key off "bts", not "mail").
_COMMON_SUBDOMAIN_PREFIXES = {
    "www",
    "mail",
    "webmail",
    "smtp",
    "mx",
    "ftp",
    "m",
    "portal",
    "intranet",
    "vpn",
}

# Reliability tiers, in the priority order the report should render them.
EMPLOYEE_TIER_ORDER = [
    "leadership",
    "current_employee",
    "intern",
    "former_employee",
    "reject",
]

LEADERSHIP_KEYWORDS = [
    "director",
    "directeur",
    "directrice",
    "manager",
    "responsable",
    "head of",
    "chef de",
    "chief",
    "ceo",
    "cto",
    "cfo",
    "coo",
    "cio",
    "founder",
    "co-founder",
    "fondateur",
    "fondatrice",
    "president",
    "président",
    "présidente",
    "vice president",
    "vice-président",
    "vice-présidente",
    "chairman",
    "chairwoman",
    "partner",
    "associé gérant",
    "general manager",
    "managing director",
    "gérant",
    "gérante",
    "principal",
    "senior manager",
    "team lead",
]

INTERN_KEYWORDS = ["intern", "stagiaire", "trainee", "internship", "stage"]


def _normalize_for_match(text: Optional[str]) -> str:
    return strip_accents(text or "").lower()


# Title-keyword matching runs against accent-stripped text (see
# _normalize_for_match), so the keyword lists themselves must be
# accent-stripped too, or accented entries like "président" would never
# match the normalized "president" they're being compared against.
_LEADERSHIP_KEYWORDS_NORMALIZED = [_normalize_for_match(k) for k in LEADERSHIP_KEYWORDS]
_INTERN_KEYWORDS_NORMALIZED = [_normalize_for_match(k) for k in INTERN_KEYWORDS]


def _domain_keyword(domain: Optional[str]) -> str:
    """Extracts the company-identifying label from a domain, skipping
    common subdomain prefixes (e.g. 'mail.bts.com.tn' -> 'bts', not
    'mail', which the old plain `domain.split('.')[0]` approach got wrong)."""
    if not domain:
        return ""
    parts = domain.lower().split(".")
    while len(parts) > 2 and parts[0] in _COMMON_SUBDOMAIN_PREFIXES:
        parts = parts[1:]
    return parts[0] if parts else ""


def _acronym(text: str) -> str:
    """Derives an initials-style acronym from a multi-word name, e.g.
    'Banque Tunisienne de Solidarite' -> 'btds'. Only meaningful for
    matching against short, acronym-like domain keywords/company names."""
    words = [w for w in _normalize_for_match(text).split() if w]
    return "".join(w[0] for w in words)


def _position_matches_target(
    position_company_name: Optional[str],
    domain_keyword: str,
    company_name: Optional[str],
) -> bool:
    """True if a position's company name plausibly refers to the target
    company. The search domain's label (e.g. 'bts' from bts.com.tn) is the
    primary, reliable signal. The human-readable --company name is used
    only as a narrow acronym check (e.g. company_name 'BTS' matching a
    position literally containing 'BTS') - a plain word-overlap match was
    tried and rejected because generic words shared by many companies in
    the same sector/country (e.g. 'Banque', 'Tunisienne', 'Solidarite')
    produced false positives on unrelated employers."""
    if not position_company_name:
        return False

    normalized = (
        _normalize_for_match(position_company_name).replace("-", " ").replace("_", " ")
    )
    normalized_compact = normalized.replace(" ", "")

    if domain_keyword and domain_keyword in normalized_compact:
        return True

    if company_name:
        target_normalized = _normalize_for_match(company_name).strip()
        # Short, acronym-like target names (e.g. "BTS") matched as a whole
        # word in the position's company name.
        if len(target_normalized) <= 6 and target_normalized:
            if target_normalized in set(normalized.split()):
                return True
        # A multi-word target name's own initials appearing as a whole
        # word in the position's company name (e.g. target "Banque
        # Tunisienne de Solidarite" -> "bts" found in "BTS BANK").
        acronym = _acronym(company_name)
        if len(acronym) >= 2 and acronym in set(normalized.split()):
            return True

    return False


def _is_ongoing(position: Dict[str, Any]) -> bool:
    """A position with no end_date (LinkedIn's convention for a role
    that's still active) counts as ongoing."""
    end_date = position.get("end_date")
    return not end_date or str(end_date).strip().lower() in ("", "present")


def classify_employee_tier(
    employee: Dict[str, Any], matched_domain: str, company_name: Optional[str]
) -> Tuple[str, str]:
    """Assigns a reliability tier to a discovered person based on their
    position history at the target company, plus a short human-readable
    reason:
      - leadership       - current role at target company, senior/leadership title
      - current_employee - current role at target company, standard title
      - intern           - current role at target company, intern/trainee title
      - former_employee  - past (non-ongoing) role at target company only
      - reject           - no position in the data ever placed them at the
                            target company at all
    Requires enriched position data (current_position / all_positions); if
    neither is present (e.g. raw, pre-enrichment records) this can't be
    assessed and the caller should not call this function.
    """
    domain_keyword = _domain_keyword(matched_domain)

    current_list = employee.get("current_position") or []
    if isinstance(current_list, dict):
        current_list = [current_list]
    if not isinstance(current_list, list):
        current_list = []
    all_positions = employee.get("all_positions") or []
    if not isinstance(all_positions, list):
        all_positions = []

    matched_current: List[Dict[str, Any]] = []
    matched_former: List[Dict[str, Any]] = []

    # LinkedIn's own "current position" bucket is definitionally ongoing.
    for pos in current_list:
        if isinstance(pos, dict) and _position_matches_target(
            pos.get("company_name"), domain_keyword, company_name
        ):
            matched_current.append(pos)

    # Full position history may surface target-company roles (current or
    # past) that didn't make it into the current-position bucket.
    for pos in all_positions:
        if not isinstance(pos, dict):
            continue
        if not _position_matches_target(
            pos.get("company_name"), domain_keyword, company_name
        ):
            continue
        if _is_ongoing(pos):
            matched_current.append(pos)
        else:
            matched_former.append(pos)

    if not matched_current and not matched_former:
        return (
            "reject",
            "No position in this person's LinkedIn history mentions the target company.",
        )

    if matched_current:
        title_text = _normalize_for_match(
            " ".join(p.get("title") or "" for p in matched_current)
        )
        title = (
            next((p.get("title") for p in matched_current if p.get("title")), "") or ""
        )
        if any(kw in title_text for kw in _INTERN_KEYWORDS_NORMALIZED):
            return (
                "intern",
                f"Current intern/trainee at target company ({title.strip()}).",
            )
        if any(kw in title_text for kw in _LEADERSHIP_KEYWORDS_NORMALIZED):
            return (
                "leadership",
                f"Current employee in a senior/leadership role at target company ({title.strip()}).",
            )
        return (
            "current_employee",
            f"Current employee at target company ({title.strip()}).",
        )

    title = next((p.get("title") for p in matched_former if p.get("title")), "") or ""
    return "former_employee", f"Former employee at target company ({title.strip()})."


def _current_position(
    employee: Dict[str, Any], matched_domain: str
) -> Optional[Dict[str, Any]]:
    domain_keyword = _domain_keyword(matched_domain)
    for pos in employee.get("all_positions", []) or []:
        if _position_matches_target(pos.get("company_name"), domain_keyword, None):
            return pos
    return None


def aggregate_employees(
    company_dir: Path, company_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Load the enriched employees.json (falls back to employees_raw.json
    only if the enriched file is absent), deduplicated by LinkedIn public
    identifier / profile URL, trimmed to the fields relevant for the next
    LLM stage (full position history and education are kept, but redundant
    raw-scrape wrapper fields are dropped). Each entry is also tagged with
    an `employee_tier` / `tier_reason` reliability ranking, and carries
    through both `linkedin_url` and Stage B's `additional_profiles`
    (maigret-discovered social links) so downstream stages don't have to
    re-derive either."""
    employees = load_list(company_dir / "employees.json")
    used_raw = False
    if not employees:
        raw_employees = load_list(company_dir / "employees_raw.json")
        employees = raw_employees
        used_raw = True

    merged: Dict[str, Dict[str, Any]] = {}

    for emp in employees:
        if used_raw:
            raw = emp.get("raw", {})
            key = raw.get("publicIdentifier") or raw.get("linkedinUrl") or raw.get("id")
            if not key:
                continue
            merged[key] = {
                "name": raw.get("name"),
                "job_title": raw.get("position") or raw.get("headline"),
                "linkedin_url": raw.get("linkedinUrl"),
                "public_identifier": raw.get("publicIdentifier"),
                "location": raw.get("location", {}).get("parsed", {}),
                "emails": raw.get("emails", []),
                "matched_domain": emp.get("search_target"),
                "source": "raw-linkedin-search",
                "additional_profiles": [],
                # Raw (pre-enrichment) records have no position history to
                # tier against - default to keeping them rather than
                # silently rejecting for lack of data.
                "employee_tier": "current_employee",
                "tier_reason": "Raw discovery data only; position history "
                "unavailable to assess target-company tenure.",
            }
        else:
            key = (
                emp.get("public_identifier")
                or emp.get("linkedin_url")
                or emp.get("name")
            )
            if not key:
                continue
            matched_domain = emp.get("matched_domain", "")
            tier, tier_reason = classify_employee_tier(
                emp, matched_domain, company_name
            )
            merged[key] = {
                "name": emp.get("name"),
                "job_title": emp.get("job_title"),
                "linkedin_url": emp.get("linkedin_url"),
                "public_identifier": emp.get("public_identifier"),
                "location": emp.get("location"),
                "about": emp.get("about"),
                "emails": emp.get("emails", []),
                "current_position": emp.get("current_position")
                or _current_position(emp, matched_domain),
                "all_positions": emp.get("all_positions", []),
                "education": emp.get("education", []),
                "services_offered": emp.get("services_offered", []),
                "matched_domain": matched_domain,
                "source": emp.get("source"),
                "additional_profiles": emp.get("additional_profiles", []),
                "employee_tier": tier,
                "employee_tier_rank": EMPLOYEE_TIER_ORDER.index(tier),
                "tier_reason": tier_reason,
            }

    result = list(merged.values())
    result.sort(key=lambda x: x.get("name") or "")
    return result


# =====================================================================
# BREACHES
# =====================================================================


def aggregate_breaches(company_dir: Path) -> List[Dict[str, Any]]:
    breaches = load_list(company_dir / "breaches.json")
    merged: Dict[str, Dict[str, Any]] = {}
    for item in breaches:
        email = (item.get("email") or "").strip().lower()
        if not email:
            continue
        entry = merged.setdefault(
            email, {"email": email, "breaches": [], "services": []}
        )
        for b in item.get("breaches", []):
            if b not in entry["breaches"]:
                entry["breaches"].append(b)
        # breach_lookup.py emits "services_checked" (a list, e.g.
        # ["spiderfoot", "apify"]) - fall back to a legacy singular
        # "service" key in case an older run/tool version produced that
        # shape instead, so neither format silently loses data.
        services = item.get("services_checked")
        if not isinstance(services, list):
            single = item.get("service")
            services = [single] if single else []
        for service in services:
            if service and service not in entry["services"]:
                entry["services"].append(service)
    return sorted(merged.values(), key=lambda x: x["email"])


# =====================================================================
# DARK WEB
# =====================================================================


def aggregate_darkweb(company_dir: Path) -> List[Dict[str, Any]]:
    """Merge darkweb_discovery.py's per-target scan results, deduplicated on
    (target, target_type), unioning modules_checked and mentions in case the
    same target was scanned more than once."""
    records = load_list(company_dir / "darkweb.json")
    merged: Dict[tuple, Dict[str, Any]] = {}

    for item in records:
        target = item.get("target")
        target_type = item.get("target_type")
        if not target or not target_type:
            continue
        key = (target, target_type)
        entry = merged.setdefault(
            key,
            {
                "target": target,
                "target_type": target_type,
                "mentions": [],
                "modules_checked": [],
            },
        )
        mentions = item.get("mentions")
        if isinstance(mentions, list):
            for m in mentions:
                if m not in entry["mentions"]:
                    entry["mentions"].append(m)
        modules = item.get("modules_checked")
        if isinstance(modules, list):
            for mod in modules:
                if mod not in entry["modules_checked"]:
                    entry["modules_checked"].append(mod)

    result = list(merged.values())
    result.sort(key=lambda x: (x["target_type"], x["target"]))
    return result


# =====================================================================
# SLIM / LLM-READY VIEW
# =====================================================================


def build_slim_context(aggregate: Dict[str, Any]) -> Dict[str, Any]:
    """A trimmed-down version of the aggregate, keeping only what an LLM
    typically needs to reason over (drops verbose bios/descriptions)."""
    return {
        "company": aggregate["company"],
        "domains": [d["domain"] for d in aggregate["domains"]],
        "emails": [
            {
                "email": e["email"],
                "employee": e["employee"],
                "validation_status": e["validation_status"],
                "confidence": e["confidence"],
            }
            for e in aggregate["emails"]
        ],
        "email_domains": aggregate.get("email_domains", []),
        "documents": [
            {
                "filename": d["filename"],
                "filepath": d["filepath"],
                "file_exists": d["file_exists"],
                "source_domain": d["source_domain"],
            }
            for d in aggregate["documents"]
        ],
        "employees": [
            {
                "name": e["name"],
                "job_title": e.get("job_title"),
                "linkedin_url": e.get("linkedin_url"),
                "additional_profiles": e.get("additional_profiles", []),
                "matched_domain": e.get("matched_domain"),
                "employee_tier": e.get("employee_tier"),
            }
            for e in aggregate["employees"]
        ],
        "breach_count": len(aggregate["breaches"]),
        "darkweb_targets_with_mentions": [
            {
                "target": d["target"],
                "target_type": d["target_type"],
                "mention_count": len(d["mentions"]),
            }
            for d in aggregate["darkweb"]
            if d["mentions"]
        ],
        "counts": aggregate["counts"],
    }


# =====================================================================
# CLI & MAIN PIPELINE
# =====================================================================


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OSINT Stage: Aggregate all collected results into a single JSON payload"
    )
    parser.add_argument("--company", required=True, help="Target company name")
    parser.add_argument(
        "--slim",
        action="store_true",
        help="Also write a trimmed llm_context.json alongside the full aggregate",
    )
    return parser.parse_args()


def print_summary(counts: Dict[str, int], output_files: List[Path]) -> None:
    print("\n" + "=" * 60)
    print("               AGGREGATION SUMMARY")
    print("=" * 60)
    for label, count in counts.items():
        print(f"{label:<28}: {count}")
    print("-" * 60)
    for f in output_files:
        print(f"Written: {f.resolve()}")
    print("=" * 60 + "\n")


def main() -> None:
    setup_logging()
    args = parse_arguments()

    company_slug = slugify_company(args.company)
    company_dir = Path("output") / company_slug

    if not company_dir.exists():
        logger.error(f"Company output directory not found: {company_dir}")
        return

    domains = aggregate_domains(company_dir)
    emails = aggregate_emails(company_dir)
    email_domains = summarize_email_domains(emails)
    documents = aggregate_documents(company_dir)
    employees = aggregate_employees(company_dir, args.company)
    breaches = aggregate_breaches(company_dir)
    darkweb = aggregate_darkweb(company_dir)
    dns_infra = load_dict(company_dir / "dns_infra.json")
    infrastructure_raw = load_dict(company_dir / "domain_discovery_raw.json")
    dns_infra_raw = load_dict(company_dir / "dns_infra_raw.json")

    counts = {
        "Domains": len(domains),
        "Emails": len(emails),
        "  - deliverable": sum(
            1 for e in emails if e["validation_status"] == "deliverable"
        ),
        "  - catch-all domains": sum(
            1 for d in email_domains if d["confirmed_catch_all"]
        ),
        "Documents": len(documents),
        "  - found on disk": sum(1 for d in documents if d["file_exists"]),
        "  - content verified": sum(1 for d in documents if d["content_verified"]),
        "Employees": len(employees),
        "  - leadership": sum(
            1 for e in employees if e.get("employee_tier") == "leadership"
        ),
        "  - current_employee": sum(
            1 for e in employees if e.get("employee_tier") == "current_employee"
        ),
        "  - intern": sum(1 for e in employees if e.get("employee_tier") == "intern"),
        "  - former_employee": sum(
            1 for e in employees if e.get("employee_tier") == "former_employee"
        ),
        "  - reject": sum(1 for e in employees if e.get("employee_tier") == "reject"),
        "Breach records": len(breaches),
        "Dark web targets scanned": len(darkweb),
        "  - with mentions found": sum(1 for d in darkweb if d["mentions"]),
        "Infrastructure raw targets": len(infrastructure_raw),
        "DNS infra raw targets": len(dns_infra_raw),
    }

    aggregate: Dict[str, Any] = {
        "company": args.company,
        "company_slug": company_slug,
        "domains": domains,
        "dns_infra": dns_infra,
        "emails": emails,
        "email_domains": email_domains,
        "documents": documents,
        "employees": employees,
        "breaches": breaches,
        "darkweb": darkweb,
        "infrastructure_raw": infrastructure_raw,
        "dns_infra_raw": dns_infra_raw,
        "counts": counts,
    }

    output_files = []

    output_file = company_dir / "aggregate_results.json"
    save_json(output_file, aggregate, indent=2)
    output_files.append(output_file)

    if args.slim:
        slim = build_slim_context(aggregate)
        slim_file = company_dir / "llm_context.json"
        save_json(slim_file, slim, indent=2)
        output_files.append(slim_file)

    print_summary(counts, output_files)


if __name__ == "__main__":
    main()
