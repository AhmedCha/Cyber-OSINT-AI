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
    """Logs the configuration status of key OSINT API keys present in environment variables."""
    api_keys = [
        "SHODAN_API_KEY",
        "CENSYS_API_KEY",
        "VIRUSTOTAL_API_KEY",
        "SECURITYTRAILS_API_KEY",
        "HUNTER_API_KEY",
    ]
    status = {key: bool(os.getenv(key)) for key in api_keys}
    configured = [k for k, v in status.items() if v]
    logger.info(
        f"API Key Status: {len(configured)}/{len(api_keys)} configured "
        f"({', '.join(configured) if configured else 'None'})"
    )
