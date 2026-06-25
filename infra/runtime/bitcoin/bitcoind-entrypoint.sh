#!/bin/sh
set -eu

bitcoin_dir="${BITCOIN_DATA_DIR:-/home/bitcoin/.bitcoin}"
chain="${BITCOIN_CHAIN:-mainnet}"
wallet="${BITCOIN_RPC_WALLET:-kerosene}"
prune_mb="${BITCOIN_PRUNE_MB:-5500}"
rpc_port="${BITCOIN_RPC_PORT:-8332}"
p2p_port="${BITCOIN_P2P_PORT:-8333}"
wallet_passphrase="${BITCOIN_WALLET_PASSPHRASE:-}"
max_mempool_mb="${BITCOIN_MAX_MEMPOOL_MB:-300}"
dbcache_mb="${BITCOIN_DBCACHE_MB:-1024}"
reindex_chainstate_once="${BITCOIN_REINDEX_CHAINSTATE_ONCE:-false}"
reindex_once="${BITCOIN_REINDEX_ONCE:-false}"

case "$prune_mb" in
  ''|*[!0-9]*)
    echo "BITCOIN_PRUNE_MB must be a numeric value in MiB." >&2
    exit 1
    ;;
esac

if [ "$prune_mb" -lt 550 ]; then
  echo "BITCOIN_PRUNE_MB must be at least 550 MiB for Bitcoin Core prune mode." >&2
  exit 1
fi

mkdir -p "$bitcoin_dir"
cat > "$bitcoin_dir/bitcoin.conf" <<EOF
server=1
printtoconsole=1
listen=1
prune=${prune_mb}
txindex=0
blockfilterindex=0
coinstatsindex=0
blocksonly=0
maxmempool=${max_mempool_mb}
dbcache=${dbcache_mb}
fallbackfee=0.0002
rpcallowip=0.0.0.0/0
rpcuser=${BITCOIN_RPC_USER}
rpcpassword=${BITCOIN_RPC_PASSWORD}
zmqpubrawtx=tcp://0.0.0.0:28332
zmqpubhashblock=tcp://0.0.0.0:28333
zmqpubrawblock=tcp://0.0.0.0:28334
EOF

case "$chain" in
  mainnet|main)
    ;;
  testnet|testnet3)
    printf '%s\n' "testnet=1" >> "$bitcoin_dir/bitcoin.conf"
    ;;
  testnet4)
    printf '%s\n' "testnet4=1" >> "$bitcoin_dir/bitcoin.conf"
    ;;
  signet)
    printf '%s\n' "signet=1" >> "$bitcoin_dir/bitcoin.conf"
    ;;
  regtest)
    cat >> "$bitcoin_dir/bitcoin.conf" <<EOF
regtest=1
[regtest]
port=${p2p_port}
rpcbind=0.0.0.0
rpcport=${rpc_port}
wallet=${wallet}
EOF
    ;;
  *)
    echo "Unsupported BITCOIN_CHAIN: $chain" >&2
    exit 1
    ;;
esac

bitcoind_args="-conf=$bitcoin_dir/bitcoin.conf -datadir=$bitcoin_dir"

if [ "$reindex_chainstate_once" = "true" ] && [ ! -f "$bitcoin_dir/.kerosene-reindex-chainstate-complete" ]; then
  echo "BITCOIN_REINDEX_CHAINSTATE_ONCE=true: starting Bitcoin Core with -reindex-chainstate."
  bitcoind_args="$bitcoind_args -reindex-chainstate"
fi

if [ "$reindex_once" = "true" ] && [ ! -f "$bitcoin_dir/.kerosene-reindex-complete" ]; then
  echo "BITCOIN_REINDEX_ONCE=true: starting Bitcoin Core with -reindex."
  bitcoind_args="$bitcoind_args -reindex"
fi

# shellcheck disable=SC2086
bitcoind $bitcoind_args &
pid="$!"

stop_bitcoind() {
  bitcoin-cli -conf="$bitcoin_dir/bitcoin.conf" stop >/dev/null 2>&1 || true
  wait "$pid" || true
}
trap stop_bitcoind INT TERM

bitcoin-cli -conf="$bitcoin_dir/bitcoin.conf" -rpcwait getblockchaininfo >/dev/null

if [ "$reindex_chainstate_once" = "true" ]; then
  touch "$bitcoin_dir/.kerosene-reindex-chainstate-complete"
fi

if [ "$reindex_once" = "true" ]; then
  touch "$bitcoin_dir/.kerosene-reindex-complete"
fi

if ! bitcoin-cli -conf="$bitcoin_dir/bitcoin.conf" listwallets | grep -F "\"$wallet\"" >/dev/null 2>&1; then
  if bitcoin-cli -conf="$bitcoin_dir/bitcoin.conf" listwalletdir | grep -F "\"name\": \"$wallet\"" >/dev/null 2>&1; then
    bitcoin-cli -conf="$bitcoin_dir/bitcoin.conf" loadwallet "$wallet" >/dev/null
  else
    bitcoin-cli -conf="$bitcoin_dir/bitcoin.conf" createwallet "$wallet" false false "$wallet_passphrase" false true true >/dev/null
  fi
fi

wait "$pid"
