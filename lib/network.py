import re
import socket
from typing import Optional
from urllib.parse import urlparse


def is_valid_domain_syntax(domain: Optional[str]) -> bool:
    """Validates domain string against standard host name grammar."""
    if not domain:
        return False
    pattern = re.compile(
        r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
    )
    return bool(pattern.match(domain.strip().lower()))


def normalize_domain(url_or_domain: Optional[str]) -> str:
    """Extracts clean host domain from a URL or raw string, stripping protocol and 'www.'."""
    if not url_or_domain:
        return ""

    text = url_or_domain.strip().lower()
    if not text.startswith(("http://", "https://")):
        text = f"http://{text}"

    try:
        parsed = urlparse(text)
        hostname = parsed.hostname or ""
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
    except Exception:
        return ""


def normalize_subdomain(line: Optional[str], root_domain: str) -> Optional[str]:
    """Parses raw stdout/tool output lines to extract subdomains matching the target root domain."""
    if not line or not root_domain:
        return None

    text = line.strip().lower()
    pattern = re.compile(r"([a-z0-9._-]+\." + re.escape(root_domain.lower()) + r")")
    match = pattern.search(text)

    if match:
        candidate = match.group(1).strip(".")
        if is_valid_domain_syntax(candidate):
            return candidate

    return None


def domain_matches_company(
    domain: str, company_name: str, abbreviation: str = ""
) -> bool:
    """Lexically verifies if a domain candidate matches the target company name or abbreviation."""
    if not domain or not company_name:
        return False

    clean_company = re.sub(r"[^a-z0-9]", "", company_name.lower())
    domain_root = domain.split(".")[0].lower()
    clean_domain_root = re.sub(r"[^a-z0-9]", "", domain_root)

    if not clean_company or not clean_domain_root:
        return False

    # Check 1 & 2: concatenated full name logic
    is_match = (clean_domain_root in clean_company) or (
        clean_company in clean_domain_root
    )

    # Check 3: Generated abbreviation (lowercased) appears in the domain string
    if abbreviation and (abbreviation.lower() in domain.lower()):
        is_match = True

    return is_match


def resolves_dns(domain: str) -> bool:
    """Checks if a domain resolves via DNS A/AAAA record lookup."""
    if not domain or not is_valid_domain_syntax(domain):
        return False

    try:
        socket.gethostbyname(domain)
        return True
    except (socket.gaierror, socket.herror, TimeoutError):
        return False
