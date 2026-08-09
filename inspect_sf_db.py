#!/usr/bin/env python3
import sqlite3
import os

# Target the mounted SpiderFoot database
DB_PATH = "./volumes/spiderfoot/spiderfoot.db"


def inspect_db():
    if not os.path.exists(DB_PATH):
        print(f"[FAIL] Database not found at {DB_PATH}.")
        print(
            "Ensure the spiderfoot container has run at least once to initialize the DB."
        )
        return

    print(f"[*] Inspecting: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Get all tables
    print("\n=== TABLES IN DATABASE ===")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    for t in tables:
        print(f" - {t[0]}")

    # 2. Get the schema for the config table (usually 'tbl_config')
    target_table = "tbl_config"
    print(f"\n=== SCHEMA FOR '{target_table}' ===")
    cursor.execute(
        f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{target_table}';"
    )
    schema = cursor.fetchone()
    if schema:
        print(schema[0])
    else:
        print(f"[!] Table '{target_table}' not found. The schema might have changed.")

    # 3. Get a sample of rows to confirm the key format (specifically looking for API keys)
    if schema:
        print(f"\n=== SAMPLE ROWS (Checking for API Key formats) ===")
        try:
            # We'll pull a few rows where the option name implies an API key
            cursor.execute(
                f"SELECT * FROM {target_table} WHERE opt LIKE '%api%' LIMIT 10;"
            )
            rows = cursor.fetchall()

            if not rows:
                print(
                    "No rows containing 'api' found. Fetching first 5 rows of the table instead..."
                )
                cursor.execute(f"SELECT * FROM {target_table} LIMIT 5;")
                rows = cursor.fetchall()

            for r in rows:
                print(r)
        except sqlite3.Error as e:
            print(f"[FAIL] Error querying table: {e}")

    conn.close()


if __name__ == "__main__":
    inspect_db()
