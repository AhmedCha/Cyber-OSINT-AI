import json
import logging
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger(__name__)


def load_json(file_path: Union[Path, str]) -> Any:
    """
    Reads and parses a JSON file safely.
    Returns an empty dictionary if the file is missing or invalid.
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"JSON file not found: {path}")
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from {path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error reading JSON file {path}: {e}")
        return {}


def save_json(
    file_path: Union[Path, str],
    data: Any,
    indent: int = 2,
    ensure_ascii: bool = False,
    **kwargs: Any,
) -> bool:
    """
    Serializes Python objects to a JSON file.
    Automatically creates necessary parent directories.

    Returns:
        bool: True if save was successful, False otherwise.
    """
    path = Path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii, **kwargs)
        return True
    except Exception as e:
        logger.error(f"Failed to save JSON to {path}: {e}")
        return False
