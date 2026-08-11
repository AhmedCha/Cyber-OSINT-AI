"""
Domains category for the LLM filter pass. Used with lib/llm/filter_pass.py's
run_filter_pass(identifier_field="domain").
"""

from typing import Any, Dict

DOMAIN_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "domain": {"type": "string"},
        "keep": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["domain", "keep"],
}

DOMAIN_INSTRUCTIONS = (
    "You are reviewing candidate domains discovered by automated domain-enumeration "
    "tools (theHarvester, SpiderFoot, certificate transparency logs) for a target company. "
    "Mark keep=true for domains that plausibly belong to or are operated by the target "
    "organization. Mark keep=false ONLY for domains that are clearly unrelated false "
    "positives (e.g. an unrelated third-party service, a coincidental substring match, "
    "a parked/placeholder domain) - do not exclude a domain just because you are unsure."
)


def compact_domain(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "domain": d.get("domain"),
        "sources": d.get("sources", []),
        "dns_validated": d.get("dns_validated"),
    }
