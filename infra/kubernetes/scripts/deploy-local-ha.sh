#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
K8S="$ROOT/backend/kerosene-infrastructure/k8s"
OVERLAY="$K8S/overlays/local-ha"

if [[ "${1:-}" == "--dry-run" ]]; then
  kubectl kustomize "$OVERLAY" >/tmp/kerosene-local-ha.yaml
  kubectl apply --dry-run=client -f /tmp/kerosene-local-ha.yaml
  if kubectl get namespace kerosene-local-ha >/dev/null 2>&1; then
    kubectl apply --dry-run=server -f /tmp/kerosene-local-ha.yaml
  else
    kubectl apply --dry-run=server -f "$OVERLAY/namespace.yaml"
    echo "[*] Namespace kerosene-local-ha does not exist yet; skipped full server dry-run."
    echo "    Run: kubectl apply -f $OVERLAY/namespace.yaml"
    echo "    Then rerun: $0 --dry-run"
  fi
  exit 0
fi

if [[ "${1:-}" != "--skip-image-import" && -x "$K8S/scripts/retag-local-workload-images.sh" ]]; then
  "$K8S/scripts/retag-local-workload-images.sh" || true
fi
if [[ "${1:-}" != "--skip-image-import" && -x "$K8S/scripts/import-local-docker-images.sh" ]]; then
  "$K8S/scripts/import-local-docker-images.sh" || true
fi

kubectl apply -f "$OVERLAY/namespace.yaml"
kubectl apply -k "$OVERLAY"
kubectl -n kerosene-local-ha get deploy,sts,pod,svc
echo "[+] local-ha submitted. Use web-page NodePort 31090 for split Core/KFE routing."
