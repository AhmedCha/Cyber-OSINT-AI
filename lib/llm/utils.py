"""
Small helpers shared by more than one category module (lib/llm/employees.py,
lib/llm/breaches.py, ...). Kept separate rather than duplicated in each, or
tucked inside whichever category happened to need it first.
"""

from typing import Any, List


def as_list(x: Any) -> List[Any]:
    """Defensively normalize a value that's supposed to be a list but,
    depending on the lookup service / scraper that produced it, might arrive
    as a dict, a scalar, or missing entirely."""
    if isinstance(x, list):
        return x
    if x is None:
        return []
    return [x]
