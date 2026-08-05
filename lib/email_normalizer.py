import re
from typing import Optional

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def normalize_email(email: Optional[str]) -> str:
    """Strips whitespace and converts email to lower case."""
    if not email:
        return ""
    return email.strip().lower()


def is_valid_email(email: Optional[str]) -> bool:
    """Validates email format against standard email regex."""
    if not email:
        return False
    normalized = normalize_email(email)
    return bool(EMAIL_REGEX.match(normalized))
