#!/usr/bin/env python3
"""
Legacy seed script — summit catalogs are stored encrypted via store_encrypted_catalogs.py.

This script only ensures the schema exists. It no longer inserts ${ENV:...} placeholders.
"""
import json

from pg_connect import connect_pg

INIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS trino_catalogs (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) UNIQUE NOT NULL,
  properties JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS trino_kafka_config (
  id SERIAL PRIMARY KEY,
  config_text TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def main() -> None:
    conn = connect_pg()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(INIT_SCHEMA)
        print("  Catalog schema ready (use store_encrypted_catalogs.py for summit creds)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
