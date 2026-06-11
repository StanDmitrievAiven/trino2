# Trino on Aiven App Runtime with PostgreSQL catalog store
# Catalogs/connectors are persisted in PG and restored on each startup
# v2: use launcher directly (no run-trino) - fixes runuser: not found on UBI10-micro

ARG TRINO_IMAGE=trinodb/trino:479

# Stage 1: Python + psycopg2 on UBI (same base as Trino for glibc compatibility)
FROM redhat/ubi10 AS python-deps
RUN dnf install -y python3 python3-pip && dnf clean all
RUN python3 -m pip install --no-cache-dir --target /opt/python-deps psycopg2-binary cryptography bcrypt trino

# Stage 2: Trino with catalog restore
FROM ${TRINO_IMAGE}

USER root

# Copy Python and psycopg2 from UBI stage (Python 3.12 on UBI10)
COPY --from=python-deps /usr/bin/python3 /usr/bin/python3
COPY --from=python-deps /usr/lib64/libpython3.12.so.1.0 /usr/lib64/
COPY --from=python-deps /usr/lib64/python3.12 /usr/lib64/python3.12
RUN mkdir -p /usr/lib64/python3.12/site-packages
COPY --from=python-deps /opt/python-deps/ /usr/lib64/python3.12/site-packages/

# Copy init scripts
COPY init-schema.sql /opt/trino-init/
COPY fetch_catalogs.py /opt/trino-init/
COPY init_password_auth.py /opt/trino-init/
COPY catalog_watcher.py /opt/trino-init/
COPY entrypoint.sh /opt/trino-init/
RUN chmod +x /opt/trino-init/entrypoint.sh /opt/trino-init/fetch_catalogs.py /opt/trino-init/init_password_auth.py /opt/trino-init/catalog_watcher.py

# Ensure catalog dir exists and is writable
RUN mkdir -p /etc/trino/catalog && chown -R trino:trino /etc/trino

# Run as root so entrypoint can configure password auth and write to /etc/trino
USER root

ENTRYPOINT ["/opt/trino-init/entrypoint.sh"]
EXPOSE 8080
