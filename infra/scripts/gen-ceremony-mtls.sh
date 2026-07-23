#!/usr/bin/env bash
# Infra wrapper → vault ceremony CA (unique SPIFFE + short TTL) + optional audit keys.
# Lab ≠ mainnet. See backend/kerosene-vault/docs/MTLS_SPIFFE_LAYOUT.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VAULT_SCRIPTS="$REPO_ROOT/backend/kerosene-vault/scripts"

WITH_AUDIT=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-audit) WITH_AUDIT=0; shift ;;
    -h|--help)
      echo "Usage: $0 [--no-audit]"
      echo "  Generates ceremony-certs/ (SPIRE-like unique SPIFFE) and audit keys."
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

"$VAULT_SCRIPTS/gen_ceremony_mtls_certs.sh"
if [[ "$WITH_AUDIT" -eq 1 ]]; then
  "$VAULT_SCRIPTS/gen_mesh_audit_keys.sh"
fi

echo
echo "Next: source ceremony-certs/audit/env.hint (if audit generated),"
echo "      mount per-node paths from ceremony-certs/nodes/{id}/,"
echo "      then VAULT_CEREMONY_MODE=production ./backend/kerosene-vault/scripts/genesis_ceremony_checklist.sh"
