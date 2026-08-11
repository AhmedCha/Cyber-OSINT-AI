"""
The one place that defines the full, fixed shape of llm_filtered.json.
"""

from datetime import datetime, timezone
from typing import Any, Dict


def build_output_template(
    company: str, company_slug: str, model_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """Every key here is always present in the final output, regardless of
    which model produced the content. Swapping llama3.1:8b for another model
    changes the *quality* of summaries/verdicts, never the *shape* of the
    output - see llm_filter.py's module docstring for the full design
    principle."""
    return {
        "company": company,
        "company_slug": company_slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_cfg,
        "domains": {"kept": [], "excluded": []},
        "emails": {"kept": [], "excluded": []},
        "employees": {"kept": [], "excluded": []},
        "breaches": {"kept": [], "excluded": []},
        "darkweb": {"kept": [], "excluded": []},
        "infrastructure_insights": {"kept": [], "excluded": []},
        "documents": [],
        "dns_infra": {},
        "email_domains": [],
        "warnings": [],
        "stats": {},
    }
