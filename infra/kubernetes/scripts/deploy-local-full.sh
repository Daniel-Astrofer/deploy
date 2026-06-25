#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/scripts/deploy-local-full.sh [--dry-run] [--skip-image-import] [--wait]

Deploys the complete local Kubernetes runtime into namespace kerosene-local:
  - server
  - kfe-service
  - web-page
  - mpc-sidecar
  - PostgreSQL
  - Redis
  - Vault dev
  - Bitcoin Core regtest
  - LND local placeholder
  - Tor hidden service for the web-page API gateway

Options:
  --dry-run            Validate against the Kubernetes API without persisting resources.
  --skip-image-import  Do not import kerosene/*:local images into containerd first.
  --wait               Wait for workloads after apply.
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OVERLAY="$K8S_DIR/overlays/local-full"
KUBECTL="${KUBECTL:-kubectl}"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi
DRY_RUN=0
SKIP_IMAGE_IMPORT=0
WAIT=0

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-image-import) SKIP_IMAGE_IMPORT=1 ;;
    --wait) WAIT=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unsupported option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

bash "$SCRIPT_DIR/validate-local-full.sh"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[*] Server-side dry-run for local-full overlay"
  kubectl_cmd apply -k "$OVERLAY" --dry-run=server
  exit 0
fi

if [[ "$SKIP_IMAGE_IMPORT" -eq 0 ]]; then
  echo "[*] Importing local application images into Kubernetes containerd namespace"
  if ! bash "$SCRIPT_DIR/import-local-docker-images.sh"; then
    echo "[!] Image import failed. Continue only if the cluster can already pull kerosene/*:local images." >&2
    exit 1
  fi
else
  echo "[*] Skipping image import by request"
fi

echo "[*] Applying local-full overlay"
kubectl_cmd apply -k "$OVERLAY"

echo "[*] Current local-full objects"
kubectl_cmd -n kerosene-local get deploy,sts,pod,svc,hpa,pdb,networkpolicy

if [[ "$WAIT" -eq 1 ]]; then
  KUBECONFIG="${KUBECONFIG:-}" KUBECTL="$KUBECTL" bash "$SCRIPT_DIR/wait-local-full.sh"
fi

echo "[+] local-full deployment submitted."
echo "[+] server:   http://127.0.0.1:30080"
echo "[+] mpc:      http://127.0.0.1:30081/version"
echo "[+] web-page: http://127.0.0.1:30082"
echo "[+] KFE routes: use web-page NodePort 30082 for /kfe, /api/public/kfe and /api/admin/kfe."
if kubectl_cmd -n kerosene-local get deploy/tor-onion >/dev/null 2>&1; then
  onion_hostname="$(kubectl_cmd -n kerosene-local exec deploy/tor-onion -- sh -c 'cat /var/lib/tor/kerosene_service/hostname' 2>/dev/null || true)"
  if [[ -n "$onion_hostname" ]]; then
    echo "[+] tor onion: http://$onion_hostname"
    echo "[+] Android local release will use this .onion automatically via scripts/run-android-release-local.sh."
  else
    echo "[!] tor-onion exists but hostname is not ready yet. Re-run with --wait or check: kubectl -n kerosene-local logs deploy/tor-onion" >&2
  fi
fi
