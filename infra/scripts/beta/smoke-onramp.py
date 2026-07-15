#!/usr/bin/env python3
"""
Beta onramp directory smoke.

Kerosene only exposes third-party URLs; it does not process fiat.

Usage:
  export KUBECONFIG=~/.kube/kind-config-kerosene-local
  .local/smoke-venv/bin/python infra/scripts/beta/smoke-onramp.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smoke_common import (  # noqa: E402
    KFE,
    auth_headers,
    fail,
    http_json,
    ok,
    signup_and_finalize,
    start_port_forwards,
    stop_port_forwards,
    warn,
)


def main() -> None:
    managed = "SERVER_URL" not in os.environ
    pf1 = pf2 = None
    if managed:
        pf1, pf2 = start_port_forwards()

    try:
        user = signup_and_finalize("ramp")
        resp = http_json(
            "GET",
            f"{KFE}/kfe/transactions/onramp-urls",
            headers=auth_headers(user),
        )
        if not resp.get("success"):
            fail(f"onramp-urls failed: {resp}")

        urls = resp.get("data") or {}
        if not isinstance(urls, dict):
            fail(f"unexpected onramp payload: {urls}")

        # Empty is valid; non-empty must be http(s) only.
        for key, value in urls.items():
            if not isinstance(value, str) or not value.strip():
                fail(f"empty url for key {key}")
            if not (value.startswith("https://") or value.startswith("http://")):
                fail(f"unsafe onramp url for {key}: {value}")

        if urls:
            ok(f"onramp directory has {len(urls)} link(s): {', '.join(sorted(urls))}")
            for key in ("buy", "moonpay", "transak", "help"):
                if key in urls:
                    ok(f"  {key}={urls[key]}")
        else:
            warn(
                "onramp directory empty — configure KFE_ONRAMP_URL_* on kfe-service "
                "and redeploy for beta buy links"
            )

        print("[+] smoke-onramp passed")
        print("[+] disclaimer: third-party only; Kerosene does not process fiat")
    finally:
        if managed:
            stop_port_forwards(pf1, pf2)


if __name__ == "__main__":
    main()
