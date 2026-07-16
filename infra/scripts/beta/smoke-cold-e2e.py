#!/usr/bin/env python3
"""
Cold (WATCH_ONLY) smoke against kerosene-local:

  1) signup → create WATCH_ONLY with addr() descriptor from Core
  2) fund address from Bitcoin Core
  3) poll observed_sats + inbound history
  4) spend UTXO from Core (external Electrum-like spend)
  5) poll observed drop + outbound history
  6) assert observed did not re-inflate above funded amount after settle

Usage (from repo root):
  export KUBECONFIG=~/.kube/kind-config-kerosene-local
  .local/smoke-venv/bin/python infra/scripts/beta/smoke-cold-e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smoke_common import (  # noqa: E402
    KFE,
    KUBECTL,
    NS,
    auth_headers,
    bitcoin_cli,
    fail,
    http_json,
    ok,
    signup_and_finalize,
    start_port_forwards,
    stop_port_forwards,
    warn,
)
import subprocess

FUND_SATS = int(os.environ.get("SMOKE_COLD_FUND_SATS", "25000"))
# Cold observe is scheduled across all WATCH_ONLY wallets; local kind can lag 3–6 min.
POLL_SECONDS = int(os.environ.get("SMOKE_COLD_POLL_SECONDS", "420"))
TOLERANCE_SATS = int(os.environ.get("SMOKE_COLD_TOLERANCE_SATS", "500"))


def envelope_data(resp: dict) -> object:
    if isinstance(resp, dict) and "data" in resp:
        return resp["data"]
    return resp


def wallet_observed(user: dict, wallet_id: str) -> int:
    """Read observed_sats from dashboard (wallet list DTO has no balance fields)."""
    dash = envelope_data(
        http_json("GET", f"{KFE}/kfe/dashboard", headers=auth_headers(user), timeout=60)
    )
    if not isinstance(dash, dict):
        return -1
    for list_key in ("wallets", "accounts", "bitcoinWallets", "walletSummaries"):
        rows = dash.get(list_key)
        if not isinstance(rows, list):
            continue
        for w in rows:
            if not isinstance(w, dict):
                continue
            if str(w.get("walletId") or w.get("id") or "") != str(wallet_id):
                continue
            for key in (
                "observedSats",
                "observed_sats",
                "primarySats",
                "primary_sats",
                "balanceSats",
            ):
                if w.get(key) is not None:
                    try:
                        return int(w[key])
                    except (TypeError, ValueError):
                        pass
    # Direct balances table via optional wallet detail if present later
    return -1


def list_transactions(user: dict, wallet_id: str | None = None) -> list[dict]:
    resp = http_json(
        "GET",
        f"{KFE}/kfe/transactions?limit=50",
        headers=auth_headers(user),
        expected=(200, 400),
    )
    data = envelope_data(resp)
    if not isinstance(data, list):
        return []
    rows: list[dict] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        if wallet_id is None:
            rows.append(row)
            continue
        wid = str(wallet_id)
        if wid in {
            str(row.get("sourceWalletId") or ""),
            str(row.get("destinationWalletId") or ""),
            str(row.get("walletId") or ""),
        }:
            rows.append(row)
    return rows


def bitcoin_cli_shell(inner: str) -> str:
    """Run bitcoin-cli with a full shell fragment (for complex -named JSON)."""
    cmd = [
        KUBECTL,
        "-n",
        NS,
        "exec",
        "deploy/local-bitcoin",
        "--",
        "sh",
        "-c",
        "/opt/bitcoin-28.0/bin/bitcoin-cli -datadir=/home/bitcoin/.bitcoin "
        "-rpcuser=kerosene -rpcpassword=kerosene-local-rpc "
        + inner,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"bitcoin-cli shell failed: {result.stderr or result.stdout}")
    return result.stdout.strip()


def descriptor_for_address(address: str) -> str:
    raw = bitcoin_cli("getdescriptorinfo", f"'addr({address})'")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        fail(f"getdescriptorinfo non-JSON: {raw[:300]}")
        return ""
    desc = info.get("descriptor")
    if not desc:
        fail(f"no descriptor for {address}: {raw[:300]}")
    return str(desc)


def main() -> None:
    managed = "SERVER_URL" not in os.environ
    pf1 = pf2 = None
    if managed:
        pf1, pf2 = start_port_forwards()

    try:
        user = signup_and_finalize("cold")
        ok(f"user ready {user['username']}")

        address = bitcoin_cli(
            "-rpcwallet=kerosene",
            "getnewaddress",
            f"smoke-cold-{uuid.uuid4().hex[:8]}",
            "bech32",
        )
        if not address.startswith("tb1") and not address.startswith("bcrt"):
            warn(f"unexpected address format: {address}")
        desc = descriptor_for_address(address)
        ok(f"core address={address} descriptor={desc[:48]}…")

        # Post-activation runs scantxoutset + first observe (can take 30–90s).
        created = envelope_data(
            http_json(
                "POST",
                f"{KFE}/kfe/wallets",
                {
                    "kind": "WATCH_ONLY",
                    "label": "smoke-cold",
                    "descriptor": desc,
                    "initialAddress": address,
                    "issueInitialAddress": False,
                },
                headers=auth_headers(user),
                expected=(200, 201),
                timeout=120,
            )
        )
        if not isinstance(created, dict) or not created.get("id"):
            fail(f"create WATCH_ONLY failed: {created}")
        wallet_id = str(created["id"])
        ok(f"cold wallet id={wallet_id} kind={created.get('kind')}")

        btc_amount = f"{FUND_SATS / 1e8:.8f}"
        fund_txid = bitcoin_cli(
            "-rpcwallet=kerosene",
            "-named",
            f"sendtoaddress address={address} amount={btc_amount} fee_rate=2",
        )
        ok(f"funded cold address txid={fund_txid} amountSats={FUND_SATS}")

        observed = -1
        inbound_seen = False
        for i in range(max(1, POLL_SECONDS // 5)):
            time.sleep(5)
            observed = wallet_observed(user, wallet_id)
            txs = list_transactions(user, wallet_id)
            inbound_seen = any(
                str(t.get("direction", "")).upper() == "INBOUND"
                and (
                    str(t.get("blockchainTxid") or "").lower() == fund_txid.lower()
                    or fund_txid.lower() in str(t.get("providerReference") or "").lower()
                    or int(t.get("receiverAmountSats") or t.get("grossAmountSats") or 0)
                    >= FUND_SATS - TOLERANCE_SATS
                )
                for t in txs
            )
            print(
                f"[*] fund poll#{i+1} observed={observed} inbound={inbound_seen} txs={len(txs)}",
                flush=True,
            )
            if observed >= FUND_SATS - TOLERANCE_SATS:
                break

        if observed < FUND_SATS - TOLERANCE_SATS:
            fail(
                f"cold observed_sats not funded in {POLL_SECONDS}s "
                f"(observed={observed} expected>={FUND_SATS - TOLERANCE_SATS})"
            )
        ok(f"cold observed after fund={observed} inbound_history={inbound_seen}")
        if not inbound_seen:
            warn("inbound history row not matched yet (balance gate passed)")

        # External spend: MUST spend the cold address UTXO (plain sendtoaddress
        # may pick other wallet coins and leave the watched outpoint intact).
        drain = bitcoin_cli(
            "-rpcwallet=kerosene",
            "getnewaddress",
            f"smoke-drain-{uuid.uuid4().hex[:6]}",
            "bech32",
        )
        utxo_raw = bitcoin_cli(
            "-rpcwallet=kerosene",
            "listunspent",
            "0",
            "9999999",
            f"'[\"{address}\"]'",
        )
        try:
            utxos = json.loads(utxo_raw)
        except json.JSONDecodeError:
            fail(f"listunspent non-JSON: {utxo_raw[:300]}")
            utxos = []
        if not utxos:
            fail(f"no unspent on cold address {address} after fund")
        utxo = utxos[0]
        funding_txid = str(utxo["txid"])
        funding_vout = int(utxo["vout"])
        # Spend almost all; fee_rate + subtractfeefromoutputs leaves change none.
        send_btc = f"{float(utxo['amount']):.8f}"
        send_sats = int(round(float(utxo["amount"]) * 1e8))
        # Core `send` with explicit inputs (Bitcoin Core 24+).
        send_payload = (
            f"-rpcwallet=kerosene -named send "
            f"outputs='[{{\"{drain}\":{send_btc}}}]' "
            f"fee_rate=2 "
            f"options='{{\"inputs\":[{{\"txid\":\"{funding_txid}\",\"vout\":{funding_vout}}}],"
            f"\"add_inputs\":false,\"subtract_fee_from_outputs\":[0]}}'"
        )
        # bitcoin_cli joins args with spaces into sh -c; pass as single shell string via helper.
        spend_out = bitcoin_cli_shell(send_payload)
        try:
            spend_json = json.loads(spend_out)
            spend_txid = str(spend_json.get("txid") or spend_out).strip()
        except json.JSONDecodeError:
            spend_txid = spend_out.strip().strip('"')
        ok(
            f"external spend txid={spend_txid} amountSats≈{send_sats} "
            f"from={funding_txid}:{funding_vout} dest={drain}"
        )

        after = observed
        outbound_seen = False
        for i in range(max(1, POLL_SECONDS // 5)):
            time.sleep(5)
            after = wallet_observed(user, wallet_id)
            txs = list_transactions(user, wallet_id)
            outbound_seen = any(
                str(t.get("direction", "")).upper() == "OUTBOUND"
                for t in txs
            )
            print(
                f"[*] spend poll#{i+1} observed={after} outbound={outbound_seen}",
                flush=True,
            )
            # Success: balance dropped meaningfully (not still full fund).
            if 0 <= after <= observed - (send_sats // 2):
                break
            if after == 0 and observed > 0:
                # Zero is allowed only if full drain; still accept if outbound seen.
                if outbound_seen:
                    break

        if after < 0:
            fail("could not read observed after external spend")
        if after > observed - min(1000, send_sats // 4):
            fail(
                f"cold observed did not drop after external spend "
                f"(before={observed} after={after} send≈{send_sats})"
            )
        # Must not re-inflate above pre-spend observed (mempool-blind bug).
        if after > observed + TOLERANCE_SATS:
            fail(f"cold observed re-inflated after spend before={observed} after={after}")
        ok(f"cold observed after spend={after} (was {observed}) outbound_history={outbound_seen}")
        if not outbound_seen:
            warn("outbound history not yet indexed (balance drop gate passed)")

        # Final stability check: one more read — no jump back to fund level.
        time.sleep(8)
        final = wallet_observed(user, wallet_id)
        if final > observed + TOLERANCE_SATS:
            fail(f"cold observed re-inflated on final check final={final} preSpend={observed}")
        ok(f"cold final observed={final} (stable vs pre-spend {observed})")

        print("[+] smoke-cold-e2e passed")
        print(f"[+] user={user['username']} wallet={wallet_id}")
        print(f"[+] address={address} fundTx={fund_txid} spendTx={spend_txid}")
    finally:
        if managed:
            stop_port_forwards(pf1, pf2)


if __name__ == "__main__":
    main()
