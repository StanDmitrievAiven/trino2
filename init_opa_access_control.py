#!/usr/bin/env python3
"""
Optional Open Policy Agent (OPA) access control for Trino.

When OPA_POLICY_URI and OPA_POLICY_BATCHED_URI are set, writes
/etc/trino/access-control.properties so Trino uses the built-in OPA plugin.

See: https://trino.io/docs/current/security/opa-access-control.html

Optional: OPA_ACCESS_CONTROL_EXTRAS — extra lines appended to the file
(newline-separated), e.g. opa.http-client.truststore.path=... for custom TLS.
"""
from __future__ import annotations

import os
import pwd
import sys
from urllib.parse import urlparse


def _opa_url_ok(name: str, value: str) -> bool:
    """Trino needs absolute URLs with scheme and host; placeholders break at runtime."""
    v = value.strip()
    if not v:
        return False
    if v in (name, f"${{{name}}}", f"${name}"):
        print(
            f"OPA access control: {name} looks like an unresolved placeholder ({v!r}). "
            f"Set it to a full URL, e.g. https://opa.example.com/v1/data/trino/allow",
            file=sys.stderr,
        )
        return False
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print(
            f"OPA access control: {name} must be a full http(s) URL with a host (got {v!r}).",
            file=sys.stderr,
        )
        return False
    return True


def main() -> None:
    uri = os.environ.get("OPA_POLICY_URI", "").strip()
    batched = os.environ.get("OPA_POLICY_BATCHED_URI", "").strip()

    config_dir = "/etc/trino"
    out_path = os.path.join(config_dir, "access-control.properties")

    if not uri or not batched:
        if os.path.isfile(out_path):
            os.remove(out_path)
        print(
            "OPA access control: not configured (optional). "
            "Set both OPA_POLICY_URI and OPA_POLICY_BATCHED_URI to enable.",
            file=sys.stderr,
        )
        return

    if not _opa_url_ok("OPA_POLICY_URI", uri) or not _opa_url_ok(
        "OPA_POLICY_BATCHED_URI", batched
    ):
        if os.path.isfile(out_path):
            os.remove(out_path)
        print(
            "OPA access control: disabled due to invalid URIs. "
            "Fix env vars or unset both to run without OPA.",
            file=sys.stderr,
        )
        return

    lines = [
        "access-control.name=opa",
        f"opa.policy.uri={uri}",
        f"opa.policy.batched-uri={batched}",
    ]
    extras = os.environ.get("OPA_ACCESS_CONTROL_EXTRAS", "").strip()
    if extras:
        lines.extend(extras.splitlines())

    content = "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    os.chmod(out_path, 0o640)
    try:
        trino = pwd.getpwnam("trino")
        os.chown(out_path, trino.pw_uid, trino.pw_gid)
    except (KeyError, ImportError):
        pass

    print(f"OPA access control configured: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
