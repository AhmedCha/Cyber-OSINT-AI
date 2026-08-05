import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def query_searxng(base_url: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Queries a SearXNG instance and returns a list of result dictionaries."""
    params = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{base_url.rstrip('/')}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OSINT-Pipeline/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status == 200:
                payload = json.loads(response.read().decode("utf-8"))
                results = payload.get("results", [])
                return results[:limit]
    except Exception as e:
        logger.error(f"Failed to query SearXNG at {base_url}: {e}")

    return []
