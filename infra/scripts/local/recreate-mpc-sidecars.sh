#!/usr/bin/env bash
set -euo pipefail
echo "[!] recreate-mpc-sidecars.sh removed: deploy uses vault mesh, not mpc-sidecar." >&2
echo "[!] Use infra/docker/compose/vault-mesh-lab.compose.yaml" >&2
exit 1
