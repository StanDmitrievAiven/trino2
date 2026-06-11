#!/bin/sh
set -e

# --- Environment Variable Check ---
# Support DATABASE_URL (Aiven) or TRINO_CATALOG_DB_URL
CATALOG_DB_URL="${TRINO_CATALOG_DB_URL:-$DATABASE_URL}"
if [ -z "$CATALOG_DB_URL" ]; then
    echo "ERROR: Database connection required. Set either:"
    echo "  - DATABASE_URL (auto-set when connecting PostgreSQL in Aiven)"
    echo "  - TRINO_CATALOG_DB_URL (for a separate catalog store)"
    exit 1
fi
echo "Using catalog database. Proceeding with startup."
echo "---"

# --- Password authentication (always enabled; override via env vars) ---
export TRINO_ADMIN_USER="${TRINO_ADMIN_USER:-trino_admin}"
export TRINO_ADMIN_PASSWORD="${TRINO_ADMIN_PASSWORD:-Kv7#mPx9Lq2-Tr1n0}"
echo "Configuring password authentication for user: $TRINO_ADMIN_USER"
python3 /opt/trino-init/init_password_auth.py
echo "---"

# --- Prepare connector credentials from Aiven service integrations ---
python3 /opt/trino-init/prepare_connector_env.py
if [ -f /tmp/trino-connector-env ]; then
    # shellcheck disable=SC1091
    . /tmp/trino-connector-env
fi
echo "---"

# --- Initialize schema and fetch catalogs from PG ---
echo "Seeding and fetching catalogs from database..."
python3 /opt/trino-init/seed_catalogs.py
python3 /opt/trino-init/fetch_catalogs.py
chown -R trino:trino /etc/trino/catalog 2>/dev/null || true
echo "Catalogs synced."
echo "---"

# --- Port configuration (Aiven may inject PORT) ---
TRINO_PORT="${PORT:-8080}"
if [ -n "$PORT" ]; then
    echo "Configuring Trino to listen on port $PORT"
    CONFIG_FILE="/etc/trino/config.properties"
    if [ -f "$CONFIG_FILE" ]; then
        sed -i "s/^http-server.http.port=.*/http-server.http.port=$PORT/" "$CONFIG_FILE"
        sed -i "s|^discovery.uri=.*|discovery.uri=http://localhost:$PORT|" "$CONFIG_FILE"
    fi
fi

# --- Enable dynamic catalog management (add catalogs without restart) ---
CONFIG_FILE="/etc/trino/config.properties"
if [ -f "$CONFIG_FILE" ] && ! grep -q "catalog.management" "$CONFIG_FILE"; then
    echo "catalog.management=dynamic" >> "$CONFIG_FILE"
    echo "Enabled dynamic catalog management."
fi

# --- Start Trino in background ---
echo "Starting Trino..."
/usr/lib/trino/bin/launcher run --etc-dir /etc/trino -Dnode.id=trino &
TRINO_PID=$!

# --- Wait for Trino to be ready ---
echo "Waiting for Trino to be ready..."
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${TRINO_PORT}/v1/status" 2>/dev/null | grep -q "200"; then
        echo "Trino is ready."
        break
    fi
    sleep 2
done

# --- Start catalog watcher (polls PG, runs CREATE CATALOG for new connectors) ---
export TRINO_INTERNAL_URL="http://127.0.0.1:${TRINO_PORT}"
echo -n "$TRINO_ADMIN_PASSWORD" > /tmp/trino-watcher-password
chmod 600 /tmp/trino-watcher-password
export TRINO_ADMIN_PASSWORD_FILE=/tmp/trino-watcher-password
python3 /opt/trino-init/catalog_watcher.py &
WATCHER_PID=$!

# --- Wait for Trino (main process) ---
wait $TRINO_PID
