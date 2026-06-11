"""Encrypt / decrypt Trino catalog properties and Kafka config for trino_catalogs PG store."""
from __future__ import annotations

import base64
import json
import os
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError as e:
    raise RuntimeError("cryptography package required") from e

_SALT = b"trino-catalog-store"
_ITERATIONS = 480000


def get_fernet(key: str) -> Fernet:
    """Create Fernet from a 44-char key or derive from a passphrase."""
    k = key.encode() if isinstance(key, str) else key
    if len(k) == 44:
        try:
            return Fernet(k)
        except Exception:
            pass
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=_ITERATIONS,
    )
    derived = base64.urlsafe_b64encode(kdf.derive(k))
    return Fernet(derived)


def require_encryption_key() -> str:
    key = os.environ.get("TRINO_CATALOG_ENCRYPTION_KEY", "").strip()
    if not key:
        raise ValueError(
            "TRINO_CATALOG_ENCRYPTION_KEY is required for encrypted catalog storage"
        )
    return key


def encrypt_payload(data: dict[str, Any] | str, key: str | None = None) -> dict[str, Any]:
    fernet = get_fernet(key or require_encryption_key())
    raw = data if isinstance(data, str) else json.dumps(data)
    return {"_encrypted": True, "data": fernet.encrypt(raw.encode()).decode()}


def decrypt_payload(stored: dict[str, Any] | str, key: str | None = None) -> dict[str, Any] | str:
    if isinstance(stored, str):
        stored = json.loads(stored)
    if not isinstance(stored, dict) or not stored.get("_encrypted"):
        return stored
    fernet = get_fernet(key or require_encryption_key())
    try:
        plain = fernet.decrypt(stored["data"].encode()).decode()
    except InvalidToken as e:
        raise ValueError("Failed to decrypt catalog payload — check TRINO_CATALOG_ENCRYPTION_KEY") from e
    try:
        return json.loads(plain)
    except json.JSONDecodeError:
        return plain


def decrypt_properties(props: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    decrypted = decrypt_payload(props, key)
    if isinstance(decrypted, dict):
        return decrypted
    raise ValueError("Decrypted catalog properties are not a JSON object")
