#!/usr/bin/env bash
# Lightweight smoke against a running KFE + LND (testnet/regtest).
# Usage:
#   export KFE_BASE_URL=http://127.0.0.1:8080
#   export USER_JWT=...
#   export ADMIN_JWT=...
#   export WALLET_ID=...
#   bash infra/scripts/beta/kfe-lightning-e2e-smoke.sh
set -euo pipefail

KFE_BASE_URL="${KFE_BASE_URL:-http://127.0.0.1:8080}"
USER_JWT="${USER_JWT:-}"
ADMIN_JWT="${ADMIN_JWT:-}"
WALLET_ID="${WALLET_ID:-}"
AMOUNT_SATS="${AMOUNT_SATS:-1000}"

need() {
  if [[ -z "${!1:-}" ]]; then
    echo "Missing env: $1" >&2
    exit 1
  fi
}

need USER_JWT
need ADMIN_JWT
need WALLET_ID

echo "== health =="
curl -fsS "$KFE_BASE_URL/health/ready" | head -c 400
echo

echo "== admin channels =="
curl -fsS -H "Authorization: Bearer $ADMIN_JWT" \
  "$KFE_BASE_URL/api/admin/kfe/channels" | head -c 800
echo

echo "== create LIGHTNING payment request =="
PR_JSON=$(curl -fsS -X POST \
  -H "Authorization: Bearer $USER_JWT" \
  -H "Content-Type: application/json" \
  "$KFE_BASE_URL/kfe/payment-requests" \
  -d "{\"walletId\":\"$WALLET_ID\",\"rail\":\"LIGHTNING\",\"amountSats\":$AMOUNT_SATS,\"memo\":\"e2e-smoke\"}")
echo "$PR_JSON" | head -c 1200
echo

if ! echo "$PR_JSON" | grep -qi 'paymentRequest\|payment_request\|ln'; then
  echo "WARN: payment request body may lack bolt11 (gateway not live?)" >&2
fi

echo "== rebalance jobs =="
curl -fsS -H "Authorization: Bearer $ADMIN_JWT" \
  "$KFE_BASE_URL/api/admin/kfe/channels/rebalance/jobs?limit=10" | head -c 800
echo

echo "== process rebalance worker batch =="
curl -fsS -X POST -H "Authorization: Bearer $ADMIN_JWT" \
  "$KFE_BASE_URL/api/admin/kfe/channels/rebalance/jobs/process?limit=3" | head -c 400
echo

echo "OK: smoke requests completed (pay invoice externally; wait for LN PR monitor)."
echo "Full checklist: docs/kfe/RUNBOOK_KFE_LIGHTNING_E2E_TESTNET.md"
