#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVIRONMENT="${1:-local}"
shift || true

case "$ENVIRONMENT" in
  local)
    exec bash "$ROOT/infra/scripts/quorum.sh" start "$@"
    ;;
  staging)
    required=(SERVER_IMAGE KFE_SERVICE_IMAGE WEB_PAGE_IMAGE VAULT_IMAGE NODE_IMAGE TOR_IMAGE)
    for name in "${required[@]}"; do
      value="${!name:-}"
      if [[ ! "$value" =~ @sha256:[0-9a-f]{64}$ ]]; then
        echo "${name} must be an immutable image digest." >&2
        exit 2
      fi
    done
    bash "$ROOT/infra/kubernetes/scripts/deploy.sh" staging "$@"
    bash "$ROOT/infra/kubernetes/scripts/deploy.sh" staging-vault "$@"
    cat <<'NEXT'
[+] Core and the first independent Vault are running.
[!] The Vault remains local-only until an operator publishes the signed
    OLD -> JOINT -> NEW membership manifests. Deploy never activates a signer.
NEXT
    ;;
  production)
    exec bash "$ROOT/infra/production/preflight.sh" --start "$@"
    ;;
  *)
    echo "Usage: infra/start-complete.sh <local|staging|production> [--dry-run]" >&2
    exit 2
    ;;
esac
