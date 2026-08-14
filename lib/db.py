# lib/db.py
import os
import json
import sqlite3
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_db_connection() -> sqlite3.Connection:
    """Returns a connection to the shared OSINT database."""
    db_path = os.environ.get("OSINT_SHARED_DB_PATH", "output/osint_shared.db")

    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # Add timeout=30.0 to wait up to 30 seconds if the DB is locked by another process
    conn = sqlite3.connect(db_path, timeout=30.0)

    # Enable WAL mode for concurrent multi-project reading and writing
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Initializes the database schema with raw and reviewed table tiers."""
    tables = [
        # Raw Data Tier (Written by the 12 discovery stages)
        "raw_domains",
        "raw_emails",
        "raw_employees",
        "raw_dns_infra",
        "raw_documents",
        "raw_breaches",
        "raw_darkweb",
        # Reviewed Data Tier (Written ONLY by llm_filter.py)
        "reviewed_domains",
        "reviewed_emails",
        "reviewed_employees",
        "reviewed_infrastructure",
        "reviewed_documents",
        "reviewed_breaches",
        "reviewed_darkweb",
    ]

    cursor = conn.cursor()
    for table in tables:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                company_slug TEXT,
                record_key TEXT,
                data JSON,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (company_slug, record_key)
            )
        """)
    conn.commit()


def upsert_records(
    conn: sqlite3.Connection,
    table: str,
    company_slug: str,
    records: List[Dict[str, Any]],
    key_field: str,
) -> None:
    """
    Safely upserts records. Fails gracefully to prevent pipeline crashes.
    """
    if not records:
        return

    # One-time sanity check for the batch: Verify key_field is present in the first record
    if key_field not in records[0]:
        logger.warning(
            f"Sanity Check Warning: Expected key_field '{key_field}' is missing from the first record targeting table '{table}'."
        )

    try:
        cursor = conn.cursor()
        for record in records:
            if key_field not in record:
                available_keys = list(record.keys())
                logger.warning(
                    f"Skipping record for table '{table}': Missing expected key_field '{key_field}'. Available keys: {available_keys}"
                )
                continue

            # Extract the unique key for this record
            record_key = str(record[key_field])

            cursor.execute(
                f"""
                INSERT OR REPLACE INTO {table} (company_slug, record_key, data, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (company_slug, record_key, json.dumps(record)),
            )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to upsert records into {table}: {e}")
