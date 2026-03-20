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


def main() -> None:
    uri = os.environ.get("OPA_POLICY_URI", "").strip()
    batched = os.environ.get("OPA_POLICY_BATCHED_URI", "").strip()

    if not uri or not batched:
        print(
            "OPA access control: not configured (optional). "
            "Set both OPA_POLICY_URI and OPA_POLICY_BATCHED_URI to enable.",
            file=sys.stderr,
        )
        return

    config_dir = "/etc/trino"
    out_path = os.path.join(config_dir, "access-control.properties")

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
