#!/usr/bin/env python3
"""
config.py - Dynamic API Configuration Checker

Features:
1. Zero hardcoded service lists and zero tool categorizations.
2. Dynamically scans .env and activated environments (.venv/os.environ) for API credential variables.
3. Clusters multi-variable credentials automatically (e.g. CENSYS_ID + CENSYS_SECRET -> Censys).
4. Outputs a simple flat list of Available vs Missing APIs to stdout and JSON.
"""

import os
import re
import json
import sys
from pathlib import Path
from typing import Dict, List, Any

# Ensure python-dotenv is imported properly
try:
    from dotenv import load_dotenv, dotenv_values
except ImportError:
    print(
        "[!] Error: 'python-dotenv' is not installed. Run: pip install python-dotenv",
        file=sys.stderr,
    )
    sys.exit(1)


# Common API credential suffixes to detect variables dynamically
API_SUFFIXES = (
    "_KEY",
    "_TOKEN",
    "_SECRET",
    "_ID",
    "_ACCOUNT",
    "_PASSWORD",
    "_LOGIN",
    "_USERNAME",
    "_EMAIL",
    "_SID",
)


def load_environment(env_path: Path) -> Dict[str, str | None]:
    """
    Loads environment variables from `.env` and merges them with os.environ
    so variables exported in an activated .venv or shell session are also captured.
    """
    env_vars: Dict[str, str | None] = {}

    # 1. Load from .env file if it exists
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
        env_vars.update(dotenv_values(dotenv_path=env_path))
    else:
        print(
            f"[!] Warning: '{env_path.resolve()}' not found. Relying on active environment variables...",
            file=sys.stderr,
        )

    # 2. Merge in any existing OS/virtualenv environment variables that match API suffixes
    for key, val in os.environ.items():
        if any(key.endswith(suffix) for suffix in API_SUFFIXES):
            env_vars[key] = val

    return env_vars


def extract_base_service_name(var_name: str) -> str:
    """Strips credential suffixes to cluster multi-variable services together."""
    sorted_suffixes = sorted(API_SUFFIXES, key=len, reverse=True)
    for suffix in sorted_suffixes:
        if var_name.endswith(suffix):
            base = var_name[: -len(suffix)]
            base = re.sub(r"_CLIENT$", "", base, flags=re.IGNORECASE)
            return base if base else var_name
    return var_name


def build_dynamic_services(env_vars: Dict[str, str | None]) -> Dict[str, List[str]]:
    """Dynamically maps base service names to their required environment variables."""
    services: Dict[str, List[str]] = {}

    for var_name in env_vars.keys():
        if any(var_name.endswith(suffix) for suffix in API_SUFFIXES):
            base_name = extract_base_service_name(var_name)
            # Format service name cleanly (e.g., VIRUSTOTAL -> Virustotal, CENSYS -> Censys)
            service_title = "".join(word.capitalize() for word in base_name.split("_"))

            if service_title not in services:
                services[service_title] = []

            if var_name not in services[service_title]:
                services[service_title].append(var_name)

            services[service_title].sort()

    return services


def evaluate_api_status(services: Dict[str, List[str]]) -> Dict[str, Any]:
    """Checks if required variables are set and non-empty."""
    configured: Dict[str, List[str]] = {}
    missing: Dict[str, List[str]] = {}

    for service_name, req_vars in services.items():
        is_valid = all(
            os.getenv(var) is not None and len(os.getenv(var, "").strip()) > 0
            for var in req_vars
        )
        if is_valid:
            configured[service_name] = req_vars
        else:
            missing[service_name] = req_vars

    return {"configured": configured, "missing": missing}


def save_json(data: Dict[str, Any], output_path: Path) -> None:
    """Saves the status report to a JSON file."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[+] Saved status report to '{output_path}'\n")
    except OSError as e:
        print(f"[!] Error saving JSON: {e}", file=sys.stderr)
        sys.exit(1)


def print_summary(status: Dict[str, Any]) -> None:
    """Prints a simple summary of available and missing APIs."""
    configured = status["configured"]
    missing = status["missing"]

    print("=" * 60)
    print("                 API CONFIGURATION STATUS")
    print("=" * 60)

    print("\n[+] AVAILABLE / CONFIGURED APIs:")
    print("-" * 60)
    if configured:
        for service, vars_list in sorted(configured.items()):
            print(f"  * {service} ({', '.join(vars_list)})")
    else:
        print("  (None)")

    print("\n[-] MISSING APIs:")
    print("-" * 60)
    if missing:
        for service, vars_list in sorted(missing.items()):
            print(f"  * {service} ({', '.join(vars_list)})")
    else:
        print("  (None)")

    print("\n" + "=" * 60)
    print(f"SUMMARY: {len(configured)} Available | {len(missing)} Missing")
    print("=" * 60)


def main() -> None:
    env_path = Path(".env")

    env_vars = load_environment(env_path)

    services = build_dynamic_services(env_vars)

    status = evaluate_api_status(services)

    save_json(status, Path("config") / "api_status.json")

    print_summary(status)


if __name__ == "__main__":
    main()
