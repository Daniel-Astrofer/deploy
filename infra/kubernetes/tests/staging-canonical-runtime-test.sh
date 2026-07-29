#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VALIDATOR="$REPO_ROOT/infra/kubernetes/scripts/validate-staging-runtime.sh"
DEPLOY="$REPO_ROOT/infra/kubernetes/scripts/deploy.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  find "$TMP_DIR" -type f -exec unlink {} \;
  rmdir "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

manifest="$TMP_DIR/staging.yaml"
kubectl kustomize "$REPO_ROOT/infra/kubernetes/overlays/staging" > "$manifest"
bash "$VALIDATOR" "$manifest" >/dev/null

vault_manifest="$TMP_DIR/staging-vault.yaml"
kubectl kustomize "$REPO_ROOT/infra/kubernetes/overlays/staging-vault" > "$vault_manifest"
grep -q '^  name: kerosene-staging-vault$' "$vault_manifest" \
  || fail "independent Vault namespace is missing"
grep -A1 'name: VAULT_TRANSPORT' "$vault_manifest" | grep -q 'value: tor' \
  || fail "independent Vault is not Tor-only"
grep -q 'HiddenServicePort 7801 vault:7801' "$vault_manifest" \
  || fail "Vault onion service is not published through Tor"
grep -q 'HiddenServicePort 8800 127.0.0.1:8800' "$vault_manifest" \
  || fail "Vault-plane Node is not published through Tor"
grep -q 'image: kerosene/node:staging' "$vault_manifest" \
  || fail "Vault-plane Node image is missing"
grep -A1 'name: KEROSENE_DISCOVERY_PLANE' "$vault_manifest" | grep -q 'value: vault' \
  || fail "independent Node is not bound to the Vault plane"
grep -A1 'name: VAULT_KEROSENE_NODE_URL' "$vault_manifest" \
  | grep -q 'https://tor.kerosene-staging-vault.svc:8800' \
  || fail "Vault does not use its local mTLS Node directory"
grep -q 'secretName: kerosene-node-identity' "$vault_manifest" \
  || fail "Vault-plane Node identity is not independently provisioned"
if grep -Eq 'value: clearnet|https://vault-[123]:' "$vault_manifest"; then
  fail "independent Vault manifest contains a clearnet mesh route"
fi

for workload in staging-postgres staging-redis staging-bitcoin staging-lnd staging-tor; do
  grep -q "^  name: ${workload}$" "$manifest" || fail "missing ${workload}"
done

cp "$manifest" "$TMP_DIR/cross-namespace.yaml"
printf '\n# forbidden dependency: kerosene-local\n' >> "$TMP_DIR/cross-namespace.yaml"
if bash "$VALIDATOR" "$TMP_DIR/cross-namespace.yaml" >/dev/null 2>&1; then
  fail "validator accepted a kerosene-local reference"
fi

set +e
output="$(bash "$DEPLOY" staging --dry-run 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]] || fail "staging deploy without immutable images returned ${status}"
grep -q 'requires immutable SERVER_IMAGE' <<<"$output" \
  || fail "staging deploy did not explain immutable image requirement"

grep -q 'smoke-staging.sh' "$DEPLOY" || fail "staging deploy does not execute smoke gates"
grep -q 'statefulset/staging-postgres' "$DEPLOY" || fail "staging deploy does not wait for dependencies"
grep -q 'port-forward deployment/vault' "$REPO_ROOT/infra/kubernetes/scripts/smoke-staging-vault.sh" \
  || fail "Vault smoke does not support the shell-free runtime image"

echo "[PASS] canonical staging runtime and fail-fast gates"
