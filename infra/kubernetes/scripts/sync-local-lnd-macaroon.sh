#!/usr/bin/env bash
# Sync LND admin macaroon (hex) into kerosene-lnd-secrets for local-full.
set -euo pipefail
NS="${KEROSENE_NAMESPACE:-kerosene-local}"
POD="$(kubectl -n "$NS" get po -l app=kerosene-lnd -o jsonpath='{.items[0].metadata.name}')"
HEX="$(kubectl -n "$NS" exec "$POD" -c lnd -- sh -c \
  'od -An -tx1 /root/.lnd/data/chain/bitcoin/testnet4/admin.macaroon | tr -d " \n"')"
if [ "${#HEX}" -lt 32 ]; then
  echo "Failed to read admin macaroon hex from $POD" >&2
  exit 1
fi
kubectl -n "$NS" create secret generic kerosene-lnd-secrets \
  --from-literal=admin-macaroon="$HEX" \
  --from-literal=wallet-password="${LND_WALLET_PASSWORD:-kerosene-local-lnd-wallet}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Updated kerosene-lnd-secrets admin-macaroon (${#HEX} hex chars) from $POD"
kubectl -n "$NS" rollout restart deploy/kfe-service
