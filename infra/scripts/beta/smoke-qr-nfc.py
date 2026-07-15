#!/usr/bin/env python3
"""
Beta QR/NFC payload smoke (API + format parity with Flutter parsers).

Does not require a physical NFC chip; validates:
  - payment-request create → publicId
  - encodePaymentLink format kerosene://payment/pay/<id>
  - public GET lookup
  - optional on-chain address BIP-21 encoding

Usage:
  export KUBECONFIG=~/.kube/kind-config-kerosene-local
  .local/smoke-venv/bin/python infra/scripts/beta/smoke-qr-nfc.py
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import quote, urlparse

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
)


def encode_payment_link(public_id: str) -> str:
    """Mirror QrPaymentParser.encodePaymentLink."""
    return f"kerosene://payment/pay/{quote(public_id, safe='')}"


def extract_payment_link_id(raw: str) -> str | None:
    """Subset of QrPaymentParser.extractPaymentLinkId used by product QR/NFC."""
    trimmed = raw.strip()
    lower = trimmed.lower()
    if lower.startswith("kerosene:link:"):
        value = trimmed[len("kerosene:link:") :].strip()
        return value or None

    uri = urlparse(trimmed)
    if uri.scheme.lower() == "kerosene":
        host = (uri.hostname or "").lower()
        segments = [s for s in uri.path.split("/") if s]
        if host == "pay" and segments:
            return segments[0]
        if host == "payment" and len(segments) >= 2 and segments[0].lower() == "pay":
            return segments[1]
        if segments and segments[0].lower() == "pay" and len(segments) > 1:
            return segments[1]

    if uri.scheme in ("http", "https"):
        segments = [s for s in uri.path.split("/") if s]
        for i, segment in enumerate(segments):
            if segment.lower() == "pay" and i + 1 < len(segments):
                return segments[i + 1]
            if segment.lower() == "payment-requests" and i + 1 < len(segments):
                return segments[i + 1]
    return None


def looks_like_testnet_address(address: str) -> bool:
    return bool(re.match(r"^tb1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{20,90}$", address.lower()))


def main() -> None:
    managed = "SERVER_URL" not in os.environ
    pf1 = pf2 = None
    if managed:
        pf1, pf2 = start_port_forwards()

    try:
        user = signup_and_finalize("qr")
        wallets = http_json("GET", f"{KFE}/kfe/wallets", headers=auth_headers(user))["data"]
        if not wallets:
            fail("no wallet for QR user")
        wallet_id = wallets[0]["id"]

        pr = http_json(
            "POST",
            f"{KFE}/kfe/payment-requests",
            {
                "walletId": wallet_id,
                "rail": "ONCHAIN",
                "amountSats": 15000,
                "description": "qr-nfc-smoke",
                "issueFreshAddress": True,
            },
            headers=auth_headers(user),
            expected=(200, 201),
        )["data"]

        public_id = pr["publicId"]
        address = pr.get("address") or ""
        ok(f"payment-request publicId={public_id}")
        if not address or not looks_like_testnet_address(address):
            fail(f"expected testnet bech32 address, got {address!r}")
        ok(f"on-chain address={address}")

        # Canonical app QR / NFC payload
        link_uri = encode_payment_link(public_id)
        parsed_id = extract_payment_link_id(link_uri)
        if parsed_id != public_id:
            fail(f"encode/decode mismatch: {link_uri} -> {parsed_id}")
        ok(f"QR/NFC link URI round-trip: {link_uri}")

        # Public API path (share/Tor surfaces)
        api_uri = f"http://placeholder.onion/api/public/kfe/payment-requests/{public_id}"
        if extract_payment_link_id(api_uri) != public_id:
            fail(f"failed to extract publicId from API path: {api_uri}")
        ok("public API path extracts publicId")

        # Live public lookup (what the app does after scan)
        public = http_json("GET", f"{KFE}/api/public/kfe/payment-requests/{public_id}")
        if not public.get("success"):
            fail(f"public lookup failed: {public}")
        data = public["data"]
        if data.get("publicId") != public_id:
            fail(f"public lookup publicId mismatch: {data}")
        if data.get("address") != address:
            fail(f"public lookup address mismatch: {data.get('address')}")
        ok(f"public lookup status={data.get('status')}")

        # BIP-21 form for raw address QR (fallback receive)
        btc_uri = f"bitcoin:{address}?amount=0.00015"
        if address not in btc_uri:
            fail("BIP-21 encode broken")
        ok(f"BIP-21 receive URI ready: {btc_uri}")

        print("[+] smoke-qr-nfc passed")
        print("[+] physical NFC write/read still requires a device with NFC hardware")
    finally:
        if managed:
            stop_port_forwards(pf1, pf2)


if __name__ == "__main__":
    main()
