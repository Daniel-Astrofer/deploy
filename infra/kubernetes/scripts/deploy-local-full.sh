#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/scripts/deploy-local-full.sh [--dry-run] [--skip-image-import] [--strict-image-import] [--wait]

Deploys the complete local Kubernetes runtime into namespace kerosene-local:
  - server
  - kfe-service
  - web-page
  - mpc-sidecar
  - PostgreSQL
  - Redis
  - Vault dev
  - Bitcoin Core testnet4
  - LND local placeholder
  - Tor hidden service for the web-page API gateway

Options:
  --dry-run            Validate against the Kubernetes API without persisting resources.
  --skip-image-import  Do not import kerosene/*:local images into containerd first.
  --strict-image-import
                       Abort if local image import fails. By default, continue
                       with images already available to the cluster.
  --wait               Wait for workloads after apply.
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$(cd "$K8S_DIR/.." && pwd)"
OVERLAY="$K8S_DIR/overlays/local-full"
KUBECTL="${KUBECTL:-kubectl}"
NS="kerosene-local"
HOST_HOME="${KEROSENE_HOST_HOME:-/home/omega}"
DEFAULT_KUBECONFIG="${KEROSENE_DEFAULT_KUBECONFIG:-$HOST_HOME/.kube/config}"
if [[ -z "${KUBECONFIG:-}" && -f "$DEFAULT_KUBECONFIG" ]]; then
  export KUBECONFIG="$DEFAULT_KUBECONFIG"
fi
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi
DRY_RUN=0
SKIP_IMAGE_IMPORT=0
STRICT_IMAGE_IMPORT=0
WAIT=0
IMAGE_IMPORT_SUCCEEDED=0
KUBERNETES_READY_TIMEOUT="${KEROSENE_KUBERNETES_READY_TIMEOUT:-60}"
KUBERNETES_READY_INTERVAL="${KEROSENE_KUBERNETES_READY_INTERVAL:-2}"

# shellcheck source=infra/scripts/host-services.sh
source "$INFRA_DIR/scripts/host-services.sh"

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

record_local_image_id() {
  local resource="$1"
  local image="$2"
  local image_id payload

  if ! command -v docker >/dev/null 2>&1; then
    echo "[!] Docker CLI not found; cannot record local image id for $resource." >&2
    return 0
  fi

  image_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
  if [[ -z "$image_id" ]]; then
    echo "[!] Local Docker image not found for rollout detection: $image" >&2
    return 0
  fi

  payload="$(printf '{"spec":{"template":{"metadata":{"annotations":{"kerosene.io/local-image-id":"%s"}}}}}' "$image_id")"
  echo "[*] Recording local image id for $resource from $image"
  kubectl_cmd -n "$NS" patch "$resource" --type merge -p "$payload" >/dev/null
}

record_imported_local_image_ids() {
  record_local_image_id deployment/server kerosene/server:local
  record_local_image_id deployment/kfe-service localhost:5000/kerosene/kfe-service:local
  record_local_image_id statefulset/mpc-sidecar kerosene/mpc-sidecar:local
  record_local_image_id deployment/web-page kerosene/web-page:local
  record_local_image_id deployment/tor-onion kerosene/tor:local
}

require_cluster_access() {
  local context
  local deadline now

  if ! context="$(kubectl_cmd config current-context 2>/dev/null)"; then
    cat >&2 <<'EOF'
[!] Kubernetes API is not reachable or no kubectl context is selected.
    Check the active context:
      kubectl config current-context
    Start or select your local cluster, then retry:
      bash infra/start.sh
EOF
    exit 1
  fi

  deadline=$(( $(date +%s) + KUBERNETES_READY_TIMEOUT ))
  while ! kubectl_cmd get --raw=/readyz >/dev/null 2>&1; do
    now=$(date +%s)
    if (( now >= deadline )); then
      break
    fi
    echo "[*] Waiting for Kubernetes API for context: $context"
    sleep "$KUBERNETES_READY_INTERVAL"
  done

  if ! kubectl_cmd get --raw=/readyz >/dev/null 2>&1; then
    cat >&2 <<EOF
[!] Kubernetes API is not reachable for context: $context
    Check cluster status:
      kubectl cluster-info
    Start or select your local cluster, then retry:
      bash infra/start.sh
EOF
    exit 1
  fi

  echo "[*] Kubernetes context: $context"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-image-import) SKIP_IMAGE_IMPORT=1 ;;
    --strict-image-import) STRICT_IMAGE_IMPORT=1 ;;
    --wait) WAIT=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unsupported option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

ensure_local_host_services
bash "$SCRIPT_DIR/validate-local-full.sh"
require_cluster_access

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[*] Server-side dry-run for local-full overlay"
  kubectl_cmd apply -k "$OVERLAY" --dry-run=server
  exit 0
fi

if [[ "$SKIP_IMAGE_IMPORT" -eq 0 ]]; then
  echo "[*] Importing local application images into Kubernetes containerd namespace"
  if bash "$SCRIPT_DIR/import-local-docker-images.sh"; then
    IMAGE_IMPORT_SUCCEEDED=1
  else
    echo "[!] Image import failed." >&2
    if [[ "$STRICT_IMAGE_IMPORT" -eq 1 ]]; then
      echo "[!] Aborting because --strict-image-import was requested." >&2
      exit 1
    fi
    echo "[!] Continuing with images already available to the cluster." >&2
    echo "[!] If rollout reports ImagePullBackOff or stale images, rerun after importing images with sudo/containerd access." >&2
  fi
else
  echo "[*] Skipping image import by request"
fi

echo "[*] Applying local-full overlay"
kubectl_cmd apply -k "$OVERLAY"

if [[ "$IMAGE_IMPORT_SUCCEEDED" -eq 1 ]]; then
  echo "[*] Recording imported local image ids for Kubernetes rollout detection"
  record_imported_local_image_ids
fi

echo "[*] Current local-full objects"
kubectl_cmd -n "$NS" get deploy,sts,pod,svc,hpa,pdb,networkpolicy

if kubectl_cmd -n "$NS" get svc -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.type}{"\n"}{end}' \
    | grep -E ' (NodePort|LoadBalancer)$' >/dev/null; then
  echo "[!] Refusing local-full runtime with clear-net service exposure." >&2
  kubectl_cmd -n "$NS" get svc -o wide >&2
  exit 1
fi

if [[ "$WAIT" -eq 1 ]]; then
  KUBECONFIG="${KUBECONFIG:-}" KUBECTL="$KUBECTL" bash "$SCRIPT_DIR/wait-local-full.sh"
fi

echo "[+] local-full deployment submitted."
echo "[+] clear-net service exposure: disabled"
if kubectl_cmd -n "$NS" get deploy/tor-onion >/dev/null 2>&1; then
  onion_hostname="$(kubectl_cmd -n "$NS" exec deploy/tor-onion -- sh -c 'cat /var/lib/tor/kerosene_service/hostname' 2>/dev/null || true)"
  if [[ -n "$onion_hostname" ]]; then
    echo "[+] tor onion: http://$onion_hostname"
    echo "[+] Android local release will use this .onion automatically via scripts/run-android-release-local.sh."
  else
    echo "[!] tor-onion exists but hostname is not ready yet. Re-run with --wait or check: kubectl -n kerosene-local logs deploy/tor-onion" >&2
  fi
fi
