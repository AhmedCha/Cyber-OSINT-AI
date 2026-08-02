#!/usr/bin/env python3
import os
import sys
import sqlite3
import argparse
import subprocess
from dotenv import dotenv_values

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DB_PATH = "./volumes/spiderfoot/spiderfoot.db"
DOTENV_PATH = ".env"
CONTAINER_NAME = "osint_spiderfoot"

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
        description="Seed SpiderFoot SQLite config from .env"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print operations without touching the DB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore running container warnings (Dangerous!)",
    )
    args = parser.parse_args()

    # Safety Check: SQLite writes can corrupt/fail if the container has a write lock
    if not args.dry_run and not args.force:
        if not check_container_stopped(CONTAINER_NAME):
            print(f"[ERROR] The container '{CONTAINER_NAME}' appears to be running.")
            print(
                "Writing to spiderfoot.db while the service is active may cause SQLite lock errors or corruption."
            )
            print(f"Please run: docker compose stop spiderfoot")
            print("Then try again. (Or bypass this with --force)")
            sys.exit(1)

    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database file {DB_PATH} not found.")
        print(
            "You must start the spiderfoot container at least once to generate the database schema."
        )
        sys.exit(1)

    # Load environment variables
    env_vars = dotenv_values(DOTENV_PATH) if os.path.exists(DOTENV_PATH) else {}
    if not env_vars:
        print(f"[!] Warning: {DOTENV_PATH} not found or empty.")

    # Prepare data based on the mapping
    operations = []
    for sf_key, env_var in SPIDERFOOT_MAPPING.items():
        val = env_vars.get(env_var, "").strip()
        if val:
            # sf_key format: "sfp_shodan:api_key"
            if ":" not in sf_key:
                print(f"[!] Skipping malformed key in mapping: {sf_key}")
                continue

            scope, opt = sf_key.split(":", 1)
            operations.append((scope, opt, val))

    if not operations:
        print("[*] No API keys found in .env to inject. Exiting.")
        sys.exit(0)

    print(f"[*] Found {len(operations)} valid API keys in .env for SpiderFoot.")

    if args.dry_run:
        print("\n=== DRY RUN MODE: No changes will be made ===")
        for scope, opt, val in operations:
            # Mask the secret for display
            masked_val = val[:4] + "*" * (len(val) - 4) if len(val) > 4 else "***"
            print(
                f"Would INSERT OR REPLACE -> Scope: {scope:<20} | Opt: {opt:<30} | Val: {masked_val}"
            )
        print("=============================================\n")
        sys.exit(0)

    # Execute DB insertions
    print(f"[*] Opening {DB_PATH} for writes...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # We use INSERT OR REPLACE. Since (scope, opt) is the PRIMARY KEY,
        # this will update existing default rows or insert new ones seamlessly.
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
        if "conn" in locals():
            conn.close()


if __name__ == "__main__":
    main()
