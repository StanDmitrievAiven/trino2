#!/usr/bin/env python3
"""Seed default Trino catalog definitions into trino_catalogs if missing."""
import json

from pg_connect import connect_pg

CATALOGS = [
    (
        "summit_pg",
        {
            "connector.name": "postgresql",
            "connection-url": "jdbc:postgresql://pg-37c7de3b-data-innovation-summit.c.aivencloud.com:14208/defaultdb?sslmode=require",
            "connection-user": "avnadmin",
            "connection-password": "${ENV:SUMMIT_PG_PASSWORD}",
        },
    ),
    (
        "summit_clickhouse",
        {
            "connector.name": "clickhouse",
            "connection-url": "jdbc:clickhouse://clickhouse-2a6274d2-data-innovation-summit.c.aivencloud.com:14209/default?ssl=true",
            "connection-user": "${ENV:CLICKHOUSE_USER}",
            "connection-password": "${ENV:CLICKHOUSE_PASSWORD}",
        },
    ),
    (
        "summit_kafka",
        {
            "connector.name": "kafka",
            "kafka.nodes": "kafka-1b5cb1e7-data-innovation-summit.c.aivencloud.com:14210",
            "kafka.table-names": "webshop.public.customers,webshop.public.products,webshop.public.orders,webshop.public.order_items",
            "kafka.hide-internal-columns": "false",
            "kafka.confluent-schema-registry-url": "https://kafka-1b5cb1e7-data-innovation-summit.c.aivencloud.com:14213",
            "kafka.config.resources": "/etc/trino/kafka-client.properties",
        },
    ),
]


def main() -> None:
    conn = connect_pg()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for name, props in CATALOGS:
                cur.execute(
                    """
                    INSERT INTO trino_catalogs (name, properties)
                    VALUES (%s, %s::jsonb)
                    ON CONFLICT (name) DO UPDATE SET properties = EXCLUDED.properties
                    """,
                    (name, json.dumps(props)),
                )
                print(f"  Seeded catalog: {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
