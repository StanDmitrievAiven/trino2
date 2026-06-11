# Trino on Aiven App Runtime

This repository contains a Docker-based deployment for [Trino](https://trino.io/) on Aiven's App Runtime platform, with **catalog and connector configuration persisted in PostgreSQL**. After a container restart, Trino restores all catalogs from the database—no lost connections.

This is a simplified fork of [trinostate](https://github.com/StanDmitrievAiven/trinostate) with **PostgreSQL catalog persistence only** (no OPA access control).

## Overview

- Extends the official `trinodb/trino` Docker image
- Stores catalog definitions in a PostgreSQL table (`trino_catalogs`)
- On startup, fetches catalogs from PG and writes `.properties` files to `/etc/trino/catalog/`
- **Password authentication enabled by default** (override via env vars)
- Single-node mode (coordinator + worker in one container), suitable for stateless Aiven apps

## Prerequisites

- Aiven account with App Runtime access
- PostgreSQL database service in Aiven (for catalog storage)
- Git repository access (this repo)

## Required Environment Variables

### Database Connection

- `DATABASE_URL` – Auto-set when you connect a PostgreSQL service in Aiven's "Connect services" step
- `TRINO_CATALOG_DB_URL` – Optional. Use a different PostgreSQL for catalog storage than `DATABASE_URL`

### Optional

- `PORT` – If Aiven injects a `PORT` env var, Trino will listen on it instead of 8080
- `TRINO_CATALOG_ENCRYPTION_KEY` – **Secret.** When set, catalog properties stored in encrypted form are decrypted at startup. Use a Fernet key or any passphrase. Store as a secret in Aiven.
- `TRINO_ADMIN_USER` – Admin username for Web UI and CLI. Default: `trino_admin`
- `TRINO_ADMIN_PASSWORD` – **Secret.** Admin password. Default: `Kv7#mPx9Lq2-Tr1n0` — **change this in production** via Aiven secrets.

## Default Authentication

Password authentication is **always enabled**. If you do not set credentials, the image uses:

| Setting | Default |
|---------|---------|
| Username | `trino_admin` |
| Password | `Kv7#mPx9Lq2-Tr1n0` |

Set `TRINO_ADMIN_USER` and `TRINO_ADMIN_PASSWORD` as Aiven secrets before deploying to production.

## PostgreSQL Schema

The entrypoint creates the `trino_catalogs` table automatically. For reference, the schema is:

```sql
CREATE TABLE IF NOT EXISTS trino_catalogs (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) UNIQUE NOT NULL,
  properties JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Adding Catalogs

Insert rows into `trino_catalogs`. The `properties` column is JSONB with key-value pairs for the catalog's `.properties` file.

### Example: PostgreSQL Connector

```sql
INSERT INTO trino_catalogs (name, properties) VALUES (
  'mypostgres',
  '{
    "connector.name": "postgresql",
    "connection-url": "jdbc:postgresql://host:5432/mydb",
    "connection-user": "user",
    "connection-password": "secret"
  }'::jsonb
);
```

### Example: JMX (Built-in)

```sql
INSERT INTO trino_catalogs (name, properties) VALUES (
  'jmx',
  '{"connector.name": "jmx"}'::jsonb
);
```

### Example: Iceberg with Lakekeeper

```sql
INSERT INTO trino_catalogs (name, properties) VALUES (
  'iceberg',
  '{
    "connector.name": "iceberg",
    "iceberg.catalog.type": "rest",
    "iceberg.rest-catalog.uri": "http://lakekeeper:8181"
  }'::jsonb
);
```

### Encrypted Properties (Recommended for Production)

To encrypt credentials before storing in PostgreSQL:

1. **Generate an encryption key** (store as `TRINO_CATALOG_ENCRYPTION_KEY` secret in Aiven):
   ```bash
   python3 encrypt_catalog.py --generate-key
   ```

2. **Encrypt catalog properties** before inserting:
   ```bash
   python3 encrypt_catalog.py --key "YOUR_KEY_OR_PASSPHRASE" \
     --properties '{"connector.name":"postgresql","connection-url":"jdbc:postgresql://host/db","connection-user":"user","connection-password":"secret"}' \
     --output-format sql
   ```

3. **Run the output SQL** against your PostgreSQL database.

4. **Set `TRINO_CATALOG_ENCRYPTION_KEY`** in Aiven (as a secret) to the same key.

Encrypted rows use the format `{"_encrypted": true, "data": "<base64>"}`. Unencrypted rows continue to work when the key is not set.

## Deployment to Aiven App Runtime

1. **Create a PostgreSQL Service** in Aiven (if you don't have one).
2. **Create an App Runtime Application**
   - **Source:** `https://github.com/StanDmitrievAiven/trino2`
   - **Branch:** `main`
   - **Build context:** `.` (root; Dockerfile is at repo root)
3. **Connect PostgreSQL** in "Connect services" so `DATABASE_URL` is set.
4. **Set `TRINO_ADMIN_PASSWORD`** as a secret (recommended for production).
5. **Add catalogs** to the `trino_catalogs` table (via `psql` or any PostgreSQL client).
6. **Configure port** – Open port **8080** in App Runtime.
7. **Deploy** – Aiven builds and runs the container.

Pushes to `main` will trigger a new build and deployment when auto-deploy is enabled.

## Accessing Trino

- **Web UI:** `https://<your-app-hostname>:8080/` — login with `TRINO_ADMIN_USER` / `TRINO_ADMIN_PASSWORD` (defaults above if unset).
- **CLI:** `trino --server https://<your-app-hostname>:8080 --user trino_admin --password`

Password authentication works behind Aiven's TLS-terminating proxy (`http-server.process-forwarded=true`).

## Project Structure

```
├── Dockerfile                 # Multi-stage: UBI Python + Trino
├── entrypoint.sh              # Validates env, configures auth, fetches catalogs, starts Trino
├── fetch_catalogs.py          # Reads trino_catalogs from PG, writes .properties files
├── init_password_auth.py      # Password auth (always enabled)
├── catalog_watcher.py         # Dynamic CREATE CATALOG from PG
├── encrypt_catalog.py         # Helper to encrypt properties before INSERT (run locally)
├── init-schema.sql            # Schema reference (auto-applied by fetch_catalogs.py)
└── README.md                  # This file
```

## How It Works

1. **Startup:** Entrypoint configures password auth, then runs `fetch_catalogs.py`, which connects to PostgreSQL.
2. **Schema:** Creates `trino_catalogs` table if it doesn't exist.
3. **Fetch:** Selects all rows from `trino_catalogs`.
4. **Write:** For each row, creates `/etc/trino/catalog/{name}.properties`.
5. **Start:** Launches Trino. Trino loads catalogs from the written files.

After a restart, the same flow runs again, so all catalogs are restored from PG.

## Resource Requirements

Trino is memory-intensive. For this **single-node** deployment (coordinator + worker in one container):

| Resource | Minimum | Recommended | Production (reference) |
|----------|---------|-------------|-------------------------|
| **RAM**  | 4 GB    | **8–16 GB** | 64+ GB per worker      |
| **CPU**  | 2 vCPUs | **4–8 vCPUs** | 16–32 vCPUs per worker |

- **JVM heap:** The default image allocates ~70–80% of available RAM to the JVM. For small instances (4–8 GB), expect ~2–4 GB heap.
- **Single-node vs cluster:** Production clusters typically use 64+ GB RAM and 16+ vCPUs per worker. This setup is suitable for development, testing, and light production workloads.
- **Heavy queries:** Hash joins and large aggregations need more memory. If queries fail with out-of-memory errors, increase RAM or tune `query.max-memory-per-node` in `config.properties`.

## Troubleshooting

### No catalogs after restart

- Ensure rows exist in `trino_catalogs`.
- Check application logs for `fetch_catalogs.py` errors.
- Verify `DATABASE_URL` or `TRINO_CATALOG_DB_URL` is set.

### Database connection errors

- Confirm PostgreSQL is reachable from App Runtime (VPC/network).
- Use `postgresql://` or `postgres://` in the connection string.

### Port configuration

- Trino defaults to 8080. If Aiven sets `PORT`, the entrypoint updates `config.properties` automatically.

## Security Considerations

### Current Security Posture

| Layer | Status | Notes |
|-------|--------|-------|
| **PostgreSQL (Aiven)** | Encrypted in transit | TLS (`sslmode=require`) for all connections |
| **PostgreSQL backups** | Encrypted at rest | Aiven encrypts backups |
| **trino_catalogs table** | Optional encryption | Use `TRINO_CATALOG_ENCRYPTION_KEY` to encrypt credentials (see above) |
| **Catalog .properties files** | Plain text at runtime | Decrypted in memory, written for Trino to read |
| **Trino Web UI** | Password protected | Change default credentials before production |

### Recommendations for Production

1. **Change default credentials** – Set `TRINO_ADMIN_PASSWORD` as an Aiven secret.
2. **Restrict PostgreSQL access** – Use a dedicated database/user for `trino_catalogs`. Limit access via Aiven's network controls and IAM.
3. **Use environment variables for credentials** – Store only non-sensitive config in `trino_catalogs`; put passwords in env vars and reference them:
   ```json
   {"connector.name": "postgresql", "connection-url": "jdbc:...", "connection-user": "user", "connection-password": "${ENV:MYCATALOG_PASSWORD}"}
   ```
   Then set `MYCATALOG_PASSWORD` in Aiven's env vars. Trino resolves `${ENV:...}` at runtime.
4. **Use built-in encryption** – Set `TRINO_CATALOG_ENCRYPTION_KEY` and use `encrypt_catalog.py` to encrypt properties before inserting. Credentials are stored encrypted in PostgreSQL.
5. **Enable TLS for Trino** – Use a load balancer or configure Trino to serve HTTPS for client connections.
6. **Audit access** – Enable PostgreSQL audit logging and monitor access to `trino_catalogs`.

## Resources

- [Trino Documentation](https://trino.io/docs/current/)
- [Trino Connectors](https://trino.io/docs/current/connector.html)
- [Aiven App Runtime](https://docs.aiven.io/docs/products/app-runtime)
