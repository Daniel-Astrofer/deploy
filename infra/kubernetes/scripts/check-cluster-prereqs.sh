#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"

echo "[*] Kubernetes context"
"$KUBECTL" config current-context

echo "[*] Nodes"
"$KUBECTL" get nodes -o wide

echo "[*] Kerosene namespaces"
"$KUBECTL" get namespace kerosene-local kerosene-staging kerosene-production 2>/dev/null || true

echo "[*] StorageClasses"
if ! "$KUBECTL" get storageclass; then
  echo "[!] No StorageClass detected. mpc-sidecar PVCs may stay Pending."
fi

echo "[*] IngressClasses"
if ! "$KUBECTL" get ingressclass; then
  echo "[!] No IngressClass detected. External HTTP routing is not ready."
fi

echo "[*] Metrics API"
if ! "$KUBECTL" top nodes; then
  echo "[!] Metrics API is not available. HPA can be created but will not scale from CPU/memory metrics."
fi
