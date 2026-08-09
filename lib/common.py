import logging
import re
import unicodedata
from typing import Optional


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configures standard logging for the application.
    Ensures a consistent log format across all pipeline stages.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def strip_accents(text: Optional[str]) -> str:
    """
    Removes diacritics and accents from a string.
    Useful for normalizing names and search queries.
    """
    if not text:
        return ""

    # Normalize to NFD (Canonical Decomposition)
    normalized = unicodedata.normalize("NFD", text)
    # Encode to ASCII, ignoring errors, then decode back to string
    ascii_text = normalized.encode("ascii", "ignore").decode("utf-8")

    return str(ascii_text)


def slugify_company(name: Optional[str]) -> str:
    """
    Converts a company name or domain into a filesystem-safe slug.
    Returns 'default' if the provided name is empty or strictly non-alphanumeric.
    """
    if not name:
        return "default"

    # Remove accents, convert to lowercase
    text = strip_accents(name).lower()

    # Replace any non-alphanumeric character with a hyphen
    text = re.sub(r"[^a-z0-9]+", "-", text)

    # Strip leading/trailing hyphens
    clean_slug = text.strip("-")

    return clean_slug if clean_slug else "default"


def generate_company_abbreviation(company_name: str) -> str:
    """
    Generates a company abbreviation by stripping stop words and taking initials.
    """
    if not company_name:
        return ""

    stop_words = {"de", "du", "des", "la", "le", "les", "et", "the", "and", "of", "for"}

    # Handle accented characters via the existing strip_accents() helper
    clean_name = strip_accents(company_name)

    # Splits on whitespace/hyphens
    words = re.split(r"[\s\-]+", clean_name)

    initials = []
    for word in words:
        if not word:
            continue
        if word.lower() not in stop_words:
            # Takes the first letter of each remaining word, uppercases
            initials.append(word[0].upper())

    # Joins with no separator
    return "".join(initials)
