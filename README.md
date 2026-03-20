# Trino on Aiven App Runtime

This repository contains a Docker-based deployment for [Trino](https://trino.io/) on Aiven's App Runtime platform, with **catalog and connector configuration persisted in PostgreSQL**. After a container restart, Trino restores all catalogs from the database—no lost connections.

## Overview

- Extends the official `trinodb/trino` Docker image
- Stores catalog definitions in a PostgreSQL table (`trino_catalogs`)
- On startup, fetches catalogs from PG and writes `.properties` files to `/etc/trino/catalog/`
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
- `TRINO_ADMIN_USER` – Admin username for Web UI and CLI. Requires `TRINO_ADMIN_PASSWORD`.
- `TRINO_ADMIN_PASSWORD` – **Secret.** Admin password. When set with `TRINO_ADMIN_USER`, enables password authentication for the Web UI and CLI.

### OPA access control (optional)

Use this when you run [Open Policy Agent](https://www.openpolicyagent.org/) with Trino policies (for example governance synced from [DataHub](https://datahubproject.io/)). Trino calls OPA over HTTP; **both** variables must be set or OPA is skipped.

- `OPA_POLICY_URI` – Full URL to the allow endpoint, e.g. `https://opa.example.com/v1/data/trino/allow`
- `OPA_POLICY_BATCHED_URI` – Full URL to the batch endpoint, e.g. `https://opa.example.com/v1/data/trino/batch`

The App Runtime container must be able to **reach these URLs** (TLS recommended). Trino user names should match identities expected by your policies.

- `OPA_ACCESS_CONTROL_EXTRAS` – Optional. Extra lines appended to `access-control.properties` (newline-separated), e.g. [`opa.http-client.*`](https://trino.io/docs/current/security/opa-access-control.html) for custom trust stores.

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
   - **Source:** `https://github.com/StanDmitrievAiven/trinostate`
   - **Branch:** `main`
   - **Build context:** `.` (root; Dockerfile is at repo root)
3. **Connect PostgreSQL** in "Connect services" so `DATABASE_URL` is set.
4. **Add catalogs** to the `trino_catalogs` table (via `psql` or any PostgreSQL client).
5. **Configure port** – Open port **8080** in App Runtime.
6. **Deploy** – Aiven builds and runs the container.

Pushes to `main` will trigger a new build and deployment when auto-deploy is enabled.

## Accessing Trino

- **Web UI:** `https://<your-app-hostname>:8080/` – If `TRINO_ADMIN_USER`/`TRINO_ADMIN_PASSWORD` are set, login with those credentials.
- **CLI:** `trino --server https://<your-app-hostname>:8080 --user <username> --password`

Password authentication works behind Aiven's TLS-terminating proxy (`http-server.process-forwarded=true`).

## Project Structure

```
├── Dockerfile                 # Multi-stage: UBI Python + Trino
├── entrypoint.sh              # Validates env, configures auth / OPA, fetches catalogs, starts Trino
├── fetch_catalogs.py          # Reads trino_catalogs from PG, writes .properties files
├── init_password_auth.py      # Password auth when TRINO_ADMIN_USER/PASSWORD set
├── init_opa_access_control.py # OPA plugin when OPA_POLICY_URI / OPA_POLICY_BATCHED_URI set
├── catalog_watcher.py         # Dynamic CREATE CATALOG from PG
├── encrypt_catalog.py         # Helper to encrypt properties before INSERT (run locally)
├── init-schema.sql            # Schema reference (auto-applied by fetch_catalogs.py)
└── README.md                  # This file
```

## How It Works

1. **Startup:** Entrypoint optionally configures password auth and **OPA** (`access-control.properties`), then runs `fetch_catalogs.py`, which connects to PostgreSQL.
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

### OPA / queries denied or Trino fails to start

- Confirm `OPA_POLICY_URI` and `OPA_POLICY_BATCHED_URI` are correct and reachable **from the Trino container** (curl from a debug shell or check OPA logs).
- If OPA is down, Trino may reject governed operations; run OPA with high availability or relax policies for break-glass (advanced).
- For TLS to OPA with a private CA, set `OPA_ACCESS_CONTROL_EXTRAS` with the appropriate `opa.http-client.*` properties (see Trino docs).

## Security Considerations

### Current Security Posture

| Layer | Status | Notes |
|-------|--------|-------|
| **PostgreSQL (Aiven)** | Encrypted in transit | TLS (`sslmode=require`) for all connections |
| **PostgreSQL backups** | Encrypted at rest | Aiven encrypts backups |
| **trino_catalogs table** | Optional encryption | Use `TRINO_CATALOG_ENCRYPTION_KEY` to encrypt credentials (see above) |
| **Catalog .properties files** | Plain text at runtime | Decrypted in memory, written for Trino to read |
| **Trino Web UI** | May expose credentials | `CREATE CATALOG` queries (including passwords) can appear in logs/UI |

### Gaps and Risks

1. **Credentials in plain text** – Without `TRINO_CATALOG_ENCRYPTION_KEY`, credentials are stored unencrypted. Use encryption for production.
2. **Runtime exposure** – Decrypted properties are written to `.properties` files. Restrict filesystem access.
3. **Trino's alternative** – Trino also supports `${ENV:VARIABLE}` for credentials in config; combine with encryption for defense in depth.

### Recommendations for Production

1. **Restrict PostgreSQL access** – Use a dedicated database/user for `trino_catalogs`. Limit access via Aiven's network controls and IAM.
2. **Use environment variables for credentials** – Store only non-sensitive config in `trino_catalogs`; put passwords in env vars and reference them:
   ```json
   {"connector.name": "postgresql", "connection-url": "jdbc:...", "connection-user": "user", "connection-password": "${ENV:MYCATALOG_PASSWORD}"}
   ```
   Then set `MYCATALOG_PASSWORD` in Aiven's env vars. Trino resolves `${ENV:...}` at runtime.
3. **Use built-in encryption** – Set `TRINO_CATALOG_ENCRYPTION_KEY` and use `encrypt_catalog.py` to encrypt properties before inserting. Credentials are stored encrypted in PostgreSQL.
4. **Enable TLS for Trino** – Use a load balancer or configure Trino to serve HTTPS for client connections.
5. **Audit access** – Enable PostgreSQL audit logging and monitor access to `trino_catalogs`.

## Resources

- [Trino Documentation](https://trino.io/docs/current/)
- [Trino OPA access control](https://trino.io/docs/current/security/opa-access-control.html)
- [Trino Connectors](https://trino.io/docs/current/connector.html)
- [Aiven App Runtime](https://docs.aiven.io/docs/products/app-runtime)
