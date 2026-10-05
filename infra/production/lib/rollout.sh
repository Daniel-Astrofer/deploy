#!/usr/bin/env bash
set -euo pipefail

apply_manifest() {
  local manifest="$1"
  kubectl apply --server-side --dry-run=server -f "$manifest" >/dev/null
  kubectl apply --server-side -f "$manifest"
  echo "[production] private overlay applied; Vault signers were not activated."
}
