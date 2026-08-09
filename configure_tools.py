#!/usr/bin/env python3
import os
import sys
import sqlite3
import argparse
import secrets
import subprocess
from dotenv import dotenv_values

# ==============================================================================
# CONFIGURATION / OUTPUT PATHS
# ==============================================================================
THEHARVESTER_OUT_PATH = "./volumes/theharvester/api-keys.yaml"
SEARXNG_OUT_PATH = "./volumes/searxng/settings.yml"
DB_PATH = "./volumes/spiderfoot/spiderfoot.db"
DOTENV_PATH = ".env"
CONTAINER_NAME = "osint_spiderfoot"

# ==============================================================================
# HARDCODED SCHEMAS & MAPPINGS
# ==============================================================================

# Mapping for theHarvester: service_name -> { yaml_key: env_var_name }
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

# Full SpiderFoot mapping (sfp_module:field -> ENV_VAR)
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

# NOTE: unlike SPIDERFOOT_MAPPING above (per-module options, scope = module
# name), the SOCKS/Tor proxy is a GLOBAL SpiderFoot option, stored under a
# different scope entirely - it has no module name to key off. The scope
# value and the "_socksN..." option names below are SpiderFoot's commonly
# documented global-option convention, but were NOT confirmed against a
# live tbl_config dump before this was written. SAFEST way to confirm:
# set the SOCKS proxy once via the SpiderFoot web UI (Settings -> General),
# save it, then run:
#   sqlite3 ./volumes/spiderfoot/spiderfoot.db \
#     "SELECT * FROM tbl_config WHERE opt LIKE '%socks%';"
# and adjust GLOBAL_CONFIG_SCOPE / GLOBAL_OPTIONS below to match exactly
# what that query returns before relying on this seeding it automatically.
GLOBAL_CONFIG_SCOPE = "GLOBAL"

GLOBAL_OPTIONS = {
    "_socks1type": "SPIDERFOOT_SOCKS_TYPE",  # confirmed values: '4','5','HTTP','TOR' - use 'TOR' for .onion targets, not '5'
    "_socks2addr": "SPIDERFOOT_SOCKS_HOST",  # e.g. "tor" (compose service name)
    "_socks3port": "SPIDERFOOT_SOCKS_PORT",  # e.g. "9050" (Tor's internal port)
    "_socks4user": "SPIDERFOOT_SOCKS_USER",  # usually blank for this setup
    "_socks5pwd": "SPIDERFOOT_SOCKS_PASS",  # usually blank for this setup
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


def check_container_stopped(container_name: str) -> bool:
    """Returns True if the container is stopped or doesn't exist."""
    try:
        # Check if container is currently running
        result = subprocess.run(
            ["docker", "ps", "-q", "-f", f"name={container_name}"],
            capture_output=True,
            text=True,
            check=True,
        )
        # If output is not empty, the container is running
        return not bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        print(
            "[!] Warning: Could not verify Docker status. Ensure container is stopped!"
        )
        return False
    except FileNotFoundError:
        print("[!] Warning: 'docker' command not found. Ensure container is stopped!")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Generate OSINT tool config files and seed SpiderFoot DB from .env"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print configs and DB operations without writing to disk or DB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore running container warnings for SpiderFoot (Dangerous!)",
    )
    args = parser.parse_args()

    # Load environment variables
    env_vars = dotenv_values(DOTENV_PATH) if os.path.exists(DOTENV_PATH) else {}
    if not env_vars:
        print(
            f"[!] Warning: {DOTENV_PATH} not found or empty. Proceeding with empty values."
        )

    # 1. Generate theHarvester & SearXNG File Configs
    harvester_content = generate_theharvester_yaml(env_vars)
    searxng_content = generate_searxng_yaml()

    if args.dry_run:
        print("\n" + "=" * 60)
        print(f" [DRY RUN] theHarvester -> {THEHARVESTER_OUT_PATH}")
        print("=" * 60)
        print(harvester_content)

        print("\n" + "=" * 60)
        print(f" [DRY RUN] SearXNG -> {SEARXNG_OUT_PATH}")
        print("=" * 60)
        print(searxng_content)
    else:
        # Ensure directories exist
        os.makedirs(os.path.dirname(THEHARVESTER_OUT_PATH), exist_ok=True)
        os.makedirs(os.path.dirname(SEARXNG_OUT_PATH), exist_ok=True)

        # Write files
        with open(THEHARVESTER_OUT_PATH, "w") as f:
            f.write(harvester_content)
        print(f"[+] Wrote theHarvester config to: {THEHARVESTER_OUT_PATH}")

        with open(SEARXNG_OUT_PATH, "w") as f:
            f.write(searxng_content)
        print(f"[+] Wrote SearXNG config to:      {SEARXNG_OUT_PATH}")

    # 2. Seed SpiderFoot DB
    # Safety Check: SQLite writes can corrupt/fail if the container has a write lock
    if not args.dry_run and not args.force:
        if not check_container_stopped(CONTAINER_NAME):
            print(f"[ERROR] The container '{CONTAINER_NAME}' appears to be running.")
            print(
                "Writing to spiderfoot.db while the service is active may cause SQLite lock errors or corruption."
            )
            print("Please run: docker compose stop spiderfoot")
            print("Then try again. (Or bypass this with --force)")
            sys.exit(1)

    if not os.path.exists(DB_PATH) and not args.dry_run:
        print(f"[ERROR] Database file {DB_PATH} not found.")
        print(
            "You must start the spiderfoot container at least once to generate the database schema."
        )
        sys.exit(1)

    # Prepare data based on the mapping
    operations = []
    for sf_key, env_var in SPIDERFOOT_MAPPING.items():
        val = (env_vars.get(env_var) or "").strip()
        if val:
            if ":" not in sf_key:
                print(f"[!] Skipping malformed key in mapping: {sf_key}")
                continue

            scope, opt = sf_key.split(":", 1)
            operations.append((scope, opt, val))

    # Global options (SOCKS/Tor proxy)
    for opt, env_var in GLOBAL_OPTIONS.items():
        val = (env_vars.get(env_var) or "").strip()
        if val:
            operations.append((GLOBAL_CONFIG_SCOPE, opt, val))

    if not operations:
        print("[*] No API keys found in .env to inject into SpiderFoot DB. Exiting.")
        sys.exit(0)

    print(f"[*] Found {len(operations)} valid API keys in .env for SpiderFoot.")

    if args.dry_run:
        print("\n=== DRY RUN MODE: No DB changes will be made ===")
        for scope, opt, val in operations:
            # Mask the secret for display
            masked_val = val[:4] + "*" * (len(val) - 4) if len(val) > 4 else "***"
            print(
                f"Would INSERT OR REPLACE -> Scope: {scope:<20} | Opt: {opt:<30} | Val: {masked_val}"
            )
        print("=============================================\n")
        print("[*] Dry run complete. No files or databases were written.")
        sys.exit(0)

    # Execute DB insertions
    print(f"[*] Opening {DB_PATH} for writes...")
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        insert_query = """
        INSERT OR REPLACE INTO tbl_config (scope, opt, val) 
        VALUES (?, ?, ?);
        """

        cursor.executemany(insert_query, operations)
        conn.commit()

        print(
            f"[+] Successfully injected {cursor.rowcount} configuration records into tbl_config."
        )

    except sqlite3.Error as e:
        print(f"[FAIL] SQLite database error: {e}")
        sys.exit(1)
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
