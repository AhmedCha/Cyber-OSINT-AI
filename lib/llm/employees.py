"""
Employees category for the LLM filter pass. Used with
lib/llm/filter_pass.py's run_filter_pass(identifier_field="identifier").

Note the reliability tier (leadership / current_employee / intern /
former_employee / reject) is computed upstream in aggregate_results.py from
each person's actual position history - it already decides keep/exclude
deterministically before this module's LLM pass ever runs (see the
Employees section of llm_filter.py's main()). This module's job is purely
descriptive: turning employee_tier/tier_reason into a natural
connection_to_target phrase and a few key_facts.
"""

from typing import Any, Dict

from lib.llm.utils import as_list

EMPLOYEE_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "identifier": {"type": "string"},
        "keep": {"type": "boolean"},
        "connection_to_target": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["identifier", "keep"],
}

EMPLOYEE_INSTRUCTIONS = (
    "You are reviewing people who have ALREADY been confirmed, from their position "
    "history, to be linked to the target company (current or former employees, "
    "interns). Each person carries a pre-assigned 'employee_tier' (leadership / "
    "current_employee / intern / former_employee) and a 'tier_reason' explaining which "
    "position tied them to the target company - inclusion has already been decided "
    "upstream, so your job here is descriptive, not gating. Always set keep=true. For "
    "each person, in 'connection_to_target' state in one short natural phrase how they "
    "relate to the target company (you can start from tier_reason, e.g. 'current "
    "employee', 'former intern 2019-2021', 'former employee 2022-2023'). In "
    "'key_facts' list up to 4 short, factual bullet points about them drawn ONLY from "
    "the data given (role, notable skills/certifications, tenure) - no speculation "
    "about personality, seniority you cannot verify, or anything not in the input."
)


def compact_employee(e: Dict[str, Any]) -> Dict[str, Any]:
    identifier = e.get("public_identifier") or e.get("name")
    current = e.get("current_position") or {}
    if not isinstance(current, dict):
        current = {"value": current}
    positions = as_list(e.get("all_positions"))
    trimmed_positions = [
        {
            "title": p.get("title"),
            "company_name": p.get("company_name"),
            "start_date": p.get("start_date"),
            "end_date": p.get("end_date"),
        }
        for p in positions[:6]
        if isinstance(p, dict)
    ]
    about = str(e.get("about") or "")[:400]
    return {
        "identifier": identifier,
        "name": e.get("name"),
        "job_title": e.get("job_title"),
        "matched_domain": e.get("matched_domain"),
        # Pre-computed upstream (aggregate_results.py) from the person's
        # position history - already decided this person is kept, given
        # here only so the model can phrase connection_to_target sensibly.
        "employee_tier": e.get("employee_tier"),
        "tier_reason": e.get("tier_reason"),
        "current_position": current,
        "recent_positions": trimmed_positions,
        "about_excerpt": about,
        "services_offered": as_list(e.get("services_offered"))[:6],
    }
