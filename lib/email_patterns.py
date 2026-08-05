from typing import Any, Dict, List, Set


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

    # Combine inferred patterns with an expanded list of standard corporate permutations
    standard_patterns = [
        "{first}.{last}",
        "{first}{last}",
        "{f}{last}",
        "{f}.{last}",
        "{first}.{l}",
        "{first}{l}",
        "{first}_{last}",
        "{last}.{first}",
        "{last}{first}",
        "{first}",
        "{last}",
    ]

    inferred.update(standard_patterns)
    return list(inferred)


def generate_candidate_emails(
    employees: List[Dict[str, Any]],
    target_domains: List[str],
    patterns: List[str],
) -> List[Dict[str, Any]]:
    """Generates candidate email dictionaries using employees and inferred patterns."""
    candidates = []

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

        for domain in target_domains:
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
