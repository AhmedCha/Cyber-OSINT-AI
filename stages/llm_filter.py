#!/usr/bin/env python3
"""
OSINT Stage: LLM Filter

Takes the company's aggregate_results.json (produced by aggregate_results.py)
and runs it through a local LLM (Ollama, llama3.1:8b by default - see
install_tools.sh) to:

  1. Read each discovered document's extracted text and produce a
     one-sentence usability summary (for later report inclusion decisions).
  2. Strip noise from the structured OSINT data (duplicate/irrelevant
     SpiderFoot findings, generic ISP/infra metadata, low-value pattern-
     generated emails, etc.) while keeping everything transparent - nothing
     is silently deleted, it is labeled "excluded" with a reason.

DESIGN PRINCIPLE - the LLM never owns the schema or the facts:
  - The LLM is only ever asked to return a verdict (keep/exclude + a short
    note) about a record that ALREADY exists in the aggregate data, using
    that record's own identifier (email / domain / employee id).
  - Every verdict is grounded against the original input set after parsing.
    Any identifier the model invents that doesn't match an input record is
    dropped and logged as a hallucination - it can never appear in the
    output.
  - If the model omits a record entirely, or fails after retries, the
    pipeline "fails open": the original record is kept, unmodified, with a
    warning attached - we never let an LLM hiccup silently delete real
    OSINT data.
  - The Python code always emits the same fixed top-level structure
    (company / domains / emails / employees / breaches / documents /
    dns_infra / warnings / stats / model), regardless of which model
    produced the content. Swapping llama3.1:8b for another model changes
    the *quality* of summaries/verdicts, never the *shape* of the output.

CODE LAYOUT:
  This file is the thin CLI entry point only - argument parsing, main()
  orchestration, and the final summary print. Everything category-specific
  (compact_fn / verdict schema / instructions / any deterministic backstop)
  lives one module per category under lib/llm/ - see lib/llm/__init__.py for
  the full map. If you're diagnosing or changing how one category (e.g.
  emails) is judged, go straight to lib/llm/emails.py instead of searching
  this file.

PERFORMANCE / LOW-RESOURCE HARDWARE NOTES:
  - List-based passes (domains/emails/employees/breaches) are sent in small
    batches (--batch-size, default 8) rather than one giant call, since a
    single call with dozens of records can exceed the model's context
    window (Ollama's default num_ctx is commonly 4096) and take a very long
    time to generate on CPU/iGPU-only hardware, risking timeouts.
  - --debug streams tokens live to the terminal as the model generates them
    (Ollama's stream=true API), so you can see it's actually working instead
    of staring at a blank terminal until a timeout.
  - --num-gpu lets you force layers onto CPU (0 = fully CPU) per request,
    useful for testing whether GPU offload is the source of instability
    (e.g. display blackouts) without touching your global Ollama service
    config.

Usage:
    python -m stages.llm_filter --company "Resys Consultants"
    python -m stages.llm_filter --company "Resys Consultants" --debug --batch-size 5 --timeout 900
    python -m stages.llm_filter --company "Resys Consultants" --num-gpu 0   # force CPU-only, for stability testing
"""

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List

from lib.common import setup_logging, slugify_company
from lib.json_utils import load_json, save_json

from lib.llm.client import check_ollama_available
from lib.llm.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_DOC_CHARS,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_TIMEOUT,
    BASE_SYSTEM_PROMPT,
)
from lib.llm.filter_pass import run_filter_pass
from lib.llm.output import build_output_template

from lib.llm.domains import (
    compact_domain,
    DOMAIN_VERDICT_ITEM_SCHEMA,
    DOMAIN_INSTRUCTIONS,
)
from lib.llm.infrastructure import (
    compact_infrastructure,
    INFRASTRUCTURE_VERDICT_ITEM_SCHEMA,
    INFRASTRUCTURE_INSTRUCTIONS,
)
from lib.llm.emails import (
    compact_email,
    EMAIL_VERDICT_ITEM_SCHEMA,
    EMAIL_INSTRUCTIONS,
    enforce_email_tier_rules,
    enforce_catchall_tagging,
    reconcile_unverifiable_pattern_guesses,
    is_infrastructure_hostname_email,
)
from lib.llm.employees import (
    compact_employee,
    EMPLOYEE_VERDICT_ITEM_SCHEMA,
    EMPLOYEE_INSTRUCTIONS,
)
from lib.llm.breaches import (
    compact_breach,
    BREACH_VERDICT_ITEM_SCHEMA,
    BREACH_INSTRUCTIONS,
)
from lib.llm.darkweb import (
    compact_darkweb,
    DARKWEB_VERDICT_ITEM_SCHEMA,
    DARKWEB_INSTRUCTIONS,
)
from lib.llm.documents import summarize_document
from lib.db import get_db_connection, upsert_records

try:
    from lib.config import load_env_file
except ImportError:  # pragma: no cover - keep this stage runnable standalone

    def load_env_file() -> None:
        return None


logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OSINT Stage: LLM-based noise filtering & document summarization"
    )
    parser.add_argument("--company", required=True, help="Target company name")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag")
    parser.add_argument(
        "--ollama-host", default=DEFAULT_OLLAMA_HOST, help="Ollama API base URL"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (0 = deterministic)",
    )
    parser.add_argument(
        "--max-doc-chars",
        type=int,
        default=DEFAULT_MAX_DOC_CHARS,
        help="Max characters of extracted document text sent to the model per document",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Per-request timeout in seconds (raise this on slow/CPU-only hardware)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Records per LLM call for domains/emails/employees/breaches passes "
        "(lower this if calls are timing out)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Stream the model's raw output live to the terminal as it generates, "
        "with per-call timing/throughput stats",
    )
    parser.add_argument(
        "--num-gpu",
        type=int,
        default=None,
        help="Ollama num_gpu option: number of layers to offload to GPU for this request "
        "(0 = force fully CPU-only, useful to test if GPU offload is unstable on this "
        "machine). Omit to use the Ollama service's default.",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="Ollama num_ctx option: context window size for this request. Omit to use "
        "the model's default (commonly 4096).",
    )
    parser.add_argument(
        "--num-predict",
        type=int,
        default=None,
        help="Ollama num_predict option: caps max tokens generated per response, guards "
        "against runaway generation on slow hardware.",
    )
    return parser.parse_args()


def print_summary(
    stats: Dict[str, int], warnings: List[str], output_file: Path
) -> None:
    print("\n" + "=" * 60)
    print("               LLM FILTER SUMMARY")
    print("=" * 60)
    for label, value in stats.items():
        print(f"{label:<28}: {value}")
    if warnings:
        print("-" * 60)
        print(f"Warnings ({len(warnings)}):")
        for w in warnings[:10]:
            print(f"  - {w}")
        if len(warnings) > 10:
            print(
                f"  ... and {len(warnings) - 10} more (see 'warnings' in output file)"
            )
    print("-" * 60)
    print(f"Written: {output_file.resolve()}")
    print("=" * 60 + "\n")


def main() -> None:
    setup_logging()
    load_env_file()
    args = parse_arguments()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    company_slug = slugify_company(args.company)
    company_dir = Path("output") / company_slug
    input_file = company_dir / "aggregate_results.json"

    if not input_file.exists():
        logger.error(
            f"Input file not found: {input_file}. Run aggregate_results.py first."
        )
        raise SystemExit(1)

    aggregate = load_json(input_file)
    if not aggregate:
        logger.error(f"{input_file} is empty or invalid.")
        raise SystemExit(1)

    if not check_ollama_available(args.ollama_host):
        logger.error(
            f"Ollama is not reachable at {args.ollama_host}. "
            "Start it (see install_tools.sh) before running this stage."
        )
        raise SystemExit(1)

    model_cfg = {"provider": "ollama", "name": args.model, "host": args.ollama_host}
    output = build_output_template(args.company, company_slug, model_cfg)
    warnings: List[str] = []

    common_kwargs = dict(
        host=args.ollama_host,
        model=args.model,
        temperature=args.temperature,
        timeout=args.timeout,
        warnings=warnings,
        batch_size=args.batch_size,
        debug=args.debug,
        num_gpu=args.num_gpu,
        num_ctx=args.num_ctx,
        num_predict=args.num_predict,
    )

    # --- Domains -----------------------------------------------------
    logger.info("Filtering domains...")
    kept, excluded = run_filter_pass(
        "domains",
        aggregate.get("domains", []),
        "domain",
        compact_domain,
        DOMAIN_VERDICT_ITEM_SCHEMA,
        BASE_SYSTEM_PROMPT,
        DOMAIN_INSTRUCTIONS,
        **common_kwargs,
    )
    output["domains"] = {"kept": kept, "excluded": excluded}

    # --- Infrastructure Insights ---------------------------------------
    logger.info("Filtering infrastructure raw data...")
    infra_raw_dict = aggregate.get("infrastructure_raw", {})
    dns_infra_raw_dict = aggregate.get("dns_infra_raw", {})
    infra_records = []

    # Merge by target domain: infrastructure_raw carries theHarvester/
    # SpiderFoot, dns_infra_raw carries raw_amass_records/
    # raw_certspotter_issuances + the surfaced_network_footprint (IP/ASN/
    # ISP) block from dns_infra_discovery.py. A domain can appear in
    # either or both. (Field names confirmed against a real
    # dns_infra_raw.json sample.)
    for domain in sorted(set(infra_raw_dict) | set(dns_infra_raw_dict)):
        rec = dict(infra_raw_dict.get(domain) or {})
        dns_data = dns_infra_raw_dict.get(domain) or {}
        if isinstance(dns_data, dict):
            for key in (
                "raw_amass_records",
                "raw_certspotter_issuances",
                "surfaced_network_footprint",
            ):
                if key in dns_data:
                    rec[key] = dns_data[key]
        rec["target_domain"] = domain
        infra_records.append(rec)

    infra_kept, infra_excluded = run_filter_pass(
        "infrastructure",
        infra_records,
        "target_domain",
        compact_infrastructure,
        INFRASTRUCTURE_VERDICT_ITEM_SCHEMA,
        BASE_SYSTEM_PROMPT,
        INFRASTRUCTURE_INSTRUCTIONS,
        **common_kwargs,
    )
    output["infrastructure_insights"] = {"kept": infra_kept, "excluded": infra_excluded}

    # --- Emails --------------------------------------------------------
    logger.info("Filtering emails...")
    all_email_records = aggregate.get("emails", [])

    # Structural exclusion, not a content judgment: candidates generated
    # against an MX/infrastructure hostname are invalid regardless of what
    # validation found. The real fix is upstream in lib/email_patterns.py
    # (skip generating these at all) - this is a safety net for anything
    # that reached this stage anyway, so it never gets an LLM call.
    infra_email_records = [
        e for e in all_email_records if is_infrastructure_hostname_email(e.get("email"))
    ]
    valid_domain_email_records = [
        e
        for e in all_email_records
        if not is_infrastructure_hostname_email(e.get("email"))
    ]

    excluded = []
    for e in infra_email_records:
        annotated = dict(e)
        annotated["_llm_verdict"] = {
            "keep": False,
            "tier": "noise",
            "note": "Candidate generated against an infrastructure/mail-server "
            "hostname, not a domain the company issues personal mailboxes "
            "under - structurally invalid regardless of validation result.",
        }
        excluded.append(annotated)

    kept, llm_excluded = run_filter_pass(
        "emails",
        valid_domain_email_records,
        "email",
        compact_email,
        EMAIL_VERDICT_ITEM_SCHEMA,
        BASE_SYSTEM_PROMPT,
        EMAIL_INSTRUCTIONS,
        **common_kwargs,
    )
    excluded.extend(llm_excluded)
    enforce_email_tier_rules(kept, warnings)
    enforce_email_tier_rules(excluded, warnings)
    enforce_catchall_tagging(kept, warnings)
    enforce_catchall_tagging(excluded, warnings)
    # Must run AFTER the two tier-correction backstops above, since it
    # relies on their corrected tiers (not the model's raw ones) to decide
    # what actually needs to move from kept to excluded.
    kept, excluded = reconcile_unverifiable_pattern_guesses(kept, excluded, warnings)
    output["emails"] = {"kept": kept, "excluded": excluded}

    # --- Employees -------------------------------------------------
    logger.info("Filtering employees...")

    def employee_identifier(e: Dict[str, Any]) -> str:
        return e.get("public_identifier") or e.get("name") or ""

    employees_with_id = []
    for e in aggregate.get("employees", []):
        e2 = dict(e)
        e2["public_identifier"] = employee_identifier(e)
        # "identifier" (not "public_identifier") is the key name used by both
        # compact_employee() and EMPLOYEE_VERDICT_ITEM_SCHEMA - it must match
        # exactly what's passed as identifier_field below, or every verdict
        # silently fails to ground and the whole pass fails open.
        e2["identifier"] = e2["public_identifier"]
        employees_with_id.append(e2)

    # The reliability tier computed upstream (aggregate_results.py, from
    # each person's actual position history at the target company) now
    # decides keep/exclude deterministically instead of leaving that call
    # to the model. "reject" - no position ever placed them at the target
    # company - is excluded without spending an LLM call on it. The other
    # four tiers (leadership / current_employee / intern / former_employee)
    # are always kept; the LLM pass below only adds descriptive facts for
    # them and never gates inclusion.
    reject_records = [
        e for e in employees_with_id if e.get("employee_tier") == "reject"
    ]
    tiered_records = [
        e for e in employees_with_id if e.get("employee_tier") != "reject"
    ]

    excluded = []
    for e in reject_records:
        annotated = dict(e)
        annotated["_llm_verdict"] = {
            "keep": False,
            "tier": "reject",
            "note": e.get("tier_reason")
            or "No evidence in the data that this person worked at the target company.",
        }
        excluded.append(annotated)

    llm_kept, llm_excluded = run_filter_pass(
        "employees",
        tiered_records,
        "identifier",
        compact_employee,
        EMPLOYEE_VERDICT_ITEM_SCHEMA,
        BASE_SYSTEM_PROMPT,
        EMPLOYEE_INSTRUCTIONS,
        **common_kwargs,
    )

    kept = []
    for e in llm_kept + llm_excluded:
        # Every non-reject-tier person is kept regardless of what the model
        # itself set for keep - the tier already made that call. The model's
        # verdict here is only ever consulted for its descriptive fields.
        verdict = dict(e.get("_llm_verdict") or {})
        verdict["keep"] = True
        verdict["tier"] = e.get("employee_tier")
        e2 = dict(e)
        e2["_llm_verdict"] = verdict
        kept.append(e2)

    output["employees"] = {"kept": kept, "excluded": excluded}

    # --- Breaches --------------------------------------------------
    logger.info("Filtering breaches...")
    kept, excluded = run_filter_pass(
        "breaches",
        aggregate.get("breaches", []),
        "email",
        compact_breach,
        BREACH_VERDICT_ITEM_SCHEMA,
        BASE_SYSTEM_PROMPT,
        BREACH_INSTRUCTIONS,
        **common_kwargs,
    )
    output["breaches"] = {"kept": kept, "excluded": excluded}

    # --- Dark web ----------------------------------------------------
    logger.info("Filtering dark web scan results...")
    darkweb_records = []
    for d in aggregate.get("darkweb", []):
        d2 = dict(d)
        d2["target_key"] = f"{d.get('target')}||{d.get('target_type')}"
        darkweb_records.append(d2)

    with_mentions = [d for d in darkweb_records if d.get("mentions")]
    without_mentions = [d for d in darkweb_records if not d.get("mentions")]

    dw_kept, dw_excluded = run_filter_pass(
        "darkweb",
        with_mentions,
        "target_key",
        compact_darkweb,
        DARKWEB_VERDICT_ITEM_SCHEMA,
        BASE_SYSTEM_PROMPT,
        DARKWEB_INSTRUCTIONS,
        **common_kwargs,
    )
    # Targets with zero mentions need no LLM judgment - there's nothing to
    # filter, so they're kept automatically without spending a call on them.
    for d in without_mentions:
        annotated = dict(d)
        annotated["_llm_verdict"] = {"keep": True, "note": "no_dark_web_mentions_found"}
        dw_kept.append(annotated)
    output["darkweb"] = {"kept": dw_kept, "excluded": dw_excluded}

    # --- Documents (one LLM call per document) ----------------------
    documents = aggregate.get("documents", [])
    logger.info(f"Summarizing {len(documents)} document(s)...")
    doc_summaries = []
    for i, doc in enumerate(documents, 1):
        logger.info(f"  [{i}/{len(documents)}] {doc.get('filename')}")
        doc_summaries.append(
            summarize_document(
                doc,
                args.company,
                args.ollama_host,
                args.model,
                args.max_doc_chars,
                args.temperature,
                args.timeout,
                warnings,
                debug=args.debug,
                num_gpu=args.num_gpu,
                num_ctx=args.num_ctx,
                num_predict=args.num_predict,
            )
        )
    output["documents"] = doc_summaries

    # --- DNS infra: passthrough (no LLM value yet, kept for future noise-stripping) ---
    output["dns_infra"] = aggregate.get("dns_infra", {})

    # Per-domain catch-all summary (computed in aggregate_results.py from
    # email_validation.py's pre-check) - a deterministic fact, not
    # something an LLM verdict is needed for, so it's passed straight
    # through for generate_report.py to use.
    output["email_domains"] = aggregate.get("email_domains", [])

    output["warnings"] = warnings
    output["stats"] = {
        "domains_kept": len(output["domains"]["kept"]),
        "domains_excluded": len(output["domains"]["excluded"]),
        "emails_kept": len(output["emails"]["kept"]),
        "emails_excluded": len(output["emails"]["excluded"]),
        "employees_kept": len(output["employees"]["kept"]),
        "employees_excluded": len(output["employees"]["excluded"]),
        "breaches_kept": len(output["breaches"]["kept"]),
        "breaches_excluded": len(output["breaches"]["excluded"]),
        "darkweb_kept": len(output["darkweb"]["kept"]),
        "darkweb_excluded": len(output["darkweb"]["excluded"]),
        "darkweb_with_mentions": len(with_mentions),
        "infrastructure_kept": len(output["infrastructure_insights"]["kept"]),
        "infrastructure_excluded": len(output["infrastructure_insights"]["excluded"]),
        "documents_summarized": sum(1 for d in doc_summaries if d["summary"]),
        "documents_errored": sum(1 for d in doc_summaries if d["error"]),
        "warning_count": len(warnings),
    }

    output_file = company_dir / "llm_filtered.json"
    save_json(output_file, output, indent=2)

    # Save to Database
    try:
        conn = get_db_connection()

        db_mapping = [
            ("reviewed_domains", output.get("domains", {}).get("kept", []), "domain"),
            (
                "reviewed_infrastructure",
                output.get("infrastructure_insights", {}).get("kept", []),
                "target_domain",
            ),
            ("reviewed_emails", output.get("emails", {}).get("kept", []), "email"),
            (
                "reviewed_employees",
                output.get("employees", {}).get("kept", []),
                "identifier",
            ),
            ("reviewed_breaches", output.get("breaches", {}).get("kept", []), "email"),
            (
                "reviewed_darkweb",
                output.get("darkweb", {}).get("kept", []),
                "target_key",
            ),
            ("reviewed_documents", output.get("documents", []), "filename"),
        ]

        for table, records, key_field in db_mapping:
            if records:
                upsert_records(conn, table, company_slug, records, key_field)

        conn.close()
        logger.info("Successfully upserted LLM reviewed records to the database.")
    except Exception as e:
        logger.warning(f"Failed to upsert reviewed records to database: {e}")

    print_summary(output["stats"], warnings, output_file)


if __name__ == "__main__":
    main()
