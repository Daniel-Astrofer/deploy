#!/bin/sh
set -eu

network="${BITCOIN_NETWORK:-mainnet}"
lnd_dir="${LND_DATA_DIR:-/root/.lnd}"
grpc_port="${LND_GRPC_PORT:-10009}"
rest_port="${LND_REST_PORT:-8080}"
listen_port="${LND_LISTEN_PORT:-9735}"
alias_name="${LND_ALIAS:-kerosene-lnd-bitcoind}"
color="${LND_COLOR:-#D1495B}"
bitcoin_node="${LND_BITCOIN_NODE:-bitcoind}"
bitcoind_rpc_host="${BITCOIN_RPC_HOST:-bitcoin-pruned-node}"
bitcoind_rpc_port="${BITCOIN_RPC_PORT:-8332}"
bitcoind_rpc_user="${BITCOIN_RPC_USER:-}"
bitcoind_rpc_password="${BITCOIN_RPC_PASSWORD:-}"
bitcoind_zmq_rawtx="${BITCOIN_ZMQ_RAWTX:-tcp://bitcoin-pruned-node:28332}"
bitcoind_zmq_rawblock="${BITCOIN_ZMQ_RAWBLOCK:-tcp://bitcoin-pruned-node:28334}"
connect_peer="${LND_NEUTRINO_CONNECT:-}"
add_peers="${LND_NEUTRINO_ADDPEERS:-}"
external_ip="${LND_EXTERNAL_IP:-}"
tls_extra_domains="${LND_TLS_EXTRA_DOMAINS:-lnd-bitcoind,lnd-neutrino,localhost}"
tls_extra_ips="${LND_TLS_EXTRA_IPS:-127.0.0.1}"

mkdir -p "$lnd_dir"
tls_san_marker="${lnd_dir}/.kerosene-tls-san"
tls_san_config="domains=${tls_extra_domains};ips=${tls_extra_ips}"
if [ -f "${lnd_dir}/tls.cert" ] || [ -f "${lnd_dir}/tls.key" ]; then
  previous_tls_san_config="$(cat "$tls_san_marker" 2>/dev/null || true)"
  if [ "$previous_tls_san_config" != "$tls_san_config" ]; then
    rm -f "${lnd_dir}/tls.cert" "${lnd_dir}/tls.key"
  fi
fi
printf '%s\n' "$tls_san_config" > "$tls_san_marker"

set -- \
  lnd \
  "--lnddir=${lnd_dir}" \
  "--rpclisten=0.0.0.0:${grpc_port}" \
  "--restlisten=0.0.0.0:${rest_port}" \
  "--listen=0.0.0.0:${listen_port}" \
  "--bitcoin.active" \
  "--alias=${alias_name}" \
  "--color=${color}"

case "$network" in
  mainnet)
    set -- "$@" "--bitcoin.mainnet"
    ;;
  testnet|testnet3)
    set -- "$@" "--bitcoin.testnet"
    ;;
  testnet4)
    set -- "$@" "--bitcoin.testnet4"
    ;;
  signet)
    set -- "$@" "--bitcoin.signet"
    ;;
  regtest)
    set -- "$@" "--bitcoin.regtest"
    ;;
  *)
    echo "Unsupported BITCOIN_NETWORK: $network" >&2
    exit 1
    ;;
esac

if [ -n "$external_ip" ]; then
  set -- "$@" "--externalip=${external_ip}"
fi

case "$bitcoin_node" in
  bitcoind)
    if [ -z "$bitcoind_rpc_user" ] || [ -z "$bitcoind_rpc_password" ]; then
      echo "BITCOIN_RPC_USER and BITCOIN_RPC_PASSWORD are required when LND_BITCOIN_NODE=bitcoind." >&2
      exit 1
    fi
    set -- "$@" \
      "--bitcoin.node=bitcoind" \
      "--bitcoind.rpchost=${bitcoind_rpc_host}:${bitcoind_rpc_port}" \
      "--bitcoind.rpcuser=${bitcoind_rpc_user}" \
      "--bitcoind.rpcpass=${bitcoind_rpc_password}" \
      "--bitcoind.zmqpubrawtx=${bitcoind_zmq_rawtx}" \
      "--bitcoind.zmqpubrawblock=${bitcoind_zmq_rawblock}"
    ;;
  neutrino)
    set -- "$@" "--bitcoin.node=neutrino"
    ;;
  *)
    echo "Unsupported LND_BITCOIN_NODE: $bitcoin_node" >&2
    exit 1
    ;;
esac

old_ifs=$IFS
IFS=','
for domain in $tls_extra_domains; do
  if [ -n "$domain" ]; then
    set -- "$@" "--tlsextradomain=${domain}"
  fi
done
for ip in $tls_extra_ips; do
  if [ -n "$ip" ]; then
    set -- "$@" "--tlsextraip=${ip}"
  fi
done
IFS=$old_ifs

if [ "$bitcoin_node" = "neutrino" ] && [ -n "$connect_peer" ]; then
  set -- "$@" "--neutrino.connect=${connect_peer}"
fi

IFS=','
for peer in $add_peers; do
  if [ "$bitcoin_node" = "neutrino" ] && [ -n "$peer" ]; then
    set -- "$@" "--neutrino.addpeer=${peer}"
  fi
done
IFS=$old_ifs

exec "$@"
