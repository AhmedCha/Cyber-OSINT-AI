import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Standard modern browser User-Agent to prevent CAPTCHAs and engine blocking
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def generate_search_query_variants(base_query: str) -> List[str]:
    """Generates space-separated and hyphenated search query variants."""
    space_version = re.sub(r"[-_]+", " ", base_query).strip()
    hyphen_version = re.sub(r"\s+", "-", base_query).strip()

    variants: List[str] = []
    for variant in [space_version, hyphen_version]:
        if variant and variant not in variants:
            variants.append(variant)
    return variants


def query_searxng(
    base_url: str, query: str, limit: int = 10, engines: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Queries a SearXNG instance and returns a list of result dictionaries."""
    query_params = {"q": query, "format": "json"}
    if engines:
        query_params["engines"] = engines

    params = urllib.parse.urlencode(query_params)
    url = f"{base_url.rstrip('/')}/search?{params}"

    logger.debug(f"[SearXNG] Prepared URL: {url}")
    logger.debug(f"[SearXNG] Engines: {engines if engines else 'Default (All)'}")

    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        logger.debug(f"[SearXNG] Sending request to {base_url}...")

        with urllib.request.urlopen(req, timeout=15) as response:
            logger.debug(f"[SearXNG] Received HTTP status: {response.status}")

            if response.status == 200:
                payload = json.loads(response.read().decode("utf-8"))

                # Log unresponsive engines if any
                unresponsive = payload.get("unresponsive_engines", [])
                if unresponsive:
                    logger.debug(
                        f"[SearXNG] Unresponsive engines/CAPTCHAs: {unresponsive}"
                    )

                results = payload.get("results", [])
                logger.debug(
                    f"[SearXNG] Successfully parsed JSON. Found {len(results)} total raw results."
                )

                limited_results = results[:limit]
                if len(results) > limit:
                    logger.debug(
                        f"[SearXNG] Truncating results to requested limit of {limit}."
                    )

                return limited_results
            else:
                logger.warning(
                    f"[SearXNG] Unexpected HTTP status {response.status} from {base_url}"
                )

    except Exception as e:
        logger.error(f"[SearXNG] Failed to query SearXNG at {base_url}: {e}")

    return []
