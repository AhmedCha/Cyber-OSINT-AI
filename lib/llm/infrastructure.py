"""
Infrastructure Insights category for the LLM filter pass. Used with
lib/llm/filter_pass.py's run_filter_pass(identifier_field="target_domain").
"""

from typing import Any, Dict

INFRASTRUCTURE_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "target_domain": {"type": "string"},
        "keep": {"type": "boolean"},
        "ips": {"type": "array", "items": {"type": "string"}},
        "asns": {"type": "array", "items": {"type": "string"}},
        "whois_data": {"type": "array", "items": {"type": "string"}},
        "banners_and_tech": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["target_domain", "keep"],
}

INFRASTRUCTURE_INSTRUCTIONS = (
    "You are reviewing raw infrastructure discovery data (from theHarvester and SpiderFoot) "
    "for a target domain. Extract genuinely useful structured findings like real IP addresses, "
    "ASN/network ownership info, notable WHOIS fields, and interesting webserver or tech-stack "
    "banners. Explicitly ignore purely internal bookkeeping events, duplicate/redundant entries, "
    "and empty fields. Set keep=true if you found actionable infrastructure data. Set keep=false "
    "ONLY if the data is entirely noise, empty, or uninformative."
)


def compact_infrastructure(i: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "target_domain": i.get("target_domain"),
        "theHarvester": i.get("theHarvester", {}),
        "SpiderFoot": i.get("SpiderFoot", []),
    }
