#!/usr/bin/env python3
import os
import argparse
import secrets
from dotenv import dotenv_values

# ==============================================================================
# CONFIGURATION / OUTPUT PATHS
# ==============================================================================
# Adjust these paths to point to your actual Docker volume mounts
THEHARVESTER_OUT_PATH = "./volumes/theharvester/api-keys.yaml"
SPIDERFOOT_OUT_PATH = "./volumes/spiderfoot/SpiderFoot.cfg"
SEARXNG_OUT_PATH = "./volumes/searxng/settings.yml"
DOTENV_PATH = ".env"

# ==============================================================================
# HARDCODED SCHEMAS & MAPPINGS
# ==============================================================================

# Mapping for theHarvester: service_name -> { yaml_key: env_var_name }
# Based exactly on the provided api-keys.yaml template schema.
THEHARVESTER_MAPPING = {
    "bevigil": {"key": "BEVIGIL_KEY"},
    "bitbucket": {"key": "BITBUCKET_KEY"},
    "brave": {"key": "BRAVE_KEY"},
    "bufferoverun": {"key": "BUFFEROVERUN_KEY"},
    "builtwith": {"key": "BUILTWITH_KEY"},
    "censys": {"id": "CENSYS_ID", "secret": "CENSYS_SECRET"},
    "criminalip": {"key": "CRIMINALIP_KEY"},
    "dehashed": {"key": "DEHASHED_KEY"},
    "dnsdumpster": {"key": "DNSDUMPSTER_KEY"},
    "dymo": {"key": "DYMO_KEY"},
    "fofa": {"key": "FOFA_KEY", "email": "FOFA_EMAIL"},
    "fullhunt": {"key": "FULLHUNT_KEY"},
    "github": {"key": "GITHUB_KEY"},
    "hackertarget": {"key": "HACKERTARGET_KEY"},
    "haveibeenpwned": {"key": "HIBP_KEY"},
    "hunter": {"key": "HUNTER_KEY"},
    "hunterhow": {"key": "HUNTERHOW_KEY"},
    "intelx": {"key": "INTELX_KEY"},
    "leakix": {"key": "LEAKIX_KEY"},
    "leaklookup": {"key": "LEAKLOOKUP_KEY"},
    "mojeek": {"key": "MOJEEK_KEY"},
    "netlas": {"key": "NETLAS_KEY"},
    "onyphe": {"key": "ONYPHE_KEY"},
    "pentestTools": {"key": "PENTESTTOOLS_KEY"},
    "projectDiscovery": {"key": "PROJECTDISCOVERY_KEY"},
    "rocketreach": {"key": "ROCKETREACH_KEY"},
    "securityscorecard": {"key": "SECURITYSCORECARD_KEY"},
    "securityTrails": {"key": "SECURITYTRAILS_KEY"},
    "sherlockeye": {"key": "SHERLOCKEYE_KEY"},
    "shodan": {"key": "SHODAN_KEY"},
    "tomba": {"key": "TOMBA_KEY", "secret": "TOMBA_SECRET"},
    "venacus": {"key": "VENACUS_KEY"},
    "virustotal": {"key": "VIRUSTOTAL_KEY"},
    "whoisxml": {"key": "WHOISXML_KEY"},
    "windvane": {"key": "WINDVANE_KEY"},
    "zoomeye": {"key": "ZOOMEYE_KEY"},
}

# Mapping for SpiderFoot: SF_config_key -> env_var_name
SPIDERFOOT_MAPPING = {
    "sfp_abstractapi:companyenrichment_api_key": "ABSTRACTAPI_COMPANYENRICHMENT_KEY",
    "sfp_abstractapi:ipgeolocation_api_key": "ABSTRACTAPI_IPGEOLOCATION_KEY",
    "sfp_abstractapi:phonevalidation_api_key": "ABSTRACTAPI_PHONEVALIDATION_KEY",
    "sfp_abuseipdb:api_key": "ABUSEIPDB_KEY",
    "sfp_abusix:api_key": "ABUSIX_KEY",
    "sfp_alienvault:api_key": "ALIENVAULT_KEY",
    "sfp_badpackets:api_key": "BADPACKETS_KEY",
    "sfp_binaryedge:binaryedge_api_key": "BINARYEDGE_KEY",
    "sfp_bitcoinabuse:api_key": "BITCOINABUSE_KEY",
    "sfp_bitcoinwhoswho:api_key": "BITCOINWHOSWHO_KEY",
    "sfp_botscout:api_key": "BOTSCOUT_KEY",
    "sfp_builtwith:api_key": "BUILTWITH_KEY",
    "sfp_c99:api_key": "C99_KEY",
    "sfp_censys:censys_api_key_secret": "CENSYS_SECRET",
    "sfp_censys:censys_api_key_uid": "CENSYS_ID",
    "sfp_certspotter:api_key": "CERTSPOTTER_KEY",
    "sfp_circllu:api_key_login": "CIRCLLU_LOGIN",
    "sfp_circllu:api_key_password": "CIRCLLU_PASSWORD",
    "sfp_citadel:api_key": "CITADEL_KEY",
    "sfp_clearbit:api_key": "CLEARBIT_KEY",
    "sfp_dehashed:api_key": "DEHASHED_KEY",
    "sfp_dehashed:api_key_username": "DEHASHED_USERNAME",
    "sfp_dnsdb:api_key": "DNSDB_KEY",
    "sfp_emailcrawlr:api_key": "EMAILCRAWLR_KEY",
    "sfp_emailrep:api_key": "EMAILREP_KEY",
    "sfp_etherscan:api_key": "ETHERSCAN_KEY",
    "sfp_focsec:api_key": "FOCSEC_KEY",
    "sfp_fraudguard:fraudguard_api_key_account": "FRAUDGUARD_ACCOUNT",
    "sfp_fraudguard:fraudguard_api_key_password": "FRAUDGUARD_PASSWORD",
    "sfp_fullcontact:api_key": "FULLCONTACT_KEY",
    "sfp_fullhunt:api_key": "FULLHUNT_KEY",
    "sfp_googlemaps:api_key": "GOOGLEMAPS_KEY",
    "sfp_googlesafebrowsing:api_key": "GOOGLE_SAFEBROWSING_KEY",
    "sfp_grayhatwarfare:api_key": "GRAYHATWARFARE_KEY",
    "sfp_greynoise:api_key": "GREYNOISE_KEY",
    "sfp_haveibeenpwned:api_key": "HIBP_KEY",
    "sfp_honeypot:api_key": "HONEYPOT_KEY",
    "sfp_hostio:api_key": "HOSTIO_KEY",
    "sfp_hunter:api_key": "HUNTER_KEY",
    "sfp_hybrid_analysis:api_key": "HYBRID_ANALYSIS_KEY",
    "sfp_iknowwhatyoudownload:api_key": "IKNOWWHATYOUDOWNLOAD_KEY",
    "sfp_intelx:api_key": "INTELX_KEY",
    "sfp_ipapicom:api_key": "IPAPICOM_KEY",
    "sfp_ipinfo:api_key": "IPINFO_KEY",
    "sfp_ipqualityscore:api_key": "IPQUALITYSCORE_KEY",
    "sfp_ipregistry:api_key": "IPREGISTRY_KEY",
    "sfp_ipstack:api_key": "IPSTACK_KEY",
    "sfp_jsonwhoiscom:api_key": "JSONWHOISCOM_KEY",
    "sfp_leakix:api_key": "LEAKIX_KEY",
    "sfp_malwarepatrol:api_key": "MALWAREPATROL_KEY",
    "sfp_metadefender:api_key": "METADEFENDER_KEY",
    "sfp_nameapi:api_key": "NAMEAPI_KEY",
    "sfp_networksdb:api_key": "NETWORKSDB_KEY",
    "sfp_neutrinoapi:api_key": "NEUTRINOAPI_KEY",
    "sfp_numverify:api_key": "NUMVERIFY_KEY",
    "sfp_onioncity:api_key": "ONIONCITY_KEY",
    "sfp_onyphe:api_key": "ONYPHE_KEY",
    "sfp_opencorporates:api_key": "OPENCORPORATES_KEY",
    "sfp_pastebin:api_key": "PASTEBIN_KEY",
    "sfp_projectdiscovery:api_key": "PROJECTDISCOVERY_KEY",
    "sfp_pulsedive:api_key": "PULSEDIVE_KEY",
    "sfp_recondev:api_key": "RECONDEV_KEY",
    "sfp_riskiq:api_key_login": "RISKIQ_LOGIN",
    "sfp_riskiq:api_key_password": "RISKIQ_PASSWORD",
    "sfp_securitytrails:api_key": "SECURITYTRAILS_KEY",
    "sfp_seon:api_key": "SEON_KEY",
    "sfp_shodan:api_key": "SHODAN_KEY",
    "sfp_snov:api_key_client_id": "SNOV_CLIENT_ID",
    "sfp_snov:api_key_client_secret": "SNOV_CLIENT_SECRET",
    "sfp_sociallinks:api_key": "SOCIALLINKS_KEY",
    "sfp_spur:api_key": "SPUR_KEY",
    "sfp_spyonweb:api_key": "SPYONWEB_KEY",
    "sfp_spyse:api_key": "SPYSE_KEY",
    "sfp_stackoverflow:api_key": "STACKOVERFLOW_KEY",
    "sfp_textmagic:api_key": "TEXTMAGIC_KEY",
    "sfp_textmagic:api_key_username": "TEXTMAGIC_USERNAME",
    "sfp_trashpanda:api_key_password": "TRASHPANDA_PASSWORD",
    "sfp_trashpanda:api_key_username": "TRASHPANDA_USERNAME",
    "sfp_twilio:api_key_account_sid": "TWILIO_ACCOUNT_SID",
    "sfp_twilio:api_key_auth_token": "TWILIO_AUTH_TOKEN",
    "sfp_viewdns:api_key": "VIEWDNS_KEY",
    "sfp_virustotal:api_key": "VIRUSTOTAL_KEY",
    "sfp_whatcms:api_key": "WHATCMS_KEY",
    "sfp_whoisology:api_key": "WHOISOLOGY_KEY",
    "sfp_whoxy:api_key": "WHOXY_KEY",
    "sfp_wigle:api_key_encoded": "WIGLE_KEY",
    "sfp_xforce:xforce_api_key": "XFORCE_KEY",
    "sfp_xforce:xforce_api_key_password": "XFORCE_PASSWORD",
    "sfp_zetalytics:api_key": "ZETALYTICS_KEY",
}


def generate_theharvester_yaml(env_vars):
    """Generates the content for theHarvester's api-keys.yaml."""
    lines = ["apikeys:"]

    for service, fields in THEHARVESTER_MAPPING.items():
        lines.append(f"\n  {service}:")

        # Check if ALL required env vars for this service exist and are not empty
        all_present = all(
            env_vars.get(env_var, "").strip() != "" for env_var in fields.values()
        )

        for yaml_key, env_var in fields.items():
            if all_present:
                value = env_vars.get(env_var).strip()
                # Wrap in quotes if it contains spaces or special characters
                if " " in value or ":" in value:
                    value = f'"{value}"'
                lines.append(f"    {yaml_key}: {value}")
            else:
                # Leave blank per instructions if missing or incomplete
                lines.append(f"    {yaml_key}:")

    return "\n".join(lines) + "\n"


def generate_spiderfoot_cfg(env_vars):
    """Generates the content for SpiderFoot's SpiderFoot.cfg."""
    lines = []

    for sf_key, env_var in SPIDERFOOT_MAPPING.items():
        val = env_vars.get(env_var, "").strip()
        lines.append(f"{sf_key}={val}")

    return "\n".join(lines) + "\n"


def generate_searxng_yaml():
    """Generates the content for SearXNG's settings.yml with Tor proxy addresses."""
    secret_key = secrets.token_hex(32)
    return f"""use_default_settings: true

server:
  secret_key: "{secret_key}"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false

search:
  safe_search: 0
  formats:
    - html
    - json
engines:
  - name: google
    engine: google
    shortcut: go
    disabled: false
    timeout: 6.0

  - name: bing
    engine: bing
    shortcut: bi
    disabled: false
    timeout: 6.0

valkey:
  url: redis://valkey:6379/0
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate OSINT tool config files from .env"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print configs without writing to disk"
    )
    args = parser.parse_args()

    # Load environment variables
    if not os.path.exists(DOTENV_PATH):
        print(f"[!] Warning: {DOTENV_PATH} not found. Proceeding with empty values.")
        env_vars = {}
    else:
        env_vars = dotenv_values(DOTENV_PATH)

    # 1. Generate theHarvester YAML
    harvester_content = generate_theharvester_yaml(env_vars)

    # 2. Generate SpiderFoot CFG
    spiderfoot_content = generate_spiderfoot_cfg(env_vars)

    # 3. Generate SearXNG YAML
    searxng_content = generate_searxng_yaml()

    if args.dry_run:
        print("\n" + "=" * 60)
        print(f" [DRY RUN] theHarvester -> {THEHARVESTER_OUT_PATH}")
        print("=" * 60)
        print(harvester_content)

        print("\n" + "=" * 60)
        print(f" [DRY RUN] SpiderFoot -> {SPIDERFOOT_OUT_PATH}")
        print("=" * 60)
        print(spiderfoot_content)

        print("\n" + "=" * 60)
        print(f" [DRY RUN] SearXNG -> {SEARXNG_OUT_PATH}")
        print("=" * 60)
        print(searxng_content)

        print("=" * 60 + "\n")
        print("[*] Dry run complete. No files were written.")

    else:
        # Ensure directories exist
        os.makedirs(os.path.dirname(THEHARVESTER_OUT_PATH), exist_ok=True)
        os.makedirs(os.path.dirname(SPIDERFOOT_OUT_PATH), exist_ok=True)
        os.makedirs(os.path.dirname(SEARXNG_OUT_PATH), exist_ok=True)

        # Write files
        with open(THEHARVESTER_OUT_PATH, "w") as f:
            f.write(harvester_content)
        print(f"[+] Wrote theHarvester config to: {THEHARVESTER_OUT_PATH}")

        with open(SPIDERFOOT_OUT_PATH, "w") as f:
            f.write(spiderfoot_content)
        print(f"[+] Wrote SpiderFoot config to:   {SPIDERFOOT_OUT_PATH}")

        with open(SEARXNG_OUT_PATH, "w") as f:
            f.write(searxng_content)
        print(f"[+] Wrote SearXNG config to:      {SEARXNG_OUT_PATH}")


if __name__ == "__main__":
    main()
