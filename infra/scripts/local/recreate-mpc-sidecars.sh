#!/usr/bin/env bash
set -euo pipefail

echo "[!] recreate-mpc-sidecars.sh removed: deploy uses vault mesh, not mpc-sidecar." >&2
echo "[!] Start mesh: bash infra/kubernetes/scripts/ensure-vault-mesh-lab.sh" >&2
echo "[!] See backend/mpc-sidecar/REMOVED.txt" >&2
exit 2
