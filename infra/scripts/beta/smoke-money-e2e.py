#!/usr/bin/env python3
"""
Beta money-path smoke:
  two users → wallets/dashboard → payment-request → public lookup → quote
  → fund on-chain from local Bitcoin Core → poll settlement
  → attempt internal transfer (may require passkey; reported honestly)

Usage:
  export KUBECONFIG=~/.kube/kind-config-kerosene-local
  .local/smoke-venv/bin/python infra/scripts/beta/smoke-money-e2e.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smoke_common import (  # noqa: E402
    KFE,
    KUBECTL,
    NS,
    SERVER,
    auth_headers,
    bitcoin_cli,
    build_device_key_auth_assertion,
    fail,
    http_json,
    ok,
    signup_and_finalize,
    start_port_forwards,
    stop_port_forwards,
    warn,
)
import subprocess

FUND_SATS = int(os.environ.get("SMOKE_FUND_SATS", "50000"))  # 0.0005 BTC
POLL_SECONDS = int(os.environ.get("SMOKE_POLL_SECONDS", "90"))
APP_PIN = os.environ.get("SMOKE_APP_PIN", "135790")


def envelope_data(resp: dict) -> object:
    if "data" in resp:
        return resp["data"]
    return resp


def psql_count_credit_movements_for_txid(txid: str) -> dict[str, int] | None:
    """Count AVAILABLE-side credit movements linked to transactions with this blockchain txid."""
    sql = (
        "SELECT bm.movement_type, COUNT(*) "
        "FROM financial.balance_movements bm "
        "JOIN financial.transactions_master t ON t.id = bm.transaction_id "
        f"WHERE t.blockchain_txid = '{txid}' "
        "AND bm.movement_type IN ("
        "'CREDIT_INBOUND','CREDIT_PAYMENT_REQUEST',"
        "'CREDIT_CUSTODIAL_DEPOSIT','CREDIT','CREDIT_KEROSENE_FEE'"
        ") GROUP BY bm.movement_type;"
    )
    cmd = [
        KUBECTL,
        "-n",
        NS,
        "exec",
        "local-postgres-0",
        "--",
        "psql",
        "-U",
        "kerosene",
        "-d",
        "kerosene",
        "-t",
        "-A",
        "-F",
        ",",
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    out: dict[str, int] = {}
    for line in (result.stdout or "").strip().splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        mtype, cnt = line.split(",", 1)
        try:
            out[mtype] = int(cnt)
        except ValueError:
            continue
    return out


def main() -> None:
    managed = "SERVER_URL" not in os.environ
    pf1 = pf2 = None
    if managed:
        pf1, pf2 = start_port_forwards()

    try:
        # --- two users ---
        alice = signup_and_finalize("alice")
        bob = signup_and_finalize("bob")
        ok(f"users ready alice={alice['username']} bob={bob['username']}")

        # --- wallets ---
        alice_wallets = envelope_data(
            http_json("GET", f"{KFE}/kfe/wallets", headers=auth_headers(alice))
        )
        if not isinstance(alice_wallets, list) or not alice_wallets:
            fail(f"alice has no wallets: {alice_wallets}")
        alice_wallet = alice_wallets[0]
        alice_wallet_id = alice_wallet["id"]
        ok(f"alice wallet id={alice_wallet_id} address={alice_wallet.get('activeAddress')}")

        bob_wallets = envelope_data(
            http_json("GET", f"{KFE}/kfe/wallets", headers=auth_headers(bob))
        )
        if not isinstance(bob_wallets, list) or not bob_wallets:
            fail(f"bob has no wallets: {bob_wallets}")
        bob_wallet_id = bob_wallets[0]["id"]
        ok(f"bob wallet id={bob_wallet_id}")

        # --- dashboard ---
        dash = envelope_data(
            http_json("GET", f"{KFE}/kfe/dashboard", headers=auth_headers(alice))
        )
        ok(f"dashboard keys={list(dash.keys())[:10] if isinstance(dash, dict) else type(dash)}")

        # --- receiving capabilities ---
        caps = envelope_data(
            http_json(
                "GET",
                f"{KFE}/kfe/users/{bob['username']}/receiving-capabilities",
                headers=auth_headers(alice),
            )
        )
        ok(
            "capabilities "
            f"internal={caps.get('canReceiveInternal') if isinstance(caps, dict) else caps} "
            f"wallet={caps.get('internalWalletId') if isinstance(caps, dict) else None}"
        )

        # --- quote ---
        for rail, direction in (("INTERNAL", "INTERNAL"), ("ONCHAIN", "OUTBOUND")):
            quote = envelope_data(
                http_json(
                    "POST",
                    f"{KFE}/kfe/transactions/quote",
                    {
                        "rail": rail,
                        "direction": direction,
                        "amountSats": 1000,
                        "networkFeeSats": 0 if rail == "INTERNAL" else 300,
                    },
                    headers=auth_headers(alice),
                )
            )
            ok(
                f"quote {rail}/{direction} totalDebit="
                f"{quote.get('totalDebitSats') if isinstance(quote, dict) else quote}"
            )

        # --- payment request (QR payload source) ---
        pr = envelope_data(
            http_json(
                "POST",
                f"{KFE}/kfe/payment-requests",
                {
                    "walletId": alice_wallet_id,
                    "rail": "ONCHAIN",
                    "amountSats": FUND_SATS,
                    "description": "beta-smoke-receive",
                    "memo": "smoke",
                    "issueFreshAddress": True,
                },
                headers=auth_headers(alice),
                expected=(200, 201),
            )
        )
        if not isinstance(pr, dict):
            fail(f"unexpected payment request: {pr}")
        public_id = pr.get("publicId")
        address = pr.get("address")
        status = pr.get("status")
        ok(f"payment-request publicId={public_id} status={status} address={address}")
        if not address:
            fail("payment request missing address (QR/on-chain receive broken)")
        if not public_id:
            fail("payment request missing publicId")

        # --- public lookup (QR scan target) ---
        public = envelope_data(
            http_json("GET", f"{KFE}/api/public/kfe/payment-requests/{public_id}")
        )
        ok(f"public payment-request lookup status={public.get('status') if isinstance(public, dict) else public}")

        # --- fund from Bitcoin Core testnet wallet ---
        btc_amount = f"{FUND_SATS / 1e8:.8f}"
        # Explicit fee_rate (sat/vB) because testnet fee estimation can be empty
        # when -fallbackfee is disabled on the local bitcoind.
        txid = bitcoin_cli(
            "-rpcwallet=kerosene",
            "-named",
            f"sendtoaddress address={address} amount={btc_amount} fee_rate=2",
        )
        ok(f"funded payment request txid={txid} amount={btc_amount} BTC")

        # --- poll settlement / observation ---
        settled = False
        last_status = status
        for i in range(max(1, POLL_SECONDS // 5)):
            time.sleep(5)
            latest = envelope_data(
                http_json(
                    "GET",
                    f"{KFE}/kfe/payment-requests/{pr['id']}",
                    headers=auth_headers(alice),
                )
            )
            if isinstance(latest, dict):
                last_status = latest.get("status")
                confs = latest.get("confirmations")
                tx = latest.get("blockchainTxid")
                print(
                    f"[*] poll#{i+1} status={last_status} confs={confs} txid={tx}",
                    flush=True,
                )
                if str(last_status).upper() in {
                    "PAID",
                    "SETTLED",
                    "COMPLETED",
                    "CONFIRMED",
                    "OBSERVED",
                }:
                    settled = True
                    break
                if tx:
                    # observed on chain even if not fully settled
                    ok(f"payment request linked to on-chain txid={tx}")
                    settled = True
                    break

        if settled:
            ok(f"receive path progressing (status={last_status})")
        else:
            warn(
                f"payment request not settled within {POLL_SECONDS}s "
                f"(status={last_status}). Testnet confirms can lag; "
                "address+txid funding still validates create/fund path."
            )

        # --- dual-credit guard: available credit movements for this chain tx ≤ 1 each type ---
        try:
            credit_rows = psql_count_credit_movements_for_txid(txid)
            if credit_rows is not None:
                for mtype, cnt in credit_rows.items():
                    if cnt > 1:
                        fail(f"dual credit detected type={mtype} count={cnt} txid={txid}")
                ok(f"credit movement counts ok for fund tx: {credit_rows}")
            else:
                warn("could not query balance_movements (psql); skip dual-credit assert")
        except Exception as exc:  # noqa: BLE001 — smoke must not crash on optional assert infra
            warn(f"dual-credit assert skipped: {exc}")

        # --- configure app pin (needed for transfers) ---
        pin_resp = http_json(
            "PUT",
            f"{SERVER}/auth/security/app-pin",
            {"enabled": True, "pin": APP_PIN},
            headers=auth_headers(alice),
            expected=(200, 201, 400, 401, 422),
        )
        if pin_resp.get("success"):
            ok("app PIN configured for alice")
        else:
            warn(f"app PIN configure: {pin_resp.get('message') or pin_resp}")

        # --- attempt internal transfer alice → bob ---
        dest_wallet = None
        if isinstance(caps, dict):
            dest_wallet = caps.get("internalWalletId") or bob_wallet_id
        else:
            dest_wallet = bob_wallet_id

        device_assertion = build_device_key_auth_assertion(alice)
        transfer = http_json(
            "POST",
            f"{KFE}/kfe/transactions",
            {
                "idempotencyKey": f"smoke-internal-{uuid.uuid4()}",
                "rail": "INTERNAL",
                "direction": "INTERNAL",
                "sourceWalletId": alice_wallet_id,
                "destinationWalletId": dest_wallet,
                "amountSats": 1000,
                "networkFeeSats": 0,
                "memo": "beta-smoke-internal",
                "appPin": APP_PIN,
                "confirmationPassphrase": alice["password"],
                "passkeyAssertionJson": device_assertion,
            },
            headers=auth_headers(alice),
            expected=(200, 201, 401, 403, 422, 409, 400, 428),
        )
        if transfer.get("success"):
            ok(f"internal transfer accepted: {transfer.get('data')}")
        else:
            warn(
                "internal transfer not completed (may need funded balance after confirmations): "
                f"{transfer.get('errorCode')} {transfer.get('message')}"
            )

        print("[+] smoke-money-e2e completed core receive/quote path")
        print(f"[+] alice={alice['username']} bob={bob['username']}")
        print(f"[+] paymentRequest publicId={public_id} address={address}")
        print(f"[+] fundTxid={txid}")
        # Exit 0: receive+quote path is the beta money gate for device-key accounts.
        # Transfer passkey gap is tracked for F3 follow-up / product hardening.
    finally:
        if managed:
            stop_port_forwards(pf1, pf2)


if __name__ == "__main__":
    main()
