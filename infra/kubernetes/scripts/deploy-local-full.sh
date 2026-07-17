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
  - LND (testnet4) + peer for local liquidity
  - Tor hidden service for the web-page API gateway
  - Grafana + Prometheus (namespace monitoring; always ensured)

Options:
  --dry-run            Validate against the Kubernetes API without persisting resources.
  --skip-image-import  Do not import kerosene/*:local images into the cluster first.
  --strict-image-import
                       Abort if local image import fails. By default, continue
                       with images already available to the cluster.
  --wait               Wait for workloads after apply.

Environment:
  KEROSENE_HOST_HOME           Host home used for kubeconfig and onion keys (default: $HOME)
  KEROSENE_REPO_ROOT           Repository root for hostPath data dirs
  KEROSENE_AUTO_CREATE_CLUSTER Create a kind cluster when no API is reachable (default: 1)
  KUBECONFIG                   Explicit kubeconfig path
  KEROSENE_SKIP_MONITORING=1              Skip Grafana/Prometheus ensure
  KEROSENE_SKIP_MONITORING_PORT_FORWARD=1 Skip only local port-forwards
  GRAFANA_ADMIN_PASSWORD                  Default: admin
  GRAFANA_LOCAL_PORT                      Default: 3000
  PROMETHEUS_LOCAL_PORT                   Default: 9090
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$(cd "$K8S_DIR/.." && pwd)"
REPO_ROOT="$(cd "$INFRA_DIR/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
NS="kerosene-local"

# shellcheck source=infra/kubernetes/scripts/local-host-env.sh
source "$SCRIPT_DIR/local-host-env.sh"
kerosene_load_local_host_env "$REPO_ROOT"
kerosene_prepare_local_host_paths

KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi
DRY_RUN=0
SKIP_IMAGE_IMPORT=0
STRICT_IMAGE_IMPORT=0
WAIT=0
IMAGE_IMPORT_SUCCEEDED=0
RENDER_ROOT=""
WORK_OVERLAY=""

# shellcheck source=infra/scripts/host-services.sh
source "$INFRA_DIR/scripts/host-services.sh"

cleanup() {
  if [[ -n "$RENDER_ROOT" && -d "$RENDER_ROOT" ]]; then
    rm -rf "$RENDER_ROOT"
  fi
}
trap cleanup EXIT

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

refresh_kubectl_args() {
  KUBECTL_ARGS=()
  if [[ -n "${KUBECONFIG:-}" ]]; then
    KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
  fi
}

render_overlay() {
  RENDER_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/kerosene-local-full-XXXXXX")"
  WORK_OVERLAY="$(
    KEROSENE_RENDER_ROOT="$RENDER_ROOT" \
      KEROSENE_HOST_HOME="$KEROSENE_HOST_HOME" \
      KEROSENE_REPO_ROOT="$KEROSENE_REPO_ROOT" \
      KEROSENE_LOCAL_ONION_KEYS_PATH="$KEROSENE_LOCAL_ONION_KEYS_PATH" \
      KEROSENE_LOCAL_POSTGRES_DATA="$KEROSENE_LOCAL_POSTGRES_DATA" \
      KEROSENE_LOCAL_BITCOIN_DATA="$KEROSENE_LOCAL_BITCOIN_DATA" \
      bash "$SCRIPT_DIR/render-local-full-overlay.sh"
  )"
  echo "[*] Rendered local-full overlay with host paths under $KEROSENE_HOST_HOME"
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

record_tor_config_hash() {
  local tor_manifest="$WORK_OVERLAY/local-tor-onion.yaml"
  local config_hash payload

  if [[ ! -f "$tor_manifest" ]]; then
    echo "[!] Tor manifest not found; cannot record config hash: $tor_manifest" >&2
    return 0
  fi

  config_hash="$(sha256sum "$tor_manifest" | awk '{print $1}')"
  payload="$(printf '{"spec":{"template":{"metadata":{"annotations":{"kerosene.io/tor-config-hash":"%s"}}}}}' "$config_hash")"
  echo "[*] Recording Tor config hash for deployment/tor-onion"
  kubectl_cmd -n "$NS" patch deployment/tor-onion --type merge -p "$payload" >/dev/null
}

record_imported_local_image_ids() {
  record_local_image_id deployment/server localhost:5000/kerosene/server:local
  record_local_image_id deployment/kfe-service localhost:5000/kerosene/kfe-service:local
  record_local_image_id statefulset/mpc-sidecar kerosene/mpc-sidecar:local
  record_local_image_id deployment/web-page localhost:5000/kerosene/web-page:local
  record_local_image_id deployment/tor-onion kerosene/tor:local
}

cleanup_stale_local_full_resources() {
  kubectl_cmd -n "$NS" delete networkpolicy/local-full-allow-nodeport-ingress --ignore-not-found >/dev/null
}

require_cluster_access() {
  if ! \
    KEROSENE_HOST_HOME="$KEROSENE_HOST_HOME" \
    KEROSENE_REPO_ROOT="$KEROSENE_REPO_ROOT" \
    KEROSENE_AUTO_CREATE_CLUSTER="$KEROSENE_AUTO_CREATE_CLUSTER" \
    KEROSENE_KUBERNETES_READY_TIMEOUT="${KEROSENE_KUBERNETES_READY_TIMEOUT:-60}" \
    KEROSENE_KUBERNETES_READY_INTERVAL="${KEROSENE_KUBERNETES_READY_INTERVAL:-2}" \
    KEROSENE_KIND_CLUSTER_NAME="$KEROSENE_KIND_CLUSTER_NAME" \
    KEROSENE_KIND_KUBECONFIG="$KEROSENE_KIND_KUBECONFIG" \
    KUBECONFIG="${KUBECONFIG:-}" \
    KUBECTL="$KUBECTL" \
    bash "$SCRIPT_DIR/ensure-local-cluster.sh"
  then
    exit 1
  fi

  # ensure-local-cluster runs in a subshell; re-pick a working kubeconfig here.
  if ! kubectl_cmd get --raw=/readyz >/dev/null 2>&1; then
    if [[ -f "$KEROSENE_KIND_KUBECONFIG" ]]; then
      export KUBECONFIG="$KEROSENE_KIND_KUBECONFIG"
      refresh_kubectl_args
    elif [[ -f "$KEROSENE_HOST_HOME/.kube/config" ]]; then
      export KUBECONFIG="$KEROSENE_HOST_HOME/.kube/config"
      refresh_kubectl_args
    fi
  fi

  local context
  if ! context="$(kubectl_cmd config current-context 2>/dev/null)"; then
    echo "[!] Kubernetes context still missing after cluster ensure." >&2
    exit 1
  fi
  if ! kubectl_cmd get --raw=/readyz >/dev/null 2>&1; then
    echo "[!] Kubernetes API is not reachable for context: $context" >&2
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
render_overlay
require_cluster_access

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[*] Server-side dry-run for local-full overlay"
  # Server-side dry-run does not create the namespace for later objects in the
  # same batch. Ensure it exists first so namespaced resources can be validated.
  if ! kubectl_cmd get namespace "$NS" >/dev/null 2>&1; then
    echo "[*] Creating namespace $NS so server-side dry-run can validate namespaced objects"
    kubectl_cmd create namespace "$NS"
  fi
  kubectl_cmd apply -k "$WORK_OVERLAY" --server-side --dry-run=server
  echo "[+] local-full dry-run completed."
  exit 0
fi

# True if the image exists in the local Docker engine.
local_docker_has_image() {
  local image="$1"
  command -v docker >/dev/null 2>&1 || return 1
  docker image inspect "$image" >/dev/null 2>&1
}

# True if the image is already loaded into the kind node containerd (k8s.io).
# Host Docker can be empty after a failed rebuild while kind still has usable tags.
local_kind_has_image() {
  local image="$1"
  local kind_name="${KEROSENE_KIND_CLUSTER_NAME:-kerosene-local}"
  local node="kerosene-local-control-plane"
  local candidates=("$image")

  # crictl/ctr usually store short docker.io names with an explicit registry prefix.
  if [[ "$image" != */*/* && "$image" == */* ]]; then
    candidates+=("docker.io/$image")
  fi

  if ! command -v docker >/dev/null 2>&1; then
    return 1
  fi
  if ! docker inspect "$node" >/dev/null 2>&1; then
    return 1
  fi

  local candidate
  for candidate in "${candidates[@]}"; do
    if docker exec "$node" crictl inspecti "$candidate" >/dev/null 2>&1; then
      return 0
    fi
    if docker exec "$node" ctr -n k8s.io images check "$candidate" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

require_local_app_images() {
  # Abort apply when required local app images are missing. This prevents the
  # previous failure mode: import dies early, deploy continues, pods sit in
  # ImagePullBackOff forever.
  #
  # Accept either host Docker tags (normal import path) or images already present
  # in the kind node. Builds write to Docker on /var; when that disk is full the
  # rebuild fails even though kind still holds the last good tags.
  local missing=0
  local image
  local found_where
  local required=(
    "kerosene/server:local"
    "kerosene/kfe-service:local"
    "localhost:5000/kerosene/server:local"
    "localhost:5000/kerosene/kfe-service:local"
    "kerosene/mpc-sidecar:local"
    "kerosene/tor:local"
  )

  if ! command -v docker >/dev/null 2>&1; then
    echo "[!] Docker CLI not found; cannot verify local application images." >&2
    return 1
  fi

  for image in "${required[@]}"; do
    found_where=""
    if local_docker_has_image "$image"; then
      found_where="docker"
    elif local_kind_has_image "$image"; then
      found_where="kind"
    else
      echo "[!] Required local image missing: $image" >&2
      missing=1
      continue
    fi
    if [[ "$found_where" == "kind" ]]; then
      echo "[*] Found $image in kind node (host Docker copy not required)"
    fi
  done

  if [[ "$missing" -eq 1 ]]; then
    echo "[!] Refusing to apply local-full without the required app images." >&2
    echo "[!] Fix the image build/import and retry:" >&2
    echo "      # If /var is full (Docker data-root), free space or relocate Docker first:" >&2
    echo "      df -h /var && docker system df" >&2
    echo "      docker builder prune -af" >&2
    echo "      bash infra/kubernetes/scripts/import-local-docker-images.sh" >&2
    echo "      bash infra/deploy.sh --wait" >&2
    echo "[!] Or deploy with images already in kind: bash infra/deploy.sh --skip-image-import --wait" >&2
    return 1
  fi
  return 0
}

if [[ "$SKIP_IMAGE_IMPORT" -eq 0 ]]; then
  echo "[*] Importing local application images into the Kubernetes container runtime"
  if bash "$SCRIPT_DIR/import-local-docker-images.sh"; then
    IMAGE_IMPORT_SUCCEEDED=1
  else
    echo "[!] Image import failed." >&2
    if [[ "$STRICT_IMAGE_IMPORT" -eq 1 ]]; then
      echo "[!] Aborting because --strict-image-import was requested." >&2
      exit 1
    fi
    echo "[!] Checking whether usable local images already exist..." >&2
    if ! require_local_app_images; then
      exit 1
    fi
    echo "[!] Continuing with images already available to the cluster." >&2
  fi
else
  echo "[*] Skipping image import by request"
  if ! require_local_app_images; then
    exit 1
  fi
fi

echo "[*] Applying local-full overlay"
kubectl_cmd apply -k "$WORK_OVERLAY"
cleanup_stale_local_full_resources
record_tor_config_hash

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

# Always bring up Grafana + Prometheus with the local server stack.
echo "[*] Ensuring Grafana + Prometheus (monitoring namespace)"
KUBECONFIG="${KUBECONFIG:-}" KUBECTL="$KUBECTL" \
  bash "$SCRIPT_DIR/ensure-local-monitoring.sh" || {
    echo "[!] Monitoring stack ensure failed (non-fatal for core API)." >&2
  }

echo "[+] local-full deployment submitted."
echo "[+] clear-net service exposure: disabled"
echo "[+] host data:"
echo "    postgres: $KEROSENE_LOCAL_POSTGRES_DATA"
echo "    bitcoin:  $KEROSENE_LOCAL_BITCOIN_DATA"
echo "    onion keys: $KEROSENE_LOCAL_ONION_KEYS_PATH"
if kubectl_cmd -n "$NS" get deploy/tor-onion >/dev/null 2>&1; then
  onion_hostname="$(kubectl_cmd -n "$NS" exec deploy/tor-onion -- sh -c 'cat /var/lib/tor/kerosene_service/hostname' 2>/dev/null || true)"
  if [[ -n "$onion_hostname" ]]; then
    echo "[+] tor onion: http://$onion_hostname"
    echo "[+] Android local release will use this .onion automatically via scripts/run-android-release-local.sh."
  else
    echo "[!] tor-onion exists but hostname is not ready yet. Re-run with --wait or check: kubectl -n kerosene-local logs deploy/tor-onion" >&2
  fi
fi
