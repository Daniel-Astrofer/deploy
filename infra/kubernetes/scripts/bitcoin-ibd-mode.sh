#!/usr/bin/env bash
# Temporarily free RAM/CPU for Bitcoin Core IBD, then restore the normal local-full stack.
#
# Usage:
#   bash infra/kubernetes/scripts/bitcoin-ibd-mode.sh boost    # scale down apps, boost bitcoind
#   bash infra/kubernetes/scripts/bitcoin-ibd-mode.sh restore  # restore apps + modest bitcoind limits
#   bash infra/kubernetes/scripts/bitcoin-ibd-mode.sh status
set -euo pipefail

NS="${NS:-kerosene-local}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
STATE_FILE="${STATE_FILE:-$ROOT/.local/bitcoin-ibd-mode-state}"
KUBECTL="${KUBECTL:-kubectl}"

# App / infra pods paused during IBD (keep bitcoin + both LND nodes).
PAUSE_DEPLOYS=(
  server
  kfe-service
  web-page
  tor-onion
  local-redis
)
PAUSE_STS=(
  local-postgres
)

log() { printf '[bitcoin-ibd] %s\n' "$*"; }
die() { printf '[bitcoin-ibd] ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }
need_cmd "$KUBECTL"

scale_deploy() {
  local name=$1 replicas=$2
  if $KUBECTL -n "$NS" get deploy "$name" >/dev/null 2>&1; then
    log "scale deploy/$name -> $replicas"
    $KUBECTL -n "$NS" scale "deploy/$name" --replicas="$replicas"
  else
    log "skip missing deploy/$name"
  fi
}

scale_sts() {
  local name=$1 replicas=$2
  if $KUBECTL -n "$NS" get sts "$name" >/dev/null 2>&1; then
    log "scale sts/$name -> $replicas"
    $KUBECTL -n "$NS" scale "sts/$name" --replicas="$replicas"
  else
    log "skip missing sts/$name"
  fi
}

patch_bitcoin_resources() {
  local cpu_req=$1 mem_req=$2 cpu_lim=$3 mem_lim=$4
  log "bitcoin resources req=${cpu_req}/${mem_req} lim=${cpu_lim}/${mem_lim}"
  $KUBECTL -n "$NS" patch deploy local-bitcoin --type strategic -p "{
    \"spec\": {
      \"template\": {
        \"spec\": {
          \"containers\": [{
            \"name\": \"bitcoind\",
            \"resources\": {
              \"requests\": {\"cpu\": \"${cpu_req}\", \"memory\": \"${mem_req}\"},
              \"limits\": {\"cpu\": \"${cpu_lim}\", \"memory\": \"${mem_lim}\"}
            }
          }]
        }
      }
    }
  }"
}

# Replace IBD speed knobs inside the bitcoind launch script without rewriting the whole deploy.
# Steady-state has no -dbcache/-blocksonly/-par; boost injects them after -txindex=0.
set_bitcoin_ibd_knobs() {
  local mode=$1 # boost|steady
  local tmp
  tmp="$(mktemp)"
  $KUBECTL -n "$NS" get deploy local-bitcoin -o json >"$tmp"
  python3 - "$tmp" "$mode" <<'PY'
import json, sys, re
path, mode = sys.argv[1], sys.argv[2]
with open(path) as f:
    d = json.load(f)
args = d["spec"]["template"]["spec"]["containers"][0]["args"][0]

# Strip previous IBD knobs (idempotent).
args = re.sub(r"\n[ \t]*-dbcache=\d+[ \t]*\\\n", "\n", args)
args = re.sub(r"\n[ \t]*-blocksonly=1[ \t]*\\\n", "\n", args)
args = re.sub(r"\n[ \t]*-par=\d+[ \t]*\\\n", "\n", args)
args = re.sub(r"\n[ \t]*-maxconnections=\d+[ \t]*\\\n", "\n", args)

if mode == "boost":
    m = re.search(r"(?m)^([ \t]*)-txindex=0[ \t]*\\[ \t]*$", args)
    if not m:
        raise SystemExit("cannot find -txindex=0 anchor in bitcoin args")
    indent = m.group(1)
    insert = (
        f"{indent}-txindex=0 \\\n"
        f"{indent}-dbcache=512 \\\n"
        f"{indent}-blocksonly=1 \\\n"
        f"{indent}-par=10 \\\n"
        f"{indent}-maxconnections=64 \\\n"
    )
    args = args[: m.start()] + insert + args[m.end() + 1 :]  # +1 skip newline after match line
elif mode != "steady":
    raise SystemExit(f"unknown mode {mode}")

d["spec"]["template"]["spec"]["containers"][0]["args"][0] = args
with open(path, "w") as f:
    json.dump(d, f)
print(f"knobs={mode}")
PY
  $KUBECTL -n "$NS" replace -f "$tmp"
  rm -f "$tmp"
}

cmd_boost() {
  mkdir -p "$(dirname "$STATE_FILE")"
  {
    echo "paused_at=$(date -Iseconds)"
    echo "pause_deploys=${PAUSE_DEPLOYS[*]}"
    echo "pause_sts=${PAUSE_STS[*]}"
  } >"$STATE_FILE"

  for d in "${PAUSE_DEPLOYS[@]}"; do scale_deploy "$d" 0; done
  for s in "${PAUSE_STS[@]}"; do scale_sts "$s" 0; done

  # Give kube a moment to free memory before raising bitcoin.
  sleep 5
  # Avoid dual pods fighting the RWO PVC / datadir lock during boost restarts.
  $KUBECTL -n "$NS" patch deploy local-bitcoin --type json -p \
    '[{"op":"replace","path":"/spec/strategy","value":{"type":"Recreate"}}]' \
    >/dev/null
  set_bitcoin_ibd_knobs boost
  # Realistic on a 16Gi workstation that still runs IDE/browser.
  # Raise further only if `free -h` shows >8Gi available after pause.
  patch_bitcoin_resources "1" "2Gi" "4" "3500Mi"
  $KUBECTL -n "$NS" rollout status deploy/local-bitcoin --timeout=300s || true
  log "boost active. When IBD finishes: bash infra/kubernetes/scripts/bitcoin-ibd-mode.sh restore"
  cmd_status
}

cmd_restore() {
  # Steady-state resources (pre-boost local-full defaults).
  set_bitcoin_ibd_knobs steady
  patch_bitcoin_resources "100m" "256Mi" "1000m" "1Gi"

  for d in "${PAUSE_DEPLOYS[@]}"; do scale_deploy "$d" 1; done
  for s in "${PAUSE_STS[@]}"; do scale_sts "$s" 1; done

  $KUBECTL -n "$NS" rollout status deploy/local-bitcoin --timeout=300s || true
  rm -f "$STATE_FILE"
  log "restored apps + modest bitcoin limits"
  cmd_status
}

cmd_status() {
  echo "=== memory ==="
  free -h | head -2 || true
  echo "=== bitcoin ==="
  $KUBECTL -n "$NS" get deploy local-bitcoin -o jsonpath='resources limits cpu={.spec.template.spec.containers[0].resources.limits.cpu} mem={.spec.template.spec.containers[0].resources.limits.memory}{"\n"}' 2>/dev/null || true
  $KUBECTL -n "$NS" get pods -l 'app.kubernetes.io/name in (bitcoin-core,lnd,lnd-peer)' -o wide 2>/dev/null || true
  if $KUBECTL -n "$NS" get deploy local-bitcoin >/dev/null 2>&1; then
    $KUBECTL -n "$NS" exec deploy/local-bitcoin -- sh -c \
      'bitcoin-cli -testnet -datadir=/home/bitcoin/.bitcoin -rpcport=8332 -rpcuser="$BITCOIN_RPC_USER" -rpcpassword="$BITCOIN_RPC_PASSWORD" getblockchaininfo' \
      2>/dev/null | python3 -c 'import sys,json
try:
 d=json.load(sys.stdin)
 print("chain",d.get("chain"),"blocks",d.get("blocks"),"headers",d.get("headers"),
       "progress",f"{(d.get("verificationprogress") or 0)*100:.2f}%",
       "ibd",d.get("initialblockdownload"),"pruned",d.get("pruned"),
       "disk_gb",round((d.get("size_on_disk") or 0)/1e9,2))
except Exception as e:
 print("rpc unavailable:", e)' || true
  fi
  echo "=== paused apps (replicas) ==="
  for d in "${PAUSE_DEPLOYS[@]}"; do
    r=$($KUBECTL -n "$NS" get deploy "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo missing)
    echo "  deploy/$d=$r"
  done
  for s in "${PAUSE_STS[@]}"; do
    r=$($KUBECTL -n "$NS" get sts "$s" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo missing)
    echo "  sts/$s=$r"
  done
  if [ -f "$STATE_FILE" ]; then
    echo "=== state file ==="
    cat "$STATE_FILE"
  fi
}

case "${1:-}" in
  boost) cmd_boost ;;
  restore) cmd_restore ;;
  status) cmd_status ;;
  *)
    cat <<EOF
Usage: $0 boost|restore|status
EOF
    exit 2
    ;;
esac
