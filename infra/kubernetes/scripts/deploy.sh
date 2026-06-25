#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 <local|staging|production> [--dry-run]

Required for production unless the overlay was already edited:
  SERVER_IMAGE=registry/server@sha256:...
  KFE_SERVICE_IMAGE=registry/kfe-service@sha256:...
  MPC_SIDECAR_IMAGE=registry/mpc-sidecar@sha256:...
  WEB_PAGE_IMAGE=registry/web-page@sha256:...

Optional:
  KUBECTL=kubectl
  KUSTOMIZE=kustomize
USAGE
}

ENVIRONMENT="${1:-}"
DRY_RUN="${2:-}"

if [[ -z "$ENVIRONMENT" || "$ENVIRONMENT" == "-h" || "$ENVIRONMENT" == "--help" ]]; then
  usage
  exit 0
fi

case "$ENVIRONMENT" in
  local) NAMESPACE="kerosene-local" ;;
  staging) NAMESPACE="kerosene-staging" ;;
  production) NAMESPACE="kerosene-production" ;;
  *) echo "Unsupported environment: $ENVIRONMENT" >&2; usage; exit 2 ;;
esac

if [[ -n "$DRY_RUN" && "$DRY_RUN" != "--dry-run" ]]; then
  echo "Unsupported option: $DRY_RUN" >&2
  usage
  exit 2
fi

KUBECTL="${KUBECTL:-kubectl}"
KUSTOMIZE_BIN="${KUSTOMIZE:-kustomize}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OVERLAY="$K8S_ROOT/overlays/$ENVIRONMENT"

if ! command -v "$KUBECTL" >/dev/null 2>&1; then
  echo "kubectl not found. Set KUBECTL=/path/to/kubectl." >&2
  exit 127
fi

if [[ -n "${SERVER_IMAGE:-}" || -n "${KFE_SERVICE_IMAGE:-}" || -n "${MPC_SIDECAR_IMAGE:-}" || -n "${WEB_PAGE_IMAGE:-}" ]]; then
  if ! command -v "$KUSTOMIZE_BIN" >/dev/null 2>&1; then
    echo "kustomize not found. It is required when setting images through environment variables." >&2
    echo "Install kustomize or edit the overlay image tags manually and run with no image env vars." >&2
    exit 127
  fi
fi

if [[ ! -d "$OVERLAY" ]]; then
  echo "Overlay not found: $OVERLAY" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

cp -R "$K8S_ROOT" "$TMP_DIR/k8s"
WORK_OVERLAY="$TMP_DIR/k8s/overlays/$ENVIRONMENT"

if [[ -n "${SERVER_IMAGE:-}" ]]; then
  (cd "$WORK_OVERLAY" && "$KUSTOMIZE_BIN" edit set image "kerosene/server=${SERVER_IMAGE}")
fi
if [[ -n "${KFE_SERVICE_IMAGE:-}" ]]; then
  (cd "$WORK_OVERLAY" && "$KUSTOMIZE_BIN" edit set image "kerosene/kfe-service=${KFE_SERVICE_IMAGE}")
fi
if [[ -n "${MPC_SIDECAR_IMAGE:-}" ]]; then
  (cd "$WORK_OVERLAY" && "$KUSTOMIZE_BIN" edit set image "kerosene/mpc-sidecar=${MPC_SIDECAR_IMAGE}")
fi
if [[ -n "${WEB_PAGE_IMAGE:-}" ]]; then
  (cd "$WORK_OVERLAY" && "$KUSTOMIZE_BIN" edit set image "kerosene/web-page=${WEB_PAGE_IMAGE}")
fi

MANIFEST="$TMP_DIR/manifest.yaml"
"$KUBECTL" kustomize "$WORK_OVERLAY" > "$MANIFEST"

if [[ "$ENVIRONMENT" == "production" ]] && grep -q 'replace-me' "$MANIFEST"; then
  echo "Refusing production deploy with replace-me image tags." >&2
  echo "Set SERVER_IMAGE, KFE_SERVICE_IMAGE, MPC_SIDECAR_IMAGE and WEB_PAGE_IMAGE to immutable tags or digests." >&2
  exit 3
fi

echo "[*] Validating rendered manifest for namespace $NAMESPACE..."
"$KUBECTL" apply --dry-run=client -f "$MANIFEST" >/dev/null

if [[ "$DRY_RUN" == "--dry-run" ]]; then
  echo "[*] Running server-side dry-run..."
  if "$KUBECTL" get namespace "$NAMESPACE" >/dev/null 2>&1; then
    "$KUBECTL" apply --server-side --dry-run=server -f "$MANIFEST"
  else
    echo "[!] Namespace $NAMESPACE does not exist yet."
    echo "[!] Kubernetes server-side dry-run does not create the namespace for later objects in the same dry-run batch."
    echo "[!] Client validation passed. To run full server dry-run first, create the namespace with:"
    echo "    $KUBECTL create namespace $NAMESPACE --dry-run=client -o yaml | $KUBECTL apply -f -"
  fi
  exit 0
fi

echo "[*] Ensuring namespace exists..."
"$KUBECTL" create namespace "$NAMESPACE" --dry-run=client -o yaml | "$KUBECTL" apply -f -

echo "[*] Applying manifest..."
"$KUBECTL" apply --server-side -f "$MANIFEST"

echo "[*] Waiting for core workloads..."
"$KUBECTL" -n "$NAMESPACE" rollout status deployment/server --timeout=10m
"$KUBECTL" -n "$NAMESPACE" rollout status deployment/kfe-service --timeout=10m
"$KUBECTL" -n "$NAMESPACE" rollout status deployment/web-page --timeout=5m
"$KUBECTL" -n "$NAMESPACE" rollout status statefulset/mpc-sidecar --timeout=10m

echo "[+] $ENVIRONMENT deploy completed."
