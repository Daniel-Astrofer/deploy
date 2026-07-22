#!/usr/bin/env bash
# Minimal beta smoke for the kind local-full quorum.
# Usage:
#   export KUBECONFIG=~/.kube/kind-config-kerosene-local
#   bash infra/scripts/beta/smoke-local-full.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
NS="${KEROSENE_NAMESPACE:-kerosene-local}"
SERVER_PORT="${SMOKE_SERVER_PORT:-18080}"
KFE_PORT="${SMOKE_KFE_PORT:-18081}"

fail() { echo "[FAIL] $*" >&2; exit 1; }
ok() { echo "[OK] $*"; }

echo "[*] Kerosene beta smoke (namespace=$NS)"

"$KUBECTL" -n "$NS" get deploy,sts >/dev/null || fail "cannot list workloads"

not_ready=0
while read -r name ready; do
  [[ -z "$name" ]] && continue
  if [[ "$ready" != "1/1" && "$ready" != "True" ]]; then
    echo "[!] not ready: $name ($ready)"
    not_ready=1
  fi
done < <("$KUBECTL" -n "$NS" get pods --no-headers 2>/dev/null | awk '{print $1, $2}')
[[ "$not_ready" -eq 0 ]] || fail "one or more pods are not Ready"

ok "all pods Ready"

"$KUBECTL" -n "$NS" port-forward svc/server "$SERVER_PORT:8080" >/tmp/kerosene-smoke-server-pf.log 2>&1 &
PF1=$!
"$KUBECTL" -n "$NS" port-forward svc/kfe-service "$KFE_PORT:8080" >/tmp/kerosene-smoke-kfe-pf.log 2>&1 &
PF2=$!
cleanup() { kill "$PF1" "$PF2" 2>/dev/null || true; }
trap cleanup EXIT
sleep 2

server_health="$(curl -fsS -m 8 "http://127.0.0.1:${SERVER_PORT}/health/ready")"
echo "$server_health" | grep -q '"status":"UP"' || fail "server health not UP: $server_health"
ok "server /health/ready UP"

kfe_health="$(curl -fsS -m 8 "http://127.0.0.1:${KFE_PORT}/health/ready")"
echo "$kfe_health" | grep -q '"status":"UP"' || fail "kfe health not UP: $kfe_health"
ok "kfe-service /health/ready UP"

pow="$(curl -fsS -m 8 "http://127.0.0.1:${SERVER_PORT}/auth/pow/challenge")"
echo "$pow" | grep -q '"success":true' || fail "pow challenge failed: $pow"
echo "$pow" | grep -q 'challenge' || fail "pow missing challenge"
ok "auth PoW challenge"

onion="$("$KUBECTL" -n "$NS" exec deploy/tor-onion -- sh -c 'cat /var/lib/tor/kerosene_service/hostname' 2>/dev/null || true)"
[[ -n "$onion" ]] || fail "tor onion hostname missing"
ok "tor onion: http://$onion"

btc_chain="$("$KUBECTL" -n "$NS" exec deploy/local-bitcoin -- sh -c \
  '/opt/bitcoin-28.0/bin/bitcoin-cli -datadir=/home/bitcoin/.bitcoin -rpcuser=kerosene -rpcpassword=kerosene-local-rpc getblockchaininfo' \
  2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("chain",""))' 2>/dev/null || true)"
[[ "$btc_chain" == "test" ]] || fail "bitcoin chain is '$btc_chain' (expected test / classic testnet3)"
ok "bitcoin chain test (testnet3)"

echo "[+] smoke-local-full passed"
echo "[+] next: exercise signup/login via app or authenticated API tests"
echo "[+] onion: http://$onion"
