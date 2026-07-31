#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
manifest="${1:-}"
created_manifest=0

cleanup() {
  if [[ "$created_manifest" -eq 1 && -f "$manifest" ]]; then
    unlink "$manifest"
  fi
}
trap cleanup EXIT

if [[ -z "$manifest" ]]; then
  manifest="$(mktemp "${TMPDIR:-/tmp}/kerosene-staging.XXXXXX.yaml")"
  created_manifest=1
  "$KUBECTL" kustomize "$K8S_ROOT/overlays/staging" > "$manifest"
fi

require() {
  local pattern="$1"
  local message="$2"
  grep -Eq "$pattern" "$manifest" || {
    echo "[!] Staging manifest gate failed: $message" >&2
    exit 1
  }
}

for workload in staging-postgres staging-redis staging-bitcoin staging-lnd staging-tor; do
  require "^  name: ${workload}$" "missing staging-owned workload ${workload}"
done

for claim in data-staging-postgres-0 data-staging-redis-0 data-staging-bitcoin-0 data-staging-lnd-0 data-staging-tor-0; do
  # StatefulSet claim names are generated at runtime; the rendered template must
  # still expose a named data volume for each owner.
  owner="${claim#data-}"
  owner="${owner%-0}"
  grep -A180 -E "^  name: ${owner}$" "$manifest" | grep -q '^      name: data$' || {
    echo "[!] Staging manifest gate failed: ${owner} has no data claim template" >&2
    exit 1
  }
done

require '(value: testnet3|BITCOIN_NETWORK: testnet3)' "Bitcoin network is not testnet3"
require 'HiddenServicePort 80 web-page:8080' "staging onion does not publish the web gateway"
require 'HiddenServicePort 8800 127.0.0.1:8800' "Bank-plane Node is not onion-published"
require '^  name: kerosene-staging$' "staging namespace is missing"
require 'image:.*node' "Kerosene Node image is missing"
require 'name: KEROSENE_DISCOVERY_PLANE' "Node discovery plane is not configured"
require 'value: bank' "Core Node is not bound to the Bank plane"
require 'name: KEROSENE_NODE_ONION_HOSTNAME_PATH' "Node does not consume the Tor hostname"
require 'name: KFE_VAULTMESH_TRANSPORT' "KFE Vault transport mode is missing"
require 'name: KFE_KEROSENE_NODE_TRANSPORT' "KFE Node transport mode is missing"
require 'value: tor' "Tor-only transport is not enabled"

if grep -Eq 'KFE_VAULTMESH_(BASE_URL|URLS).*(vault-[123]|\\.svc)|https://vault-[123]:' "$manifest"; then
  echo "[!] Staging Core contains a clearnet Kubernetes Vault endpoint." >&2
  exit 1
fi

if grep -Eq 'KFE_KEROSENE_NODE_BASE_URL.*(vault-[123]|\\.svc)|https://vault-[123]:' "$manifest"; then
  echo "[!] Staging Core contains a clearnet Kubernetes Node endpoint." >&2
  exit 1
fi

if grep -Eq '^  name: vault-[123]$' "$manifest"; then
  echo "[!] Staging Core embeds a Vault deployment; use staging-vault independently." >&2
  exit 1
fi

if grep -Eq 'kerosene-local(-ha)?|namespace:[[:space:]]+kerosene-(local|production)' "$manifest"; then
  echo "[!] Staging manifest references a non-staging Kerosene runtime." >&2
  exit 1
fi

echo "[+] Canonical staging runtime validation passed."
