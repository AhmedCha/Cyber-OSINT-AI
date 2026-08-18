# lib/db.py
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Asymmetric mapping of raw tables to their corresponding reviewed tables
RAW_TO_REVIEWED = {
    "raw_domains": "reviewed_domains",
    "raw_emails": "reviewed_emails",
    "raw_employees": "reviewed_employees",
    "raw_dns_infra": "reviewed_infrastructure",
    "raw_documents": "reviewed_documents",
    "raw_breaches": "reviewed_breaches",
    "raw_darkweb": "reviewed_darkweb",
}


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
    """Initializes the database schema with raw, reviewed, and review tracking tables."""
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

    # Track manual and automated review decisions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS review_status (
            source_table TEXT,
            company_slug TEXT,
            record_key TEXT,
            status TEXT,  -- 'approved' | 'rejected'
            reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_table, company_slug, record_key)
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


def _validate_raw_table(raw_table: str) -> None:
    """Centralized whitelist check to prevent SQL injection and ensure valid tables."""
    if raw_table not in RAW_TO_REVIEWED:
        raise ValueError(f"Invalid or unmapped raw table: '{raw_table}'")


def approve_record(
    conn: sqlite3.Connection,
    raw_table: str,
    company_slug: str,
    record_key: str,
    edited_data: Optional[Any] = None,
) -> None:
    """
    Approves a raw record, copying it into the reviewed table and logging
    the 'approved' decision in review_status within a single transaction.
    """
    _validate_raw_table(raw_table)
    reviewed_table = RAW_TO_REVIEWED[raw_table]

    cursor = conn.cursor()

    if edited_data is not None:
        data_json = (
            json.dumps(edited_data)
            if isinstance(edited_data, (dict, list))
            else str(edited_data)
        )
    else:
        cursor.execute(
            f"SELECT data FROM {raw_table} WHERE company_slug = ? AND record_key = ?",
            (company_slug, record_key),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(
                f"Record with key '{record_key}' for company '{company_slug}' not found in table '{raw_table}'"
            )
        data_json = row["data"]

    try:
        cursor.execute("BEGIN IMMEDIATE TRANSACTION;")
        cursor.execute(
            f"""
            INSERT OR REPLACE INTO {reviewed_table} (company_slug, record_key, data, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (company_slug, record_key, data_json),
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO review_status (source_table, company_slug, record_key, status, reviewed_at)
            VALUES (?, ?, ?, 'approved', CURRENT_TIMESTAMP)
            """,
            (raw_table, company_slug, record_key),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reject_record(
    conn: sqlite3.Connection,
    raw_table: str,
    company_slug: str,
    record_key: str,
) -> None:
    """
    Marks a record as 'rejected' in review_status without modifying or
    deleting raw or reviewed tables.
    """
    _validate_raw_table(raw_table)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO review_status (source_table, company_slug, record_key, status, reviewed_at)
        VALUES (?, ?, ?, 'rejected', CURRENT_TIMESTAMP)
        """,
        (raw_table, company_slug, record_key),
    )
    conn.commit()


def list_records_with_status(
    conn: sqlite3.Connection,
    raw_table: str,
    company_slug: str,
) -> List[sqlite3.Row]:
    """
    Returns raw records for a company joined with their review status
    ('approved', 'rejected', or None for pending).
    """
    _validate_raw_table(raw_table)
    cursor = conn.cursor()
    query = f"""
        SELECT 
            r.*, 
            rs.status, 
            rs.reviewed_at
        FROM {raw_table} r
        LEFT JOIN review_status rs
          ON rs.source_table = ?
         AND rs.company_slug = r.company_slug
         AND rs.record_key = r.record_key
        WHERE r.company_slug = ?
    """
    cursor.execute(query, (raw_table, company_slug))
    return cursor.fetchall()


def get_company_summary(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Returns row counts for each raw_* and reviewed_* table per unique company_slug
    found across all raw tables.
    """
    cursor = conn.cursor()
    slugs = set()

    for raw_table in RAW_TO_REVIEWED.keys():
        cursor.execute(f"SELECT DISTINCT company_slug FROM {raw_table}")
        for row in cursor.fetchall():
            if row["company_slug"]:
                slugs.add(row["company_slug"])

    summary = []
    for slug in sorted(slugs):
        info: Dict[str, Any] = {"company_slug": slug}
        for raw_table, reviewed_table in RAW_TO_REVIEWED.items():
            cursor.execute(
                f"SELECT COUNT(*) FROM {raw_table} WHERE company_slug = ?",
                (slug,),
            )
            info[raw_table] = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT COUNT(*) FROM {reviewed_table} WHERE company_slug = ?",
                (slug,),
            )
            info[reviewed_table] = cursor.fetchone()[0]

        summary.append(info)

    return summary


def preview_merge(
    conn: sqlite3.Connection, source_slug: str, target_slug: str
) -> Dict[str, Dict[str, Any]]:
    """
    Previews a merge by counting rows to move and identifying collisions across all 14 data tables.
    """
    tables = list(RAW_TO_REVIEWED.keys()) + list(RAW_TO_REVIEWED.values())
    breakdown = {}
    cursor = conn.cursor()

    for table in tables:
        # Count total rows belonging to source_slug
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} WHERE company_slug = ?", (source_slug,)
        )
        would_move = cursor.fetchone()[0]

        # Find collisions (record_keys that already exist in target_slug)
        cursor.execute(
            f"""
            SELECT record_key FROM {table} 
            WHERE company_slug = ? 
            AND record_key IN (
                SELECT record_key FROM {table} WHERE company_slug = ?
            )
            """,
            (source_slug, target_slug),
        )
        collisions = [row["record_key"] for row in cursor.fetchall()]

        breakdown[table] = {"would_move": would_move, "collisions": collisions}
    return breakdown


def execute_merge(
    conn: sqlite3.Connection, source_slug: str, target_slug: str
) -> Dict[str, Dict[str, Any]]:
    """
    Executes a merge by re-keying rows from source_slug to target_slug,
    skipping collisions. Runs in a single transaction.
    """
    # Generate the breakdown to return to the caller
    breakdown = preview_merge(conn, source_slug, target_slug)
    tables = list(RAW_TO_REVIEWED.keys()) + list(RAW_TO_REVIEWED.values())

    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE TRANSACTION;")
        for table in tables:
            # Re-key all non-colliding rows
            cursor.execute(
                f"""
                UPDATE {table}
                SET company_slug = ?
                WHERE company_slug = ?
                AND record_key NOT IN (
                    SELECT record_key FROM {table} WHERE company_slug = ?
                )
                """,
                (target_slug, source_slug, target_slug),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return breakdown
