#!/bin/sh
set -eu

bitcoin_dir="${BITCOIN_DATA_DIR:-/home/bitcoin/.bitcoin}"
conf="$bitcoin_dir/bitcoin.conf"
expected_chain="${BITCOIN_CHAIN:-mainnet}"

chain_info="$(bitcoin-cli -conf="$conf" getblockchaininfo 2>&1)" || {
  echo "Bitcoin node RPC is not ready: $chain_info" >&2
  exit 1
}

network_info="$(bitcoin-cli -conf="$conf" getnetworkinfo 2>&1)" || {
  echo "Bitcoin node network info is not ready: $network_info" >&2
  exit 1
}

printf '%s\n' "$chain_info" | grep -q '"pruned": true' || {
  echo "Bitcoin node is reachable but prune mode is not active." >&2
  exit 1
}

case "$expected_chain" in
  mainnet|main)
    printf '%s\n' "$chain_info" | grep -q '"chain": "main"' || {
      echo "Bitcoin node is not on mainnet." >&2
      exit 1
    }
    ;;
  testnet|testnet3)
    printf '%s\n' "$chain_info" | grep -q '"chain": "test"' || {
      echo "Bitcoin node is not on testnet." >&2
      exit 1
    }
    ;;
  testnet4)
    printf '%s\n' "$chain_info" | grep -q '"chain": "testnet4"' || {
      echo "Bitcoin node is not on testnet4." >&2
      exit 1
    }
    ;;
  signet)
    printf '%s\n' "$chain_info" | grep -q '"chain": "signet"' || {
      echo "Bitcoin node is not on signet." >&2
      exit 1
    }
    ;;
  regtest)
    printf '%s\n' "$chain_info" | grep -q '"chain": "regtest"' || {
      echo "Bitcoin node is not on regtest." >&2
      exit 1
    }
    ;;
  *)
    echo "Unsupported BITCOIN_CHAIN: $expected_chain" >&2
    exit 1
    ;;
esac

printf '%s\n' "$network_info" | grep -q '"networkactive": true' || {
  echo "Bitcoin node networking is disabled." >&2
  exit 1
}
