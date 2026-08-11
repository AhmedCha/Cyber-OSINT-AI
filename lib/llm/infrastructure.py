"""
Infrastructure Insights category for the LLM filter pass. Used with
lib/llm/filter_pass.py's run_filter_pass(identifier_field="target_domain").

Records here are merged, per target domain, from two upstream raw sources
before reaching this module (see llm_filter.py's main(), Infrastructure
Insights section):
  - infrastructure_raw (domain_discovery_raw.json): theHarvester + SpiderFoot
  - dns_infra_raw (dns_infra_raw.json, from dns_infra_discovery.py):
      - raw_amass_records: Amass subdomain enumeration hits
      - raw_certspotter_issuances: certificate-transparency issuance
        records (confirmed against a real sample - each has dns_names,
        issuer.{friendly_name,name}, not_before/not_after, revoked, plus
        several *_sha256 hash fields with no report value)
      - surfaced_network_footprint: resolved IP/ASN/ISP data
compact_infrastructure() below surfaces fields from both, trimming the
certspotter records down to what's actually useful for a report (drops
id/tbs_sha256/cert_sha256/pubkey_sha256 - internal certificate-transparency
bookkeeping, not OSINT-relevant) the same way compact_breach() trims a
verbose analytics tree out of raw breach reports elsewhere in this package.
"""

from typing import Any, Dict, List

INFRASTRUCTURE_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "target_domain": {"type": "string"},
        "keep": {"type": "boolean"},
        "ips": {"type": "array", "items": {"type": "string"}},
        "asns": {"type": "array", "items": {"type": "string"}},
        "whois_data": {"type": "array", "items": {"type": "string"}},
        "banners_and_tech": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["target_domain", "keep"],
}

INFRASTRUCTURE_INSTRUCTIONS = (
    "You are reviewing raw infrastructure discovery data for a target domain, gathered "
    "from several tools: theHarvester and SpiderFoot (general recon), Amass "
    "(amass_hits: subdomain enumeration) and CertSpotter (certspotter_issuances: "
    "certificate-transparency log issuance records - each with dns_names covered, the "
    "issuer name, validity dates, and revocation status), and a "
    "surfaced_network_footprint block (resolved IP addresses, ASN/network ownership, and "
    "ISP data derived from DNS records). Extract genuinely useful structured findings - "
    "real IP addresses, ASN/network ownership info, ISP ownership, notable WHOIS fields, "
    "TLS certificate issuer names and the subdomains they cover, interesting webserver or "
    "tech-stack banners - from ANY of these sources, not just theHarvester/SpiderFoot. A "
    "certificate issuance record is worth surfacing mainly for its dns_names (can reveal "
    "subdomains not found elsewhere) and issuer - ignore certificate hash/ID fields "
    "entirely, they carry no OSINT value. Explicitly ignore purely internal bookkeeping "
    "events, duplicate/redundant entries, and empty fields. Set keep=true if you found "
    "actionable infrastructure data from any source given. Set keep=false ONLY if the "
    "data is entirely noise, empty, or uninformative across every source given."
)


def _compact_certspotter_issuances(
    issuances: Any, limit: int = 15
) -> List[Dict[str, Any]]:
    """Strips certificate-transparency bookkeeping fields (id, tbs_sha256,
    cert_sha256, pubkey_sha256, issuer.pubkey_sha256) that a real
    dns_infra_raw.json sample confirmed are present but carry no OSINT
    value - keeps only what's actually useful: which hostnames a
    certificate covers, who issued it, its validity window, and whether
    it's been revoked."""
    if not isinstance(issuances, list):
        return []
    trimmed = []
    for entry in issuances[:limit]:
        if not isinstance(entry, dict):
            continue
        issuer = entry.get("issuer") or {}
        trimmed.append(
            {
                "dns_names": entry.get("dns_names", []),
                "issuer": issuer.get("friendly_name") or issuer.get("name"),
                "not_before": entry.get("not_before"),
                "not_after": entry.get("not_after"),
                "revoked": entry.get("revoked"),
            }
        )
    return trimmed


def compact_infrastructure(i: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "target_domain": i.get("target_domain"),
        "theHarvester": i.get("theHarvester", {}),
        "SpiderFoot": i.get("SpiderFoot", []),
        "amass_hits": i.get("raw_amass_records", []),
        "certspotter_issuances": _compact_certspotter_issuances(
            i.get("raw_certspotter_issuances")
        ),
        "surfaced_network_footprint": i.get("surfaced_network_footprint", {}),
    }
