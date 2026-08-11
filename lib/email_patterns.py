from typing import Any, Dict, List, Optional, Set

# Common non-mailbox subdomain prefixes: MX/mail-server hostnames and
# other infrastructure endpoints. A company's personal mailboxes (the
# ones firstname.lastname@ guessing targets) are virtually never issued
# under one of these - they belong on the root/apex domain instead.
# (llm_filter.py keeps a matching safety-net check in case a candidate
# generated against one of these ever reaches that stage anyway.)
INFRASTRUCTURE_SUBDOMAIN_PREFIXES = (
    "mail.",
    "smtp.",
    "mx.",
    "webmail.",
    "autodiscover.",
    "ns.",
    "www.",
)


def is_infrastructure_hostname(domain: Optional[str]) -> bool:
    """True if `domain` looks like a mail-server/infrastructure hostname
    rather than a domain the company actually issues personal mailboxes
    under (e.g. 'mail.example.com', not 'example.com')."""
    if not domain:
        return False
    return domain.strip().lower().startswith(INFRASTRUCTURE_SUBDOMAIN_PREFIXES)


def filter_personal_mailbox_domains(
    target_domains: List[str],
    confirmed_mailbox_domains: Optional[Set[str]] = None,
) -> List[str]:
    """Drops infrastructure-hostname-looking domains from a domain list
    before personal-email pattern generation, unless a domain has been
    independently confirmed (via `confirmed_mailbox_domains`) as actually
    accepting personal mail despite the subdomain naming. In this
    pipeline's current ordering (candidates are generated before any
    validation runs), that confirmation set will normally be empty - the
    parameter exists so a future caller with that signal can supply it."""
    confirmed = confirmed_mailbox_domains or set()
    kept = []
    for domain in target_domains:
        if domain in confirmed or not is_infrastructure_hostname(domain):
            kept.append(domain)
    return kept


def deduce_patterns(
    discovered_emails: Set[str], target_domains: List[str]
) -> List[str]:
    """Infers email formatting patterns from discovered active emails."""
    inferred = set()
    for email in discovered_emails:
        if "@" not in email:
            continue
        local, domain = email.split("@", 1)
        if domain not in target_domains:
            continue

        if "." in local:
            inferred.add("{first}.{last}")
        elif "_" in local:
            inferred.add("{first}_{last}")
        else:
            inferred.add("{f}{last}")

    # Core default patterns: 3-4 most common real-world conventions
    standard_patterns = [
        "{first}.{last}",
        "{first}{last}",
        "{f}{last}",
        "{f}.{last}",
    ]

    inferred.update(standard_patterns)
    return list(inferred)


def generate_candidate_emails(
    employees: List[Dict[str, Any]],
    target_domains: List[str],
    patterns: List[str],
    confirmed_mailbox_domains: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Generates candidate email dictionaries using employees and inferred patterns.

    Only pattern-generates against domains that plausibly issue personal
    mailboxes - infrastructure hostnames (mail., smtp., mx., ...) are
    skipped by default (see filter_personal_mailbox_domains) since a
    firstname.lastname@ guess against an MX hostname is structurally
    invalid regardless of what SMTP validation later says about it."""
    candidates = []
    mailbox_domains = filter_personal_mailbox_domains(
        target_domains, confirmed_mailbox_domains
    )

    for emp in employees:
        first_name = emp.get("first_name", "").strip().lower()
        last_name = emp.get("last_name", "").strip().lower()

        if not first_name or not last_name:
            full_name = emp.get("name", "").strip().lower()
            parts = full_name.split()
            if len(parts) >= 2:
                first_name, last_name = parts[0], parts[-1]
            else:
                continue

        first_initial = first_name[0] if first_name else ""
        last_initial = last_name[0] if last_name else ""

        for domain in mailbox_domains:
            # Keep track of generated emails for this user to avoid duplicates
            seen_for_user = set()

            for pat in patterns:
                try:
                    local_part = pat.format(
                        first=first_name,
                        last=last_name,
                        f=first_initial,
                        l=last_initial,
                    )
                    email = f"{local_part}@{domain}"

                    if email not in seen_for_user:
                        seen_for_user.add(email)
                        candidates.append(
                            {
                                "email": email,
                                "employee": f"{first_name.capitalize()} {last_name.capitalize()}",
                                "sources": ["Pattern-Generation"],
                            }
                        )
                except KeyError:
                    # Ignore patterns that might require variables we didn't provide
                    continue

    return candidates
