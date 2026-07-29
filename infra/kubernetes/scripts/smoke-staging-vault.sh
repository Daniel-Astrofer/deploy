#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
NAMESPACE="${KEROSENE_STAGING_VAULT_NAMESPACE:-kerosene-staging-vault}"

"$KUBECTL" -n "$NAMESPACE" exec deployment/vault -- \
  sh -c 'wget -qO- --no-check-certificate https://127.0.0.1:7801/v1/health' |
  grep -Eq '"status":"(starting|ready)"'

# A single Vault is locally healthy but must not pretend a configured 2-of-3
# financial quorum exists.
if "$KUBECTL" -n "$NAMESPACE" exec deployment/vault -- \
    sh -c 'wget -qO- --no-check-certificate https://127.0.0.1:7801/v1/health' |
    grep -Eq '"peer_count":[[:space:]]*[2-9]'; then
  echo "[!] Fresh single-Vault bootstrap unexpectedly reports quorum peers." >&2
  exit 1
fi

echo "[+] Independent Vault is locally healthy and limited without quorum."
