#!/usr/bin/env bash
# Fund local LND nodes from platform bitcoind (classic testnet) and open a channel
# so KFE can pay/settle Lightning invoices.
#
# Usage:
#   bash infra/kubernetes/scripts/bootstrap-local-lightning-liquidity.sh
#
# Optional env:
#   NS=kerosene-local
#   FUND_MAIN_BTC=0.010      # on-chain to platform LND
#   FUND_PEER_BTC=0.008      # on-chain to peer LND
#   CHANNEL_SATS=500000      # local capacity on openchannel
#   PUSH_SATS=250000         # push to remote for bidirectional liquidity
#   BITCOIN_WALLET=kerosene
set -euo pipefail

NS="${NS:-kerosene-local}"
FUND_MAIN_BTC="${FUND_MAIN_BTC:-0.010}"
FUND_PEER_BTC="${FUND_PEER_BTC:-0.008}"
CHANNEL_SATS="${CHANNEL_SATS:-500000}"
PUSH_SATS="${PUSH_SATS:-250000}"
BITCOIN_WALLET="${BITCOIN_WALLET:-kerosene}"
MIN_CONFS="${MIN_CONFS:-1}"

log() { printf '[ln-bootstrap] %s\n' "$*"; }
die() { printf '[ln-bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }
need_cmd kubectl
need_cmd python3

lncli_main() {
  kubectl -n "$NS" exec deploy/local-lnd -c lnd -- \
    lncli --lnddir=/root/.lnd --network=testnet "$@"
}

lncli_peer() {
  kubectl -n "$NS" exec deploy/local-lnd-peer -c lnd -- \
    lncli --lnddir=/root/.lnd --network=testnet "$@"
}

btc() {
  kubectl -n "$NS" exec deploy/local-bitcoin -- sh -c \
    "bitcoin-cli -testnet -datadir=/home/bitcoin/.bitcoin -rpcport=8332 \
      -rpcuser=\"\$BITCOIN_RPC_USER\" -rpcpassword=\"\$BITCOIN_RPC_PASSWORD\" \
      -rpcwallet=${BITCOIN_WALLET} $*"
}

wait_deploy_ready() {
  local deploy=$1
  log "waiting for $deploy ready..."
  kubectl -n "$NS" rollout status "deploy/$deploy" --timeout=240s
  # containers ready (2/2)
  for _ in $(seq 1 60); do
    ready="$(kubectl -n "$NS" get deploy "$deploy" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
    if [ "${ready:-0}" = "1" ]; then
      return 0
    fi
    sleep 3
  done
  die "$deploy not ready"
}

json_field() {
  local path=$1
  python3 -c 'import json,sys; d=json.load(sys.stdin)
def dig(o,p):
  for k in p.split("."):
    if isinstance(o, dict): o=o.get(k)
    else: return None
  return o
v=dig(d,sys.argv[1]); print("" if v is None else v)' "$path"
}

wait_lnd_balance() {
  local side=$1
  local min_sats=$2
  log "waiting $side confirmed balance >= ${min_sats} sats..."
  for i in $(seq 1 120); do
    if [ "$side" = "main" ]; then
      bal="$(lncli_main walletbalance | json_field confirmed_balance)"
    else
      bal="$(lncli_peer walletbalance | json_field confirmed_balance)"
    fi
    bal="${bal:-0}"
    log "  [$i] $side confirmed_balance=$bal"
    if [ "$bal" -ge "$min_sats" ] 2>/dev/null; then
      return 0
    fi
    sleep 15
  done
  die "timeout waiting for $side on-chain funds (need real testnet confs)"
}

wait_active_channel() {
  log "waiting for active channel..."
  for i in $(seq 1 120); do
    chans="$(lncli_main listchannels)"
    active="$(printf '%s' "$chans" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(1 for c in d.get("channels",[]) if c.get("active")))')"
    pending="$(lncli_main pendingchannels 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("pending_open_channels",[])))' 2>/dev/null || echo 0)"
    log "  [$i] active=$active pending_open=$pending"
    if [ "${active:-0}" -ge 1 ]; then
      printf '%s\n' "$chans"
      return 0
    fi
    sleep 15
  done
  die "timeout waiting for active channel (testnet confirmations)"
}

# --- ensure peer deployment ---
if ! kubectl -n "$NS" get deploy local-lnd-peer >/dev/null 2>&1; then
  die "deploy/local-lnd-peer missing — apply infra/kubernetes/overlays/local-full/local-lnd-peer.yaml first"
fi

wait_deploy_ready local-lnd
wait_deploy_ready local-lnd-peer

log "platform LND info:"
lncli_main getinfo | python3 -c 'import json,sys;d=json.load(sys.stdin);print("  pubkey=",d["identity_pubkey"]);print("  synced=",d.get("synced_to_chain"),"peers=",d.get("num_peers"),"chans=",d.get("num_active_channels"))'
log "peer LND info:"
lncli_peer getinfo | python3 -c 'import json,sys;d=json.load(sys.stdin);print("  pubkey=",d["identity_pubkey"]);print("  synced=",d.get("synced_to_chain"))'

MAIN_PUB="$(lncli_main getinfo | json_field identity_pubkey)"
PEER_PUB="$(lncli_peer getinfo | json_field identity_pubkey)"
PEER_POD_IP="$(kubectl -n "$NS" get po -l app=kerosene-lnd-peer -o jsonpath='{.items[0].status.podIP}')"
MAIN_POD_IP="$(kubectl -n "$NS" get po -l app=kerosene-lnd -o jsonpath='{.items[0].status.podIP}')"
[ -n "$PEER_POD_IP" ] || die "peer pod IP empty"
[ -n "$MAIN_POD_IP" ] || die "main pod IP empty"

# --- fund if needed ---
main_conf="$(lncli_main walletbalance | json_field confirmed_balance)"
main_conf="${main_conf:-0}"
peer_conf="$(lncli_peer walletbalance | json_field confirmed_balance)"
peer_conf="${peer_conf:-0}"
need_main_sats=$((CHANNEL_SATS + 50000))
need_peer_sats=50000

platform_btc="$(btc getbalance 2>/dev/null || echo 0)"
log "platform wallet ${BITCOIN_WALLET} balance=${platform_btc} BTC"
log "main LND confirmed=${main_conf} peer LND confirmed=${peer_conf}"

if [ "$main_conf" -lt "$need_main_sats" ]; then
  MAIN_ADDR="$(lncli_main newaddress p2wkh | json_field address)"
  log "funding main LND ${FUND_MAIN_BTC} BTC -> ${MAIN_ADDR}"
  TXID_MAIN="$(btc sendtoaddress "$MAIN_ADDR" "$FUND_MAIN_BTC")"
  log "main fund txid=${TXID_MAIN}"
else
  log "main LND already funded enough"
fi

if [ "$peer_conf" -lt "$need_peer_sats" ]; then
  PEER_ADDR="$(lncli_peer newaddress p2wkh | json_field address)"
  log "funding peer LND ${FUND_PEER_BTC} BTC -> ${PEER_ADDR}"
  TXID_PEER="$(btc sendtoaddress "$PEER_ADDR" "$FUND_PEER_BTC")"
  log "peer fund txid=${TXID_PEER}"
else
  log "peer LND already funded enough"
fi

wait_lnd_balance main "$need_main_sats"
wait_lnd_balance peer "$need_peer_sats"

# --- connect peers both ways ---
log "connecting main -> peer ${PEER_PUB}@${PEER_POD_IP}:9735"
lncli_main connect "${PEER_PUB}@${PEER_POD_IP}:9735" 2>/dev/null || \
  log "connect main->peer may already exist"
lncli_peer connect "${MAIN_PUB}@${MAIN_POD_IP}:9735" 2>/dev/null || \
  log "connect peer->main may already exist"
sleep 2
lncli_main listpeers | python3 -c 'import json,sys;d=json.load(sys.stdin);print("main peers:", [p.get("pub_key","")[:16] for p in d.get("peers",[])])'

# --- open channel if none ---
active_now="$(lncli_main listchannels | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("channels",[])))')"
if [ "${active_now:-0}" -ge 1 ]; then
  log "channel already exists"
  lncli_main listchannels | python3 -c 'import json,sys; d=json.load(sys.stdin)
for c in d.get("channels",[]):
  print("  point=",c.get("channel_point"),"cap=",c.get("capacity"),"local=",c.get("local_balance"),"remote=",c.get("remote_balance"),"active=",c.get("active"))'
else
  log "opening channel local_amt=${CHANNEL_SATS} push_amt=${PUSH_SATS} min_confs=${MIN_CONFS}"
  OPEN_JSON="$(lncli_main openchannel \
    --node_key="$PEER_PUB" \
    --local_amt="$CHANNEL_SATS" \
    --push_amt="$PUSH_SATS" \
    --min_confs="$MIN_CONFS" \
    --sat_per_vbyte=5 2>&1)" || true
  log "openchannel result: $OPEN_JSON"
  if ! printf '%s' "$OPEN_JSON" | grep -qi 'funding_txid\|channel_point\|txid'; then
    # pending may still work
    log "openchannel response unexpected; checking pending..."
  fi
  wait_active_channel
fi

log "final channel state (main):"
lncli_main listchannels | python3 -c 'import json,sys; d=json.load(sys.stdin)
for c in d.get("channels",[]):
  print("  active=",c.get("active"),"cap=",c.get("capacity"),
        "local=",c.get("local_balance"),"remote=",c.get("remote_balance"),
        "peer=",(c.get("remote_pubkey") or "")[:16])'

# --- E2E pay smoke: peer invoice paid by main ---
log "E2E: peer creates invoice, main pays..."
INV="$(lncli_peer addinvoice --amt=1000 --memo='bootstrap-e2e')"
BOLT11="$(printf '%s' "$INV" | json_field payment_request)"
RHASH="$(printf '%s' "$INV" | json_field r_hash)"
[ -n "$BOLT11" ] || die "empty bolt11 from peer"
log "invoice r_hash=${RHASH}"
PAY="$(lncli_main payinvoice --force --fee_limit=1000 "$BOLT11" 2>&1)" || true
log "pay result: $(printf '%s' "$PAY" | head -c 400)"
if printf '%s' "$PAY" | grep -qiE 'SUCCEEDED|status.*SUCCEEDED|"status": "SUCCEEDED"'; then
  log "SUCCESS: payment settled"
else
  # lncli payinvoice streams; also check lookup
  sleep 3
  LOOKUP="$(lncli_main lookupinvoice "$RHASH" 2>&1 || lncli_peer lookupinvoice "$RHASH" 2>&1 || true)"
  log "lookup: $(printf '%s' "$LOOKUP" | head -c 300)"
  if printf '%s' "$LOOKUP" | grep -qi 'SETTLED'; then
    log "SUCCESS: invoice SETTLED"
  else
    log "WARN: payment may still be in-flight or failed — inspect above"
  fi
fi

# reverse direction: main invoice paid by peer (inbound to platform)
log "E2E reverse: main creates invoice, peer pays (inbound to platform)..."
INV2="$(lncli_main addinvoice --amt=500 --memo='bootstrap-inbound')"
BOLT11_2="$(printf '%s' "$INV2" | json_field payment_request)"
RHASH2="$(printf '%s' "$INV2" | json_field r_hash)"
PAY2="$(lncli_peer payinvoice --force --fee_limit=1000 "$BOLT11_2" 2>&1)" || true
log "inbound pay: $(printf '%s' "$PAY2" | head -c 400)"
if printf '%s' "$PAY2" | grep -qiE 'SUCCEEDED|SETTLED'; then
  log "SUCCESS: inbound payment settled"
else
  sleep 3
  LOOKUP2="$(lncli_main lookupinvoice "$RHASH2" 2>&1 || true)"
  if printf '%s' "$LOOKUP2" | grep -qi 'SETTLED'; then
    log "SUCCESS: inbound invoice SETTLED"
  else
    log "WARN: inbound payment not confirmed yet"
  fi
fi

log "Done. Platform LND can now route/pay within the local channel topology."
log "KFE outbound uses LND REST on kerosene-lnd-headless; ensure macaroon secret is synced:"
log "  ./infra/kubernetes/scripts/sync-local-lnd-macaroon.sh"
