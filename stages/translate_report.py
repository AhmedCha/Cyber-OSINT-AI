#!/usr/bin/env python3
"""
OSINT Stage: Translate Report (optional)

Takes the already-generated English-language report docx (produced by
generate_report.py) and translates it into a target language via DeepL's
Document Translation API, saving the result alongside the original as a
separate file. The original report is never modified or overwritten.

This stage only runs when run_pipeline.sh is invoked with --language
<CODE> (e.g. --language FR). It is entirely optional: if DEEPL_API_KEY
isn't configured, this stage logs a clear error and exits successfully
without touching the pipeline's other output, rather than failing the run.

DeepL Document Translation API flow (v2), per developers.deepl.com:
  1. POST  /v2/document          (multipart: file + target_lang)
           -> {document_id, document_key}
  2. POST  /v2/document/{document_id}   (form: document_key)
           -> {status: queued|translating|done|error, ...}
           poll this until status is "done" or "error"
  3. POST  /v2/document/{document_id}/result   (form: document_key)
           -> raw translated file bytes

DeepL API Free keys are identified by the literal ":fx" suffix (confirmed
against DeepL's own docs at developers.deepl.com/docs/getting-started/auth)
and must use https://api-free.deepl.com instead of https://api.deepl.com.

Usage:
    python -m stages.translate_report --company "Resys Consultants" --language FR
"""

import argparse
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import requests

from lib.common import setup_logging, slugify_company

logger = logging.getLogger(__name__)

# =====================================================================
# CONFIG
# =====================================================================

DEEPL_API_KEY_ENV = "DEEPL_API_KEY"
# Optional explicit override ("free" / "pro") if auto-detection from the
# key format ever needs to be bypassed. Auto-detection is used by default.
DEEPL_API_TIER_ENV = "DEEPL_API_TIER"

DEEPL_FREE_BASE_URL = "https://api-free.deepl.com"
DEEPL_PRO_BASE_URL = "https://api.deepl.com"

POLL_INTERVAL_SECONDS = 5
MAX_POLL_SECONDS = 15 * 60  # give up after 15 minutes of polling

UPLOAD_TIMEOUT_SECONDS = 120
POLL_REQUEST_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 120

# Matches report_{slug}_{YYYY-MM-DD}.docx exactly - i.e. the original
# source-language report, NOT an already-translated report_..._{LANG}.docx
REPORT_FILENAME_RE_TEMPLATE = r"^report_{slug}_(\d{{4}}-\d{{2}}-\d{{2}})\.docx$"


# =====================================================================
# DEEPL API HELPERS
# =====================================================================


def resolve_deepl_base_url(api_key: str) -> str:
    """Determine the correct DeepL API base URL for this key.

    Per DeepL's own docs (developers.deepl.com/docs/getting-started/auth):
    "DeepL API Free authentication keys can be identified easily by the
    suffix ':fx'" - free-tier keys must use api-free.deepl.com, all other
    keys use api.deepl.com. An explicit DEEPL_API_TIER env var can override
    this if ever needed.
    """
    tier_override = os.environ.get(DEEPL_API_TIER_ENV, "").strip().lower()
    if tier_override in ("free", "api-free"):
        return DEEPL_FREE_BASE_URL
    if tier_override in ("pro", "paid", "api"):
        return DEEPL_PRO_BASE_URL

    return (
        DEEPL_FREE_BASE_URL if api_key.strip().endswith(":fx") else DEEPL_PRO_BASE_URL
    )


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"DeepL-Auth-Key {api_key}"}


def upload_document(
    base_url: str, api_key: str, file_path: Path, target_lang: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Uploads the document for translation.

    Returns (document_id, document_key, error). Exactly one of
    (document_id and document_key) or error is set.
    """
    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                f"{base_url}/v2/document",
                headers=_auth_headers(api_key),
                data={"target_lang": target_lang},
                files={
                    "file": (
                        file_path.name,
                        f,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
    except requests.RequestException as e:
        return None, None, f"Upload request failed: {e}"

    if response.status_code != 200:
        return (
            None,
            None,
            f"Upload failed (HTTP {response.status_code}): {response.text}",
        )

    try:
        payload = response.json()
        document_id = payload["document_id"]
        document_key = payload["document_key"]
    except (ValueError, KeyError) as e:
        return None, None, f"Upload response missing expected fields: {e}"

    return document_id, document_key, None


def poll_until_done(
    base_url: str, api_key: str, document_id: str, document_key: str
) -> Tuple[bool, Optional[str]]:
    """Polls document status until it's 'done' or 'error', or until
    MAX_POLL_SECONDS elapses. Returns (success, error)."""
    deadline = time.monotonic() + MAX_POLL_SECONDS

    while True:
        try:
            response = requests.post(
                f"{base_url}/v2/document/{document_id}",
                headers=_auth_headers(api_key),
                data={"document_key": document_key},
                timeout=POLL_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            return False, f"Status check request failed: {e}"

        if response.status_code != 200:
            return (
                False,
                f"Status check failed (HTTP {response.status_code}): {response.text}",
            )

        try:
            payload = response.json()
            status = payload["status"]
        except (ValueError, KeyError) as e:
            return False, f"Status response missing expected fields: {e}"

        if status == "done":
            return True, None
        if status == "error":
            error_message = payload.get("error_message", "no further detail provided")
            return False, f"DeepL reported a translation error: {error_message}"

        seconds_remaining = payload.get("seconds_remaining")
        logger.info(
            f"[translate_report] Translation status: {status}"
            + (
                f" (~{seconds_remaining}s remaining)"
                if seconds_remaining is not None
                else ""
            )
        )

        if time.monotonic() >= deadline:
            return (
                False,
                f"Timed out after {MAX_POLL_SECONDS}s waiting for translation to finish.",
            )

        time.sleep(POLL_INTERVAL_SECONDS)


def download_result(
    base_url: str, api_key: str, document_id: str, document_key: str
) -> Tuple[Optional[bytes], Optional[str]]:
    """Downloads the translated document. Returns (content_bytes, error)."""
    try:
        response = requests.post(
            f"{base_url}/v2/document/{document_id}/result",
            headers=_auth_headers(api_key),
            data={"document_key": document_key},
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return None, f"Download request failed: {e}"

    if response.status_code != 200:
        return None, f"Download failed (HTTP {response.status_code}): {response.text}"

    return response.content, None


# =====================================================================
# REPORT FILE DISCOVERY
# =====================================================================


def find_source_report(company_dir: Path, company_slug: str) -> Optional[Path]:
    """Finds the most recent original (untranslated) report docx for this
    company, i.e. report_{slug}_{date}.docx - never a report that already
    has a language suffix."""
    pattern = re.compile(
        REPORT_FILENAME_RE_TEMPLATE.format(slug=re.escape(company_slug))
    )

    candidates = []
    if company_dir.exists():
        for path in company_dir.iterdir():
            match = pattern.match(path.name)
            if match:
                candidates.append((match.group(1), path))

    if not candidates:
        return None

    # date strings are YYYY-MM-DD, so lexicographic max == most recent
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


# =====================================================================
# MAIN
# =====================================================================


def main() -> None:
    setup_logging()
    args = parse_arguments()

    api_key = os.environ.get(DEEPL_API_KEY_ENV, "").strip()
    if not api_key:
        logger.error(
            f"[translate_report] {DEEPL_API_KEY_ENV} is not set - skipping report "
            f"translation. Set {DEEPL_API_KEY_ENV} in your .env to enable this stage."
        )
        return

    company_slug = slugify_company(args.company)
    company_dir = Path("output") / company_slug

    source_report = find_source_report(company_dir, company_slug)
    if source_report is None:
        logger.error(
            f"[translate_report] No source report found in {company_dir} matching "
            f"report_{company_slug}_<date>.docx. Run generate_report.py first."
        )
        raise SystemExit(1)

    target_lang = args.language.strip().upper()
    base_url = resolve_deepl_base_url(api_key)
    logger.info(
        f"[translate_report] Translating {source_report.name} to {target_lang} via {base_url}..."
    )

    document_id, document_key, error = upload_document(
        base_url, api_key, source_report, target_lang
    )
    if error:
        logger.error(f"[translate_report] {error}")
        raise SystemExit(1)

    success, error = poll_until_done(base_url, api_key, document_id, document_key)
    if not success:
        logger.error(f"[translate_report] {error}")
        raise SystemExit(1)

    content, error = download_result(base_url, api_key, document_id, document_key)
    if error:
        logger.error(f"[translate_report] {error}")
        raise SystemExit(1)

    # report_{slug}_{date}.docx -> report_{slug}_{date}_{LANG}.docx
    output_path = source_report.with_name(f"{source_report.stem}_{target_lang}.docx")
    output_path.write_bytes(content)

    logger.info(
        f"[translate_report] Translated report written to: {output_path.resolve()}"
    )
    print("\n" + "=" * 60)
    print("           REPORT TRANSLATION SUMMARY")
    print("=" * 60)
    print(f"Company        : {args.company}")
    print(f"Target language: {target_lang}")
    print(f"Output file    : {output_path.resolve()}")
    print("=" * 60 + "\n")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OSINT Stage: Translate the generated Word report via DeepL"
    )
    parser.add_argument("--company", required=True, help="Target company name")
    parser.add_argument(
        "--language",
        required=True,
        help="Target language code for translation (DeepL target_lang, e.g. FR, DE, AR)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
