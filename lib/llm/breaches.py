"""
Breaches category for the LLM filter pass. Used with
lib/llm/filter_pass.py's run_filter_pass(identifier_field="email").
"""

from typing import Any, Dict, Optional

from lib.llm.utils import as_list

BREACH_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "email": {"type": "string"},
        "keep": {"type": "boolean"},
        "exposure_summary": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["email", "keep"],
}

BREACH_INSTRUCTIONS = (
    "You are reviewing breach/exposure lookup results for target-company emails. Each "
    "email's 'findings' list can contain two very different kinds of entries:\n"
    "1. kind='structured_breach_report' - a real breach-database lookup result (e.g. "
    "from XposedOrNot/Apify) with fields like status, breach_count, breach_names, "
    "risk_label, risk_score, and breach_details (each with breach name, xposed_date, "
    "xposed_records, xposed_data, password_risk). If status is 'breached' or "
    "breach_names is non-empty, this IS a genuine exposure - keep=true, and write "
    "'exposure_summary' as a concise sentence naming the breaches, how many records/what "
    "kind of data was exposed, and the risk_label - using ONLY the fields given.\n"
    "2. kind='raw_event' with source='spiderfoot' (or similar) - usually just SpiderFoot "
    "confirming an email address exists (type='Email Address', module='SpiderFoot UI', "
    "data equal to the email itself), with no actual breach name/date attached. This is "
    "NOT a real exposure - keep=false with a note explaining it's just an existence check, "
    "UNLESS its 'data' field clearly names a real breach/leak source, in which case treat "
    "it like a genuine finding.\n"
    "If an email has both a genuine structured_breach_report AND spiderfoot noise, keep=true "
    "and summarize only the genuine findings - do not mention the noise in exposure_summary."
)


def _is_structured_breach_report(data: Any) -> bool:
    """Detects the Apify/XposedOrNot-style breach report shape (has
    breachNames/status/riskLabel etc.) vs a SpiderFoot raw-event dump or
    some other unknown shape."""
    return isinstance(data, dict) and (
        "breachNames" in data or "status" in data or "riskScore" in data
    )


def _extract_structured_breach_summary(
    data: Dict[str, Any], source: Optional[str]
) -> Dict[str, Any]:
    """Pulls the genuinely high-value fields out of a large Apify/XposedOrNot
    breach report, dropping the verbose nested 'analytics' tree (industry
    stat breakdowns, yearwise histograms, treemap data, etc.) that adds
    nothing for an OSINT report but would otherwise dominate the LLM's
    context budget on slow hardware."""
    breach_details = as_list(data.get("breachDetails"))
    trimmed_details = []
    for bd in breach_details[:10]:
        if not isinstance(bd, dict):
            continue
        trimmed_details.append(
            {
                "breach": bd.get("breach"),
                "industry": bd.get("industry"),
                "xposed_date": bd.get("xposed_date"),
                "xposed_records": bd.get("xposed_records"),
                "xposed_data": bd.get("xposed_data"),
                "password_risk": bd.get("password_risk"),
                "verified": bd.get("verified"),
            }
        )
    return {
        "source": source,
        "kind": "structured_breach_report",
        "status": data.get("status"),
        "breach_count": data.get("breachCount"),
        "breach_names": data.get("breachNames"),
        "risk_label": data.get("riskLabel"),
        "risk_score": data.get("riskScore"),
        "paste_count": data.get("pasteCount"),
        "breach_details": trimmed_details,
    }


def compact_breach(b: Dict[str, Any]) -> Dict[str, Any]:
    entries = []
    for group in as_list(b.get("breaches"))[:5]:
        if not isinstance(group, dict):
            # Unexpected shape (e.g. a bare string/number breach entry) -
            # surface it as-is rather than crashing or silently dropping it.
            entries.append({"source": None, "kind": "unknown", "value": group})
            continue

        source = group.get("source") or group.get("type")
        data = group.get("data")

        if isinstance(data, dict) and _is_structured_breach_report(data):
            # Real breach report (e.g. Apify/XposedOrNot) - extract the
            # high-value summary fields, drop the noisy analytics tree.
            entries.append(_extract_structured_breach_summary(data, source))
            continue

        data_items = as_list(data)[:5]
        if not data_items:
            entries.append(
                {"source": source, "kind": "empty", "raw_type": group.get("type")}
            )
            continue

        for item in data_items:
            if isinstance(item, dict):
                # SpiderFoot-style {type, module, data} raw event, or some
                # other dict shape we don't specifically recognize.
                entries.append(
                    {
                        "source": source,
                        "kind": "raw_event",
                        "type": item.get("type", group.get("type")),
                        "module": item.get("module"),
                        "data": item["data"] if "data" in item else item,
                    }
                )
            else:
                entries.append(
                    {
                        "source": source,
                        "kind": "raw_event",
                        "type": group.get("type"),
                        "module": None,
                        "data": item,
                    }
                )

    return {
        "email": b.get("email"),
        "services": b.get("services", []),
        "findings": entries,
    }
