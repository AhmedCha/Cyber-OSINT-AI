import http.client
import json
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def run_apify_actor(actor_id: str, run_input: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Runs a specified Apify actor synchronously via REST API and returns the dataset items.
    """
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        logger.warning("APIFY_API_TOKEN not set in environment.")
        return []

    logger.info(f"Running Apify actor '{actor_id}' via HTTP POST...")

    try:
        # Increase timeout in case the actor takes a while to run synchronously
        conn = http.client.HTTPSConnection("api.apify.com", timeout=300)

        payload = json.dumps(run_input)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

        endpoint = f"/v2/actors/{actor_id}/run-sync-get-dataset-items"

        conn.request("POST", endpoint, payload, headers)
        res = conn.getresponse()
        data = res.read()

        if res.status not in (200, 201):
            logger.error(
                f"Apify HTTP request failed with status {res.status}: {data.decode('utf-8')}"
            )
            return []

        items = json.loads(data.decode("utf-8"))

        # The run-sync-get-dataset-items endpoint returns the array of dataset items directly
        if isinstance(items, list):
            logger.info(f"Apify actor '{actor_id}' returned {len(items)} items.")
            return items
        else:
            logger.warning(f"Apify actor returned an unexpected format: {type(items)}")
            return []

    except Exception as e:
        logger.error(f"Error calling Apify actor '{actor_id}' via HTTP: {e}")
        return []
    finally:
        try:
            conn.close()
        except NameError:
            pass
