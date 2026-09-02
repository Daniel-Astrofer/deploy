#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VALIDATOR="$REPO_ROOT/infra/kubernetes/scripts/validate-staging-spire.sh"

bash "$VALIDATOR" >/dev/null

if KEROSENE_SPIRE_TARGET=production bash "$VALIDATOR" >/dev/null 2>&1; then
  echo "[FAIL] staging SPIRE manifests were accepted for production" >&2
  exit 1
fi

set +e
output="$(bash "$REPO_ROOT/infra/kubernetes/scripts/deploy.sh" staging-spiffe --dry-run 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]] \
  || { echo "[FAIL] staging-spiffe without immutable images returned $status" >&2; exit 1; }
grep -q 'requires immutable SERVER_IMAGE' <<<"$output" \
  || { echo "[FAIL] staging-spiffe did not require immutable Core images" >&2; exit 1; }

set +e
output="$(bash "$REPO_ROOT/infra/kubernetes/scripts/deploy.sh" staging-vault-spiffe --dry-run 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]] \
  || { echo "[FAIL] staging-vault-spiffe without immutable images returned $status" >&2; exit 1; }
grep -q 'requires immutable VAULT_IMAGE' <<<"$output" \
  || { echo "[FAIL] staging-vault-spiffe did not require immutable Vault images" >&2; exit 1; }

grep -q 'preflight-staging-spire.sh' "$REPO_ROOT/infra/kubernetes/scripts/deploy.sh" \
  || { echo "[FAIL] SPIFFE deploy profiles do not call the fail-closed preflight" >&2; exit 1; }
grep -q -- '--as="$GITOPS_USER"' "$REPO_ROOT/infra/kubernetes/scripts/deploy.sh" \
  || { echo "[FAIL] SPIFFE manifests are not applied through the dedicated GitOps identity" >&2; exit 1; }

set +e
output="$(bash "$REPO_ROOT/infra/kubernetes/scripts/install-staging-spire.sh" 2>&1)"
status=$?
set -e
[[ "$status" -eq 1 ]] \
  || { echo "[FAIL] SPIRE installer without immutable images returned $status" >&2; exit 1; }
grep -q 'SERVER_IMAGE must be an immutable image reference' <<<"$output" \
  || { echo "[FAIL] SPIRE installer did not fail before cluster mutation when images were absent" >&2; exit 1; }

echo "[PASS] SPIRE staging identity manifests and production refusal"
