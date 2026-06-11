#!/usr/bin/env python3
"""Prepare connector env vars and Kafka SSL config from Aiven service integrations."""
import os
import re
import sys
from urllib.parse import unquote, urlparse


KAFKA_CONFIG_PATH = "/etc/trino/kafka-client.properties"


def _export_env(key: str, value: str) -> None:
    os.environ[key] = value
    # Persist for subprocesses started later in entrypoint
    with open("/tmp/trino-connector-env", "a") as f:
        f.write(f'export {key}="{value.replace(chr(34), chr(92)+chr(34))}"\n')


def parse_pg_url(env_key: str, password_env_key: str) -> None:
    url = os.environ.get(env_key)
    if not url:
        return
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    parsed = urlparse(url)
    if parsed.password:
        _export_env(password_env_key, unquote(parsed.password))
        print(f"Prepared {password_env_key} from {env_key}")


def write_kafka_ssl_config() -> None:
    key = os.environ.get("KAFKA_ACCESS_KEY")
    cert = os.environ.get("KAFKA_ACCESS_CERT")
    ca = os.environ.get("KAFKA_CA_CERT")
    if not (key and cert and ca):
        return

    lines = [
        "security.protocol=SSL",
        "ssl.keystore.type=PEM",
        f"ssl.keystore.key={key}",
        f"ssl.keystore.certificate.chain={cert}",
        "ssl.truststore.type=PEM",
        f"ssl.truststore.certificates={ca}",
    ]
    os.makedirs(os.path.dirname(KAFKA_CONFIG_PATH), exist_ok=True)
    with open(KAFKA_CONFIG_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(KAFKA_CONFIG_PATH, 0o640)
    print(f"Wrote {KAFKA_CONFIG_PATH} from Kafka integration env vars")


def parse_clickhouse_url() -> None:
    url = os.environ.get("CLICKHOUSE_URL")
    if not url:
        return
    # Aiven may inject https://user:pass@host:port/db
    parsed = urlparse(url)
    if parsed.password:
        _export_env("CLICKHOUSE_PASSWORD", unquote(parsed.password))
    if parsed.username:
        _export_env("CLICKHOUSE_USER", unquote(parsed.username))
    print("Prepared ClickHouse credentials from CLICKHOUSE_URL")


def main() -> None:
    if os.path.exists("/tmp/trino-connector-env"):
        os.remove("/tmp/trino-connector-env")

    parse_pg_url("SUMMIT_PG_URL", "SUMMIT_PG_PASSWORD")
    parse_clickhouse_url()
    write_kafka_ssl_config()


if __name__ == "__main__":
    main()
