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

echo "[PASS] canonical staging runtime and fail-fast gates"
