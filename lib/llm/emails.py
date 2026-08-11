"""
Emails category for the LLM filter pass. Used with lib/llm/filter_pass.py's
run_filter_pass(identifier_field="email").

Three deterministic, code-level backstops live here alongside the LLM pass
(all applied AFTER the model's verdicts are merged in, since prompt
instructions alone aren't reliable enough to guarantee any of these rules
for an 8B model or a future swapped-in one):

  - enforce_email_tier_rules: confidence/validation_status must dominate
    tier, never how plausible the naming pattern looks.
  - enforce_catchall_tagging: candidates at a confirmed catch-all domain
    (see lib/email_patterns.py / email_validation.py) get an explicit
    catchall_unverifiable flag and a forced 'speculative' tier, since SMTP
    validation is fundamentally inconclusive for them, not merely
    unconfirmed.
  - reconcile_unverifiable_pattern_guesses: the two backstops above only
    ever relabel verdict['tier'] in place - they never touch
    verdict['keep'], so on their own they cannot move a record between the
    kept/excluded lists llm_filter.py's main() already split. This step is
    what actually enforces the underlying rule: an email that is
    tier=speculative/noise, confidence=0, AND has no real (non-pattern-
    generated) discovery source gets moved from kept to excluded, with
    keep=False and a clear note. Must run AFTER the other two so it sees
    their corrected tiers, not the model's raw ones.

_is_infrastructure_hostname_email() is the llm_filter-stage safety net for
the root-cause fix in lib/email_patterns.py (candidates shouldn't be
generated against an MX/infra hostname at all) - it excludes any that
reached this stage anyway, structurally, without spending an LLM call.
"""

from typing import Any, Dict, List, Optional, Tuple

from lib.email_patterns import is_infrastructure_hostname

EMAIL_TIERS = {"confirmed", "likely", "speculative", "noise"}

# Case-insensitive source markers that indicate an email candidate exists
# ONLY because it was pattern-guessed (firstname.lastname@domain), never
# independently discovered by a real recon tool (theHarvester, SpiderFoot).
_PATTERN_GENERATED_SOURCE_MARKERS = {"pattern-generation", "manual-generation"}

EMAIL_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "email": {"type": "string"},
        "keep": {"type": "boolean"},
        "tier": {"type": "string", "enum": sorted(EMAIL_TIERS)},
        "note": {"type": "string"},
    },
    "required": ["email", "keep", "tier"],
}

EMAIL_INSTRUCTIONS = (
    "You are reviewing candidate email addresses for a target company. Assign a tier "
    "based PRIMARILY on validation_status and confidence, not on how plausible the "
    "naming pattern looks:\n"
    "- 'confirmed': validation_status='deliverable' (SMTP validation actually succeeded).\n"
    "- 'likely': validation_status='unknown' but confidence is GREATER THAN 0 (some "
    "partial validation signal exists, e.g. a catch-all domain).\n"
    "- 'speculative': confidence is 0 (SMTP validation was never run, was inconclusive, "
    "or failed) - use this even for a common, plausible-looking naming pattern like "
    "first.last@ or f.last@. A confidence of 0 means the address is UNVALIDATED, not "
    "that the pattern is unlikely, but you must not call it 'likely' - that would "
    "overstate certainty that validation never actually confirmed.\n"
    "- 'noise': generic role addresses with no named employee, or a duplicate pattern "
    "already covered by a better candidate for the same person.\n"
    "NEVER assign 'confirmed' or 'likely' when confidence is 0, regardless of how "
    "standard the naming pattern looks. Set keep=false ONLY for tier='noise' entries "
    "that add no value - when in doubt, keep=true."
)


def compact_email(e: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "email": e.get("email"),
        "employee": e.get("employee"),
        "sources": e.get("sources", []),
        "validation_status": e.get("validation_status"),
        "confidence": e.get("confidence"),
        "is_catch_all": e.get("is_catch_all"),
    }


def enforce_email_tier_rules(
    records: List[Dict[str, Any]], warnings: List[str]
) -> None:
    """Code-level backstop for the confidence-vs-tier rule, applied AFTER the
    model's verdicts are merged in. Prompt instructions alone aren't
    reliable enough to guarantee this - an 8B model (or a future swapped-in
    model) can still call an unvalidated, confidence=0 address 'likely'
    because the naming pattern looks plausible. This corrects that
    deterministically, in-place, on both kept and excluded records, and
    logs every correction for transparency."""
    for r in records:
        verdict = r.get("_llm_verdict")
        if not isinstance(verdict, dict):
            continue
        tier = verdict.get("tier")
        if tier not in ("confirmed", "likely"):
            continue

        confidence = r.get("confidence")
        validation_status = r.get("validation_status")

        if tier == "confirmed" and validation_status != "deliverable":
            verdict["tier"] = "speculative" if (confidence or 0) == 0 else "likely"
            warnings.append(
                f"[emails] Downgraded '{r.get('email')}' from tier=confirmed to "
                f"tier={verdict['tier']}: validation_status is '{validation_status}', not 'deliverable'."
            )
        elif tier == "likely" and (confidence or 0) == 0:
            verdict["tier"] = "speculative"
            warnings.append(
                f"[emails] Downgraded '{r.get('email')}' from tier=likely to tier=speculative: "
                f"confidence is 0 (unvalidated / SMTP inconclusive), regardless of naming pattern."
            )


def enforce_catchall_tagging(
    records: List[Dict[str, Any]], warnings: List[str]
) -> None:
    """Code-level backstop, same style/placement as enforce_email_tier_rules
    above: every candidate that hit a confirmed catch-all domain
    (validation_status == 'smtp_inconclusive_catchall', set upstream by
    email_validation.py's per-domain pre-check) gets an explicit
    catchall_unverifiable=True flag, and its tier is forced to
    'speculative' if the model didn't already land there - SMTP
    validation is fundamentally inconclusive for it, not merely an
    unconfirmed guess like an address at a normal domain."""
    for r in records:
        if r.get("validation_status") != "smtp_inconclusive_catchall":
            continue
        verdict = r.get("_llm_verdict")
        if not isinstance(verdict, dict):
            continue
        verdict["catchall_unverifiable"] = True
        if verdict.get("tier") != "speculative":
            old_tier = verdict.get("tier")
            verdict["tier"] = "speculative"
            warnings.append(
                f"[emails] Downgraded '{r.get('email')}' from tier={old_tier} to "
                f"tier=speculative: domain is confirmed catch-all, so SMTP "
                f"validation is fundamentally inconclusive for every candidate at it."
            )


def _is_pattern_generated_only(sources: Any) -> bool:
    """True only if `sources` is non-empty and EVERY entry is a
    pattern-guess marker - never true for an empty/missing sources list,
    since that's not evidence of anything, and never true if even one
    real discovery source (theHarvester, SpiderFoot, ...) is present."""
    if not isinstance(sources, list) or not sources:
        return False
    return all(
        isinstance(s, str) and s.strip().lower() in _PATTERN_GENERATED_SOURCE_MARKERS
        for s in sources
    )


def reconcile_unverifiable_pattern_guesses(
    kept: List[Dict[str, Any]],
    excluded: List[Dict[str, Any]],
    warnings: List[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Final reconciliation pass - call this AFTER enforce_email_tier_rules
    and enforce_catchall_tagging have both already run on `kept`/`excluded`,
    so it sees their corrected tiers rather than the model's raw ones.

    Those two functions only ever relabel verdict['tier'] in place; by
    design neither touches verdict['keep'], so a tier correction alone can
    never move a record between the kept/excluded lists llm_filter.py's
    main() already split on the model's original keep/exclude call. This
    function is what actually enforces the underlying rule: an email that
    is tier in ('speculative', 'noise'), confidence==0, AND has no real
    (non-pattern-generated) discovery source should end up excluded, not
    merely tier-relabeled while still presented as a kept finding. A
    record with at least one real discovery source stays kept even at
    tier=speculative/confidence=0, per spec.

    Returns new (kept, excluded) lists - does not mutate the input lists
    in place, since entries move between them."""
    still_kept: List[Dict[str, Any]] = []
    reconciled: List[Dict[str, Any]] = []

    for r in kept:
        verdict = r.get("_llm_verdict")
        if not isinstance(verdict, dict):
            still_kept.append(r)
            continue

        tier = verdict.get("tier")
        confidence = r.get("confidence") or 0
        if (
            tier in ("speculative", "noise")
            and confidence == 0
            and _is_pattern_generated_only(r.get("sources"))
        ):
            verdict["keep"] = False
            addition = (
                f"Reconciled from kept to excluded: tier={tier}, confidence=0, and "
                f"every source is pattern-generated ({r.get('sources')}) - no "
                f"independent discovery confirms this address exists."
            )
            existing_note = verdict.get("note")
            verdict["note"] = (
                f"{existing_note} {addition}" if existing_note else addition
            )
            warnings.append(f"[emails] {addition} ({r.get('email')})")
            reconciled.append(r)
        else:
            still_kept.append(r)

    return still_kept, excluded + reconciled


def _email_domain(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def is_infrastructure_hostname_email(email: Optional[str]) -> bool:
    """Safety net for the root-cause fix in lib/email_patterns.py
    (candidates shouldn't be generated against an MX/infra hostname at
    all) - excludes any that reached this stage anyway, structurally,
    without waiting on an LLM content judgment."""
    return is_infrastructure_hostname(_email_domain(email))
