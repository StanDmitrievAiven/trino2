"""PostgreSQL connection helper for Aiven App Runtime (PROJECT_CA_CERT + DATABASE_URL)."""
import base64
import os
import tempfile
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg2


def get_db_url() -> str:
    db_url = os.environ.get("TRINO_CATALOG_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL or TRINO_CATALOG_DB_URL must be set")
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[11:]
    return db_url


def _strip_sslmode(db_url: str) -> str:
    parsed = urlparse(db_url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != "sslmode"]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), parsed.fragment))


def connect_pg():
    db_url = _strip_sslmode(get_db_url())
    connect_kwargs = {}

    ca_b64 = os.environ.get("PROJECT_CA_CERT")
    if ca_b64:
        ca_pem = base64.b64decode(ca_b64).decode()
        fd, ca_path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(fd, "w") as f:
            f.write(ca_pem)
        connect_kwargs["sslmode"] = "verify-full"
        connect_kwargs["sslrootcert"] = ca_path
    else:
        connect_kwargs["sslmode"] = "require"

    return psycopg2.connect(db_url, **connect_kwargs)
