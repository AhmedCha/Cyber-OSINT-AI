import logging
import os
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


def load_env_file(env_path: Union[Path, str] = ".env") -> None:
    """Loads key-value pairs from a .env file directly into os.environ."""
    path = Path(env_path)
    if not path.exists() or not path.is_file():
        logger.debug(f"Environment file '{path}' not found. Skipping.")
        return

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if (value.startswith('"') and value.endswith('"')) or (
                        value.startswith("'") and value.endswith("'")
                    ):
                        value = value[1:-1]
                    os.environ[key] = value
        logger.debug(f"Successfully loaded environment variables from '{path}'.")
    except Exception as e:
        logger.error(f"Failed to load environment file '{path}': {e}")


def log_api_status_summary() -> None:
    """Logs the status of API/Secret keys and outputs the database location."""
    keywords = ("KEY", "SECRET", "API")

    # Find all env variables matching keywords
    matching_keys = [k for k in os.environ if any(kw in k.upper() for kw in keywords)]

    # Check which variables have a non-empty string value
    configured = [k for k in matching_keys if os.getenv(k, "").strip()]

    logger.info(
        f"API Key Status: {len(configured)}/{len(matching_keys)} configured "
        f"({', '.join(sorted(configured)) if configured else 'None'})"
    )

    # Log database location
    db_path = os.getenv("OSINT_DB_PATH")
    if db_path:
        logger.info(f"Database Path: {db_path}")
    else:
        logger.warning("Database Path (OSINT_DB_PATH): Not configured in environment")
