#!/usr/bin/env python3
import argparse
import logging
import uuid
import os
import socket
import requests
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from lib.common import setup_logging, slugify_company
from lib.config import load_env_file
from lib.json_utils import load_json, save_json

logger = logging.getLogger(__name__)

# --- Port 25 connectivity diagnostic ------------------------------------
# Cloud/VPS providers very commonly block outbound port 25 to fight spam.
# If that's happening here, Reacher's direct SMTP checks will fail at the
# network/connection level for EVERY address - which looks identical, at a
# glance, to "these mailboxes don't exist." We test against the TARGET
# company's own MX host(s), not generic public providers - Gmail/Outlook
# reachability tells you nothing about whether you can reach some small
# target domain's actual mail server, and a large provider's inbound
# filtering behaves nothing like a target's.
#
# dnspython is NOT in requirements.txt, so MX lookup is done with a small
# stdlib-only (socket + struct) DNS query rather than adding a dependency
# for one lookup.
PUBLIC_DNS_RESOLVERS = ["1.1.1.1", "8.8.8.8"]
DNS_QUERY_TIMEOUT = 5.0
HUNTER_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"


def _encode_dns_name(name: str) -> bytes:
    encoded = b""
    for label in name.rstrip(".").split("."):
        encoded += bytes([len(label)]) + label.encode("ascii")
    return encoded + b"\x00"


def _decode_dns_name(data: bytes, offset: int) -> "tuple[str, int]":
    """Decodes a (possibly compressed, per RFC 1035 4.1.4) DNS name starting
    at `offset`. Returns (name, offset_after_name_in_the_ORIGINAL_record) -
    following a compression pointer must not move the caller's read
    position past the pointer itself."""
    labels = []
    pos = offset
    after_pointer: "int | None" = None
    while True:
        length = data[pos]
        if length == 0:
            pos += 1
            break
        if (length & 0xC0) == 0xC0:
            pointer = ((length & 0x3F) << 8) | data[pos + 1]
            if after_pointer is None:
                after_pointer = pos + 2
            pos = pointer
            continue
        pos += 1
        labels.append(data[pos : pos + length].decode("ascii", errors="replace"))
        pos += length
    end = after_pointer if after_pointer is not None else pos
    return ".".join(labels), end


def _parse_mx_response(data: bytes, expected_txid: int) -> List[str]:
    import struct

    txid, flags, qdcount, ancount = struct.unpack(">HHHH", data[0:8])
    if txid != expected_txid:
        raise ValueError("DNS response transaction ID mismatch")
    rcode = flags & 0x000F
    if rcode != 0:
        # NXDOMAIN, SERVFAIL, etc. - a real DNS answer, just "no MX here."
        return []

    offset = 12
    for _ in range(qdcount):
        _, offset = _decode_dns_name(data, offset)
        offset += 4  # qtype + qclass

    hostnames = []
    for _ in range(ancount):
        _, offset = _decode_dns_name(data, offset)
        rtype, _rclass, _ttl, rdlength = struct.unpack(
            ">HHIH", data[offset : offset + 10]
        )
        rdata_start = offset + 10
        if rtype == 15:  # MX
            exchange, _ = _decode_dns_name(data, rdata_start + 2)
            hostnames.append(exchange.rstrip("."))
        offset = rdata_start + rdlength

    return hostnames


def resolve_mx_hostnames(domain: str, timeout: float = DNS_QUERY_TIMEOUT) -> List[str]:
    """Minimal stdlib-only MX lookup: sends a raw DNS query (type=MX) over
    UDP straight to a public resolver. Raises on total DNS failure (no
    resolver reachable / malformed response); returns [] if DNS resolved
    fine but the domain genuinely has no MX records (NXDOMAIN, no answers)."""
    import random
    import struct

    txid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    question = _encode_dns_name(domain) + struct.pack(">HH", 15, 1)  # MX, IN
    query = header + question

    last_error: "Exception | None" = None
    for resolver in PUBLIC_DNS_RESOLVERS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(query, (resolver, 53))
                response, _ = sock.recvfrom(1024)
            return _parse_mx_response(response, txid)
        except (OSError, ValueError) as e:
            last_error = e
            continue

    raise RuntimeError(
        f"MX lookup for {domain} failed against all resolvers {PUBLIC_DNS_RESOLVERS}: {last_error}"
    )


def _load_target_domains(company_slug: str) -> List[str]:
    """Reads output/{company_slug}/domains.json - the same domain list every
    other pipeline stage already consumes - and returns the domain names
    that have actually been validated as belonging to the target, when that
    signal is present in the record (falls back to all listed domains
    otherwise)."""
    domains_file = Path("output") / company_slug / "domains.json"
    if not domains_file.exists():
        logger.warning(
            f"{domains_file} not found - can't determine the target's real MX host(s) "
            f"for the port-25 probe. Run the domain-discovery stage first."
        )
        return []

    try:
        raw = load_json(domains_file)
    except Exception as e:
        logger.warning(f"Failed to read {domains_file}: {e}")
        return []

    if not isinstance(raw, list):
        logger.warning(f"{domains_file} did not contain a JSON list - skipping.")
        return []

    domains: List[str] = []
    for item in raw:
        if isinstance(item, str):
            domains.append(item)
        elif isinstance(item, dict):
            domain = item.get("domain")
            if not domain:
                continue
            if "validated" in item and not item.get("validated"):
                continue
            domains.append(domain)
    return domains


def check_domain_catchall(domain: str) -> bool:
    """Pre-checks if a domain is a catch-all by testing via Reacher and ZeroBounce."""
    # 1. Primary Check: Local Reacher Container
    dummy_email = f"doesnotexist_{uuid.uuid4().hex[:8]}@{domain}"
    logger.info(f"Running Reacher catch-all pre-check for domain {domain}...")

    url = "http://localhost:8081/v0/check_email"
    payload = {"to_email": dummy_email}

    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()

        is_reachable = data.get("is_reachable", "unknown")
        smtp_info = data.get("smtp", {}) or {}

        if is_reachable == "safe" or smtp_info.get("is_catch_all", False):
            logger.warning(
                f"Catch-all detected by Reacher for domain {domain}! "
                f"SMTP checks will be bypassed for all candidates."
            )
            return True

    except requests.exceptions.RequestException as e:
        logger.warning(f"Reacher catch-all pre-check failed for {domain}: {e}")

    # 2. Secondary Check: ZeroBounce API
    zb_key = os.environ.get("ZEROBOUNCE_KEY")
    if zb_key:
        # Testing a common role-based address rather than a randomized one
        zb_email = f"contact@{domain}"
        logger.info(
            f"Running ZeroBounce catch-all check for {domain} using {zb_email}..."
        )

        zb_url = "https://api.zerobounce.net/v2/validate"
        params = {"api_key": zb_key, "email": zb_email}

        try:
            zb_response = requests.get(zb_url, params=params, timeout=10)
            zb_response.raise_for_status()
            zb_data = zb_response.json()

            zb_status = zb_data.get("status", "")
            zb_sub_status = zb_data.get("sub_status", "")

            # Catch-all can be indicated in the primary status or as a sub-status
            if zb_status == "catch-all" or "catch_all" in zb_sub_status:
                logger.warning(
                    f"ZeroBounce detected catch-all (Status: {zb_status}, Sub: {zb_sub_status}) "
                    f"for domain {domain}! SMTP checks bypassed."
                )
                return True
            else:
                logger.info(f"Pre-check complete: domain {domain} is NOT a catch-all.")

        except Exception as e:
            logger.error(f"ZeroBounce API request failed for {domain}: {e}")
    else:
        logger.info(
            f"Pre-check complete: Reacher found no catch-all for {domain} (ZeroBounce skipped, no key)."
        )

    return False


def check_port25_connectivity(
    company_slug: str, timeout: float = 5.0
) -> Dict[str, Any]:
    """Startup probe: resolves the TARGET company's own MX host(s) from
    output/{company_slug}/domains.json and attempts a raw TCP connect to
    each on port 25. This does NOT try to work around a block in any way -
    it only exists to surface the problem clearly so "unknown"/0-confidence
    results aren't mistaken for "these emails don't exist."

    Returns a dict with a "status" key:
      - "no_domains": domains.json missing/empty - probe couldn't run at all.
      - "mx_lookup_failed": DNS/MX resolution itself failed or found no MX
        records - a DIFFERENT failure mode from port-25 blocking, and it
        means the probe below never actually ran.
      - "port25_blocked": MX host(s) resolved fine, but port 25 couldn't be
        reached on any of them.
      - "reachable": port 25 connected successfully to at least one MX host.
    """
    domains = _load_target_domains(company_slug)
    if not domains:
        return {"status": "no_domains"}

    mx_hosts: List[str] = []
    mx_lookup_errors: Dict[str, str] = {}
    for domain in domains:
        try:
            hosts = resolve_mx_hostnames(domain, timeout=timeout)
            if hosts:
                mx_hosts.extend(hosts)
            else:
                mx_lookup_errors[domain] = (
                    "no MX records found (NXDOMAIN or empty answer)"
                )
        except Exception as e:
            mx_lookup_errors[domain] = str(e)

    if not mx_hosts:
        return {
            "status": "mx_lookup_failed",
            "domains": domains,
            "mx_lookup_errors": mx_lookup_errors,
        }

    for host in mx_hosts:
        try:
            with socket.create_connection((host, 25), timeout=timeout):
                return {"status": "reachable", "mx_hosts": mx_hosts}
        except OSError:
            continue

    return {"status": "port25_blocked", "mx_hosts": mx_hosts}


# --- Hunter.io fallback ---------------------------------------------------
# Secondary validation signal used only when Reacher's own SMTP check comes
# back "unknown"/inconclusive. Hunter verifies from ITS OWN servers, so it
# sidesteps any port-25 blocking on this machine/network entirely.
# Endpoint/response schema per Hunter's v2 API docs (hunter.io/api/email-verifier):
#   GET https://api.hunter.io/v2/email-verifier?email=...&api_key=...
#   -> {"data": {"status": "valid"|"accept_all"|"webmail"|"disposable"|
#                "invalid"|"unknown", "score": 0-100, "accept_all": bool, ...}}
#   A 202 means verification is still running server-side (poll later);
#   we just treat that as unresolved for this run rather than blocking on it.

# Confidence we assign to each Hunter status. These are deliberately a
# notch below what a direct, successful Reacher SMTP check ("safe" -> 1.0)
# would give, since Hunter is a secondary/indirect signal layered on top
# of (not replacing) Reacher.
HUNTER_STATUS_CONFIDENCE = {
    "valid": 0.9,
    "accept_all": 0.5,
    "webmail": 0.5,
    "disposable": 0.1,
    "invalid": 0.0,
    "unknown": 0.0,
    "blocked": 0.0,
}

HUNTER_STATUS_TO_LOCAL_STATUS = {
    "valid": "deliverable",
    "accept_all": "risky",
    "webmail": "risky",
    "disposable": "invalid",
    "invalid": "invalid",
    "unknown": "unknown",
    "blocked": "unknown",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OSINT Stage: Email Asset Validation")
    parser.add_argument("--company", required=True, help="Target company name folder")
    return parser.parse_args()


def run_spiderfoot_fallback(email: str) -> Dict[str, Any]:
    """Passive fallback validation using SpiderFoot account enumeration."""
    logger.info(f"Triggering SpiderFoot fallback validation for {email}...")
    result = {"status": "unknown", "confidence": 0.0, "is_catch_all": False}

    command = [
        "docker",
        "compose",
        "run",
        "--rm",
        "--no-TTY",
        "spiderfoot",
        "-m",
        "sfp_accounts",
        "-s",
        email,
        "-q",
    ]

    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=45)
        output = proc.stdout.lower()

        # Parse SpiderFoot's output for success indicators
        if "account" in output or "found" in output or "valid" in output:
            result["status"] = "deliverable"
            result["confidence"] = 0.8  # Slightly lower confidence than direct SMTP
    except subprocess.TimeoutExpired:
        logger.error(f"SpiderFoot fallback timed out for {email}.")
    except Exception as e:
        logger.error(f"SpiderFoot execution failed: {e}")

    return result


def run_hunter_verifier_fallback(
    email: str, reacher_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Secondary signal via Hunter.io's Email Verifier API..."""
    result = dict(reacher_result)

    hunter_api_key = os.environ.get("HUNTER_KEY")

    if not hunter_api_key:
        return run_spiderfoot_fallback(email)

    try:
        response = requests.get(
            HUNTER_VERIFY_URL,
            params={"email": email, "api_key": hunter_api_key},
            timeout=20,  # Hunter's docs note verification can run up to ~20s server-side
        )
        if response.status_code == 202:
            logger.warning(
                f"Hunter verification for {email} still processing (202 - poll later). "
                f"Treating as unresolved for this run."
            )
            return run_spiderfoot_fallback(email)
        response.raise_for_status()
        data = response.json().get("data", {})
    except Exception as e:
        logger.error(f"Hunter.io fallback verification failed for {email}: {e}")
        return run_spiderfoot_fallback(email)

    hunter_status = str(data.get("status", "unknown")).lower()
    local_status = HUNTER_STATUS_TO_LOCAL_STATUS.get(hunter_status, "unknown")

    if local_status == "unknown":
        # Hunter couldn't resolve it either - don't invent confidence,
        # fall through to the last-resort passive fallback instead.
        return run_spiderfoot_fallback(email)

    result["status"] = local_status
    result["confidence"] = HUNTER_STATUS_CONFIDENCE.get(hunter_status, 0.0)
    # Combine the catch-all signal from both sources instead of letting
    # Hunter silently override a positive finding Reacher already made.
    result["is_catch_all"] = bool(result.get("is_catch_all")) or bool(
        data.get("accept_all")
    )
    result["fallback_source"] = "hunter_verifier"
    logger.info(
        f"Hunter.io fallback resolved {email}: status={hunter_status} -> "
        f"{local_status} (confidence={result['confidence']}, port-25-independent signal)."
    )
    return result


def run_fallback_validation(email: str, base_result: Dict[str, Any]) -> Dict[str, Any]:
    """Picks the best available fallback when Reacher's direct SMTP check
    can't produce a confident answer..."""
    if os.environ.get("HUNTER_KEY"):
        return run_hunter_verifier_fallback(email, base_result)
    return run_spiderfoot_fallback(email)


def run_check_if_email_exists(email: str) -> Dict[str, Any]:
    logger.debug(f"Validating {email}...")
    result = {
        "status": "unknown",
        "confidence": 0.0,
        "is_catch_all": False,
        "smtp_connect_failed": False,
    }

    url = "http://localhost:8081/v0/check_email"
    payload = {"to_email": email}

    try:
        # Reduced timeout to 5 seconds
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()

        is_reachable = data.get("is_reachable", "unknown")
        smtp_info = data.get("smtp", {}) or {}

        if smtp_info.get("is_catch_all", False):
            result["is_catch_all"] = True

        # Reacher's own smtp.can_connect_smtp distinguishes "we connected to
        # the mail server and it said no such mailbox" from "we couldn't
        # even open a connection" - the latter is exactly what outbound
        # port-25 blocking looks like, and it's easy to misread as the
        # former if you only look at is_reachable. Track it so it can be
        # surfaced in the run summary alongside the startup probe.
        if "can_connect_smtp" in smtp_info and not smtp_info.get("can_connect_smtp"):
            result["smtp_connect_failed"] = True

        if is_reachable == "safe":
            result["status"] = "deliverable"
            result["confidence"] = 1.0
        elif is_reachable == "risky":
            result["status"] = "risky"
            result["confidence"] = 0.5
        elif is_reachable == "invalid":
            result["status"] = "invalid"
            result["confidence"] = 0.0
        else:
            # Fallback for 'unknown' status - prefer Hunter.io (unaffected
            # by local port-25 blocking) over SpiderFoot when configured.
            return run_fallback_validation(email, result)

    except requests.exceptions.ConnectionError:
        logger.error(
            f"Failed to connect to validation API for {email}. Is the reacher container running on port 8081?"
        )
        result["smtp_connect_failed"] = True
        return run_fallback_validation(email, result)
    except requests.exceptions.HTTPError as e:
        logger.error(f"Validation HTTP error for {email}: {e}")
        return run_fallback_validation(email, result)
    except requests.exceptions.RequestException as e:
        logger.error(f"Validation request failed (or timed out) for {email}: {e}")
        return run_fallback_validation(email, result)
    except Exception as e:
        logger.error(f"Unexpected error validating {email}: {e}")
        return run_fallback_validation(email, result)

    return result


def validate_emails(
    candidate_emails: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:

    # 1. Extract unique domains from the candidate pool
    unique_domains = set()
    for item in candidate_emails:
        email = item.get("email", "")
        if "@" in email:
            unique_domains.add(email.split("@")[-1])

    # 2. Run the catch-all pre-check per domain
    catchall_domains = set()
    for domain in unique_domains:
        if check_domain_catchall(domain):
            catchall_domains.add(domain)

    validated_results = []
    stats = {
        "deliverable": 0,
        "risky": 0,
        "invalid": 0,
        "unknown": 0,
        "smtp_connect_failures": 0,
        "smtp_inconclusive_catchall": 0,  # New stat tracker
    }

    # 3. Process candidates
    for item in candidate_emails:
        email = item.get("email")
        if not email:
            continue

        domain = email.split("@")[-1]

        if domain in catchall_domains:
            # Bypass individual SMTP check entirely
            item["validation_status"] = "smtp_inconclusive_catchall"
            item["confidence"] = (
                0.0  # Defer to non-SMTP signals (LLM filtering) downstream
            )
            item["is_catch_all"] = True
            stats["smtp_inconclusive_catchall"] += 1
            validated_results.append(item)
            continue

        # Normal validation for non-catch-all domains
        validation = run_check_if_email_exists(email)

        item["validation_status"] = validation["status"]
        item["confidence"] = validation["confidence"]
        item["is_catch_all"] = validation["is_catch_all"]
        if validation.get("fallback_source"):
            item["fallback_source"] = validation["fallback_source"]

        stats[validation["status"]] += 1
        if validation.get("smtp_connect_failed"):
            stats["smtp_connect_failures"] += 1
        validated_results.append(item)

    validated_results.sort(key=lambda x: x["confidence"], reverse=True)
    return validated_results, stats


def print_validation_summary(
    validated_count: int, stats: Dict[str, int], output_file: Path
) -> None:
    print("\n" + "=" * 50)
    print("           EMAIL VALIDATION SUMMARY")
    print("=" * 50)
    print(f"Total Candidates Processed: {validated_count}")
    print("-" * 50)
    print(f"Deliverable               : {stats['deliverable']}")
    print(f"Risky (Catch-all/Greylist): {stats['risky']}")
    print(f"Invalid                   : {stats['invalid']}")
    print(f"Unknown (Timeout/Error)   : {stats['unknown']}")
    print(f"Bypassed (Catch-all setup): {stats.get('smtp_inconclusive_catchall', 0)}")
    if stats.get("smtp_connect_failures"):
        print("-" * 50)
        print(
            f"SMTP connection failures  : {stats['smtp_connect_failures']} "
            f"(couldn't even open a connection - see port-25 warning above if shown)"
        )
    print("=" * 50)
    print(f"\nFinal output written to: {output_file.resolve()}")


def main() -> None:
    load_env_file()
    setup_logging()
    args = parse_arguments()
    company_slug = slugify_company(args.company)

    probe = check_port25_connectivity(company_slug)
    if probe["status"] == "no_domains":
        logger.warning(
            "Skipping the port-25 connectivity probe: no domains.json found for "
            "this company (or it was empty), so there's no target MX host to test "
            "against yet. Run the domain-discovery stage first if you want this "
            "pre-flight check."
        )
    elif probe["status"] == "mx_lookup_failed":
        logger.warning(
            f"Could not resolve MX records for the target domain(s) {probe['domains']} "
            f"({probe['mx_lookup_errors']}). This is a DNS/MX-lookup problem, NOT a "
            f"port-25-blocked problem - the port-25 probe couldn't run at all. Check "
            f"that the domain(s) in domains.json are correct and actually have mail "
            f"service before trusting (or distrusting) email validation results."
        )
    elif probe["status"] == "port25_blocked":
        logger.warning(
            f"Outbound port 25 appears to be BLOCKED on this network: resolved the "
            f"target's own mail server(s) ({', '.join(probe['mx_hosts'])}) but couldn't "
            f"open a connection to any of them on port 25. Reacher's direct SMTP checks "
            f"depend on port 25 - if it's blocked, EVERY email will likely come back as "
            f"'unknown'/0-confidence regardless of whether the address is actually valid. "
            f"This is a network-level restriction (common on cloud/VPS providers) and "
            f"can't be worked around from inside this script - fix it at the network/"
            f"firewall level, or use a host/relay that permits outbound port 25. "
            + (
                "HUNTER_KEY is set, so Hunter.io's API will be used as a fallback signal "
                "for inconclusive results this run - but until port 25 is unblocked, "
                "treat Reacher-only results as unreliable."
                if os.environ.get("HUNTER_KEY")  # <--- Check env directly
                else "No HUNTER_KEY is configured, so results will fall back to the "
                "passive SpiderFoot check only, which is a much weaker signal - set "
                "HUNTER_KEY to get a real fallback."
            )
        )
    # probe["status"] == "reachable": port 25 works against the target's own
    # MX host(s) - nothing to warn about.

    output_dir = Path("output") / company_slug
    input_file = output_dir / "candidate_emails.json"
    output_file = output_dir / "validated_emails.json"

    if not input_file.exists():
        logger.error(
            f"Input file {input_file} not found! Please run email_discovery.py first."
        )
        return

    candidate_emails = load_json(input_file)
    if not isinstance(candidate_emails, list):
        logger.error("Invalid input format. Expected a JSON list of candidate emails.")
        return

    logger.info(f"Loaded {len(candidate_emails)} candidates for validation.")

    validated_results, stats = validate_emails(candidate_emails)
    save_json(output_file, validated_results)
    print_validation_summary(len(validated_results), stats, output_file)


if __name__ == "__main__":
    main()
