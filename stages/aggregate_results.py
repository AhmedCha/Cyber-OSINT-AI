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
from typing import Any, Dict, List, Optional

from lib.common import setup_logging, slugify_company
from lib.json_utils import load_json, save_json

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
        for src in item.get("services_checked", []):
            if src not in entry["services"]:
                entry["services"].append(src)

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


def _current_position(
    employee: Dict[str, Any], matched_domain: str
) -> Optional[Dict[str, Any]]:
    for pos in employee.get("all_positions", []) or []:
        company_name = (pos.get("company_name") or "").lower()
        if matched_domain and matched_domain.split(".")[
            0
        ].lower() in company_name.replace("-", " ").replace("_", " "):
            return pos
    return None


def aggregate_employees(company_dir: Path) -> List[Dict[str, Any]]:
    """Load the enriched employees.json (falls back to employees_raw.json
    only if the enriched file is absent), deduplicated by LinkedIn public
    identifier / profile URL, trimmed to the fields relevant for the next
    LLM stage (full position history and education are kept, but redundant
    raw-scrape wrapper fields are dropped)."""
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
        service = item.get("service")
        if service and service not in entry["services"]:
            entry["services"].append(service)
    return sorted(merged.values(), key=lambda x: x["email"])


# =====================================================================
# BREACHES
# =====================================================================


def aggregate_darkweb(company_dir: Path) -> List[Dict[str, Any]]:
    return load_list(company_dir / "darkweb.json")


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
                "matched_domain": e.get("matched_domain"),
            }
            for e in aggregate["employees"]
        ],
        "breach_count": len(aggregate["breaches"]),
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
    documents = aggregate_documents(company_dir)
    employees = aggregate_employees(company_dir)
    breaches = aggregate_breaches(company_dir)
    darkweb = aggregate_darkweb(company_dir)
    dns_infra = load_dict(company_dir / "dns_infra.json")

    counts = {
        "Domains": len(domains),
        "Emails": len(emails),
        "  - deliverable": sum(
            1 for e in emails if e["validation_status"] == "deliverable"
        ),
        "Documents": len(documents),
        "  - found on disk": sum(1 for d in documents if d["file_exists"]),
        "  - content verified": sum(1 for d in documents if d["content_verified"]),
        "Employees": len(employees),
        "Breach records": len(breaches),
    }

    aggregate: Dict[str, Any] = {
        "company": args.company,
        "company_slug": company_slug,
        "domains": domains,
        "dns_infra": dns_infra,
        "emails": emails,
        "documents": documents,
        "employees": employees,
        "breaches": breaches,
        "darkweb": darkweb,
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
