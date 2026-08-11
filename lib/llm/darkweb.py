"""
Dark web category for the LLM filter pass. Used with
lib/llm/filter_pass.py's run_filter_pass(identifier_field="target_key").

Note llm_filter.py's main() only runs the LLM pass on targets that actually
have mentions - targets with zero mentions need no judgment call and are
kept automatically without spending a call on them (see the Dark web
section of main()).
"""

from typing import Any, Dict

from lib.llm.utils import as_list

DARKWEB_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "target_key": {"type": "string"},
        "keep": {"type": "boolean"},
        "exposure_summary": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["target_key", "keep"],
}

DARKWEB_INSTRUCTIONS = (
    "You are reviewing dark web scan results for the target company (its domain, its "
    "name, and named individuals linked to it). Each record has a 'mentions' list of "
    "actual hits found on onion search engines / dark web indexes. Some mentions may be "
    "irrelevant false positives (e.g. an unrelated page that happens to contain a common "
    "word from the target's name). Set keep=true if the mentions genuinely appear to "
    "reference the target company or person, and write 'exposure_summary' describing "
    "what was found using ONLY the data given. Set keep=false ONLY if the mentions are "
    "clearly unrelated false positives - when unsure, keep=true."
)


def compact_darkweb(d: Dict[str, Any]) -> Dict[str, Any]:
    mentions = as_list(d.get("mentions"))
    return {
        "target_key": f"{d.get('target')}||{d.get('target_type')}",
        "target": d.get("target"),
        "target_type": d.get("target_type"),
        "modules_checked": d.get("modules_checked", []),
        "mentions": mentions[:10],
    }
