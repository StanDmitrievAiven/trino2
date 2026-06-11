#!/usr/bin/env python3
"""
Watch trino_catalogs in PostgreSQL and run CREATE CATALOG for new catalogs.
Requires catalog.management=dynamic in Trino config.
Runs in background; no Trino restart needed when catalogs are added to PG.
Uses trino-python-client for proper auth handling.
"""
import os
import sys
import json
import base64
import time
from urllib.parse import urlparse, urlunparse

import requests

try:
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 required", file=sys.stderr)
    sys.exit(1)

from pg_connect import connect_pg, get_db_url

try:
    from trino.dbapi import connect as trino_connect
    from trino.auth import BasicAuthentication
except ImportError:
    trino_connect = None
    BasicAuthentication = None

try:
    from catalog_crypto import decrypt_properties, get_fernet
except ImportError:
    decrypt_properties = None

    def get_fernet(_key: str):
        return None

# Parse TRINO_INTERNAL_URL (e.g. http://127.0.0.1:8080)
TRINO_URL = os.environ.get("TRINO_INTERNAL_URL", "http://127.0.0.1:8080")
if "://" in TRINO_URL:
    _scheme, _rest = TRINO_URL.split("://", 1)
    _host_port = _rest.split("/")[0]
    TRINO_HOST = _host_port.split(":")[0] if ":" in _host_port else _host_port
    TRINO_PORT = int(_host_port.split(":")[1]) if ":" in _host_port else 8080
else:
    TRINO_HOST = "127.0.0.1"
    TRINO_PORT = 8080

TRINO_USER = os.environ.get("TRINO_ADMIN_USER", "trino_admin")
TRINO_PASSWORD = (
    os.environ.get("TRINO_ADMIN_PASSWORD")
    or os.environ.get("TRINO_PASSWORD")
    or ""
)
if not TRINO_PASSWORD and os.environ.get("TRINO_ADMIN_PASSWORD_FILE"):
    try:
        with open(os.environ["TRINO_ADMIN_PASSWORD_FILE"]) as f:
            TRINO_PASSWORD = f.read().strip()
    except Exception:
        pass
POLL_INTERVAL = int(os.environ.get("CATALOG_WATCHER_INTERVAL", "60"))


def _coerce_loopback_https_to_http(url: str) -> str:
    """
    Trino may return nextUri/infoUri as https://127.0.0.1 when
    http-server.process-forwarded=true (or similar), while inside the container
    only plain HTTP is listening — following https causes SSL record layer failure.
    """
    try:
        p = urlparse(url)
        if p.scheme != "https":
            return url
        host = (p.hostname or "").lower().strip("[]")
        if host in ("127.0.0.1", "localhost", "::1"):
            return urlunparse(
                ("http", p.netloc, p.path, p.params, p.query, p.fragment)
            )
    except Exception:
        pass
    return url


class _LoopbackTrinoHttpSession(requests.Session):
    """Forces http:// for loopback URLs on all Trino client requests (POST/GET/DELETE)."""

    def request(self, method, url, *args, **kwargs):
        if isinstance(url, str):
            url = _coerce_loopback_https_to_http(url)
        return super().request(method, url, *args, **kwargs)


def _decrypt_properties(props, fernet):
    if decrypt_properties and isinstance(props, dict) and props.get("_encrypted"):
        return decrypt_properties(props)
    if not isinstance(props, dict) or not props.get("_encrypted") or "data" not in props:
        return props
    if fernet is None:
        return props
    try:
        decrypted = fernet.decrypt(props["data"].encode()).decode()
        return json.loads(decrypted)
    except Exception:
        return props


def _trino_connection():
    """Create Trino connection (uses official client for proper auth)."""
    if not trino_connect:
        raise RuntimeError("trino package required. Install with: pip install trino")
    # With http-server.process-forwarded=true, Trino requires a "secure" client view for
    # PASSWORD auth — without X-Forwarded-Proto: https you get 401 "Password not allowed
    # for insecure authentication". That also makes nextUri use https://127.0.0.1 while
    # only plain HTTP listens locally; _LoopbackTrinoHttpSession rewrites those to http://.
    session = _LoopbackTrinoHttpSession()
    kwargs = dict(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user=TRINO_USER,
        catalog="system",
        schema="runtime",
        http_scheme="http",
        http_session=session,
        http_headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "127.0.0.1",
        },
    )
    if TRINO_PASSWORD:
        kwargs["auth"] = BasicAuthentication(TRINO_USER, TRINO_PASSWORD)
    return trino_connect(**kwargs)


def trino_query(query: str) -> list:
    """Execute query on Trino via trino-python-client."""
    conn = _trino_connection()
    try:
        cur = conn.cursor()
        cur.execute(query)
        return cur.fetchall()
    finally:
        conn.close()


def get_trino_catalogs() -> set:
    """Get set of catalog names from Trino."""
    rows = trino_query("SHOW CATALOGS")
    return {r[0] for r in rows} if rows else set()


def build_create_catalog_sql(name: str, props: dict, kafka_config_path: str = None) -> str:
    """Build CREATE CATALOG SQL from properties."""
    connector = props.get("connector.name", "memory")
    if props.get("connector.name") == "kafka":
        invalid = {"kafka.sasl.jaas.config", "kafka.sasl.mechanism", "kafka.security.protocol",
                   "kafka.ssl.endpoint.identification.algorithm"}
        props = {k: v for k, v in props.items() if k not in invalid}
        if kafka_config_path:
            props["kafka.config.resources"] = kafka_config_path
    parts = []
    for k, v in props.items():
        if k == "connector.name":
            continue
        # Escape single quotes in value: ' -> ''
        v_escaped = str(v).replace("'", "''")
        # Property names with - need double quotes
        key = f'"{k}"' if "-" in k or "." in k else k
        parts.append(f"{key} = '{v_escaped}'")
    with_clause = " WITH (" + ", ".join(parts) + ")" if parts else ""
    return f'CREATE CATALOG "{name}" USING {connector}{with_clause}'


def main():
    try:
        get_db_url()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    encryption_key = os.environ.get("TRINO_CATALOG_ENCRYPTION_KEY")
    fernet = get_fernet(encryption_key) if encryption_key else None

    kafka_config_path = "/etc/trino/kafka-client.properties"
    kafka_config_exists = os.path.exists(kafka_config_path)

    seen = set()
    # Verify we can connect before entering loop
    if not TRINO_PASSWORD:
        print("WARNING: TRINO_ADMIN_PASSWORD not set. Watcher will fail if Trino has password auth.", file=sys.stderr)
    else:
        try:
            get_trino_catalogs()
            print("Catalog watcher started. Polling every {POLL_INTERVAL}s. New catalogs in PG will be added without restart.".format(POLL_INTERVAL=POLL_INTERVAL))
        except Exception as e:
            print(f"WARNING: Cannot connect to Trino: {e}", file=sys.stderr)
            print("  Ensure TRINO_ADMIN_USER and TRINO_ADMIN_PASSWORD match your Trino password config.", file=sys.stderr)

    while True:
        try:
            trino_catalogs = get_trino_catalogs()
            conn = connect_pg()
            try:
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
                    if name in trino_catalogs:
                        continue
                    if name in seen:
                        continue
                    try:
                        sql = build_create_catalog_sql(name, dict(props), kafka_config_path if kafka_config_exists else None)
                        trino_query(sql)
                        print(f"  Added catalog: {name}")
                        seen.add(name)
                        trino_catalogs.add(name)
                    except Exception as e:
                        print(f"  Failed to add {name}: {e}", file=sys.stderr)
            finally:
                conn.close()
        except Exception as e:
            print(f"Watcher error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
