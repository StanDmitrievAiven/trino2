#!/usr/bin/env python3
"""
Fetch Trino catalog definitions from PostgreSQL and write .properties files.
Run at container startup to restore catalogs after a restart.
Supports encrypted properties when TRINO_CATALOG_ENCRYPTION_KEY is set.
"""
import os
import sys
import json
import base64

try:
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 required. Install with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

from pg_connect import connect_pg, get_db_url

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    Fernet = None

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


def _get_fernet(encryption_key: str) -> "Fernet":
    """Create Fernet instance from key or passphrase."""
    if not Fernet:
        raise RuntimeError("cryptography package required for encryption. Install with: pip install cryptography")
    key = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
    # Try as raw Fernet key first (44-char base64url)
    try:
        return Fernet(key)
    except Exception:
        pass
    # Otherwise derive key from passphrase
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"trino-catalog-store",
        iterations=480000,
    )
    derived = base64.urlsafe_b64encode(kdf.derive(key))
    return Fernet(derived)


def _decrypt_properties(props: dict, fernet: "Fernet") -> dict:
    """Decrypt properties if stored in encrypted format."""
    if not isinstance(props, dict) or not props.get("_encrypted") or "data" not in props:
        return props
    try:
        decrypted = fernet.decrypt(props["data"].encode()).decode()
        return json.loads(decrypted)
    except Exception as e:
        print(f"WARNING: Failed to decrypt catalog properties: {e}", file=sys.stderr)
        raise


def main():
    try:
        get_db_url()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    catalog_dir = "/etc/trino/catalog"
    os.makedirs(catalog_dir, exist_ok=True)

    conn = connect_pg()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(INIT_SCHEMA)
        print("Schema initialized.")

        encryption_key = os.environ.get("TRINO_CATALOG_ENCRYPTION_KEY")
        fernet = _get_fernet(encryption_key) if encryption_key else None

        # Write Kafka client config if present (for SASL_SSL etc.)
        kafka_config_path = "/etc/trino/kafka-client.properties"
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT config_text FROM trino_kafka_config LIMIT 1")
            kafka_row = cur.fetchone()
        if kafka_row:
            with open(kafka_config_path, "w") as f:
                f.write(kafka_row["config_text"])
            print("  Wrote kafka-client.properties")

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT name, properties FROM trino_catalogs")
            rows = cur.fetchall()

        for row in rows:
            name = row["name"]
            props = row["properties"]
            if isinstance(props, str):
                props = json.loads(props)
            if fernet and isinstance(props, dict):
                props = _decrypt_properties(props, fernet)
            # Kafka: filter invalid props, use kafka.config.resources
            if props.get("connector.name") == "kafka":
                invalid = {"kafka.sasl.jaas.config", "kafka.sasl.mechanism", "kafka.security.protocol",
                          "kafka.ssl.endpoint.identification.algorithm"}
                props = {k: v for k, v in props.items() if k not in invalid}
                if kafka_row:
                    props["kafka.config.resources"] = kafka_config_path
            path = os.path.join(catalog_dir, f"{name}.properties")
            with open(path, "w") as f:
                for k, v in props.items():
                    f.write(f"{k}={v}\n")
            print(f"  Wrote catalog: {name}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
