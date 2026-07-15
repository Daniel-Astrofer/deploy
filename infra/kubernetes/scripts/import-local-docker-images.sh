#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/scripts/import-local-docker-images.sh [--skip-kfe-service-build] [--skip-web-page-build]

Builds local Kerosene images and copies them into the Kubernetes containerd
namespace used by kubelet. This is needed when Kubernetes uses containerd instead
of the Docker image store.

Expected targets:
  kerosene/server:local
  kerosene/kfe-service:local
  localhost:5000/kerosene/kfe-service:local
  kerosene/mpc-sidecar:local
  kerosene/tor:local
  kerosene/web-page:local

Options:
  --skip-kfe-service-build
                         Do not build/rebuild kerosene/kfe-service:local.
  --skip-web-page-build  Do not rebuild the Flutter web bundle or kerosene/web-page:local image.
USAGE
}

# Keep absolute paths before sourcing helpers — backend-common.sh overwrites SCRIPT_DIR.
IMPORT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$IMPORT_SCRIPT_DIR/../../.." && pwd)"
BACKEND_COMMON="$REPO_ROOT/infra/scripts/backend-common.sh"
FLUTTER_COMMON="$REPO_ROOT/infra/scripts/flutter-common.sh"
LOCAL_HOST_ENV="$IMPORT_SCRIPT_DIR/local-host-env.sh"
SKIP_WEB_PAGE_BUILD=0
SKIP_KFE_SERVICE_BUILD=0
IMPORT_MODE="" # kind | containerd
KIND_BIN=""
KIND_CLUSTER_NAME="${KEROSENE_KIND_CLUSTER_NAME:-kerosene-local}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-kfe-service-build) SKIP_KFE_SERVICE_BUILD=1 ;;
    --skip-web-page-build) SKIP_WEB_PAGE_BUILD=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unsupported option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# shellcheck source=infra/scripts/backend-common.sh
source "$BACKEND_COMMON"
# shellcheck source=infra/scripts/flutter-common.sh
source "$FLUTTER_COMMON"
# shellcheck source=infra/kubernetes/scripts/local-host-env.sh
source "$LOCAL_HOST_ENV"
kerosene_load_local_host_env "$REPO_ROOT"

require_docker

CTR_CMD=()

resolve_kind_bin() {
  if [[ -n "${KIND:-}" && -x "$KIND" ]]; then
    printf '%s\n' "$KIND"
    return 0
  fi
  if command -v kind >/dev/null 2>&1; then
    command -v kind
    return 0
  fi
  local candidate
  for candidate in \
    "${KEROSENE_HOST_HOME}/.local/bin/kind" \
    "${HOME:-}/.local/bin/kind" \
    /usr/local/bin/kind
  do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

detect_import_mode() {
  if KIND_BIN="$(resolve_kind_bin 2>/dev/null)" \
    && "$KIND_BIN" get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER_NAME"; then
    IMPORT_MODE="kind"
    info "Import mode: kind load into cluster $KIND_CLUSTER_NAME"
    return 0
  fi

  if ! command -v ctr >/dev/null 2>&1; then
    fail "Neither kind cluster '$KIND_CLUSTER_NAME' nor ctr is available for image import."
  fi

  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    CTR_CMD=(ctr)
    IMPORT_MODE="containerd"
    info "Import mode: containerd namespace k8s.io"
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    fail "sudo is required to import images into containerd namespace k8s.io."
  fi

  if sudo -n true >/dev/null 2>&1; then
    CTR_CMD=(sudo -n ctr)
    IMPORT_MODE="containerd"
    info "Import mode: containerd namespace k8s.io"
    return 0
  fi

  if [[ -t 0 ]]; then
    info "sudo credentials are required to import images into containerd namespace k8s.io."
    sudo -v || fail "sudo authentication failed. Re-run after granting sudo access, or use --skip-image-import to keep existing cluster images."
    CTR_CMD=(sudo ctr)
    IMPORT_MODE="containerd"
    info "Import mode: containerd namespace k8s.io"
    return 0
  fi

  fail "sudo credentials are required to import images into containerd namespace k8s.io. Run this script from an interactive terminal, grant passwordless sudo for ctr, use a kind cluster, or re-run the deploy with --skip-image-import."
}

detect_import_mode

try_tag_from_compose_service() {
  local target="$1"
  shift
  local services=("$@")

  if docker image inspect "$target" >/dev/null 2>&1; then
    info "Docker image already exists: $target"
    return 0
  fi

  local service image_id
  for service in "${services[@]}"; do
    image_id="$(compose images -q "$service" 2>/dev/null | head -n 1 || true)"
    if [[ -n "$image_id" ]]; then
      info "Tagging Compose image for $service as $target"
      docker tag "$image_id" "$target"
      return 0
    fi
  done

  return 1
}

ensure_tag_from_compose_service() {
  local target="$1"
  shift

  if try_tag_from_compose_service "$target" "$@"; then
    return 0
  fi

  fail "Could not find Docker image for $target. Run scripts/start-local.sh once so Compose builds the services."
}

build_server_image() {
  local target="kerosene/server:local"
  local dockerfile="$REPO_ROOT/infra/docker/images/server/Dockerfile"
  local context="$REPO_ROOT/backend/kerosene"

  if [[ ! -f "$dockerfile" ]]; then
    fail "Server Dockerfile not found: $dockerfile"
  fi
  if [[ ! -d "$context" ]]; then
    fail "Server build context not found: $context"
  fi

  if docker image inspect "$target" >/dev/null 2>&1; then
    info "Rebuilding existing Docker image: $target"
  fi

  info "Building $target from $dockerfile"
  docker build -t "$target" -f "$dockerfile" "$context"
}

build_kfe_service_image() {
  local target="kerosene/kfe-service:local"
  local dockerfile="$REPO_ROOT/infra/docker/images/kfe-service/Dockerfile"
  local context="$REPO_ROOT/backend/kerosene"

  if [[ "$SKIP_KFE_SERVICE_BUILD" -eq 1 ]]; then
    info "Skipping kfe-service image build by request."
    if try_tag_from_compose_service "$target" kfe-service-wvo kfe-service-iw5 kfe-service-ltv; then
      return 0
    fi
    docker image inspect "$target" >/dev/null 2>&1 || fail "Docker image not found: $target"
    return 0
  fi

  if docker image inspect "$target" >/dev/null 2>&1; then
    info "Rebuilding existing Docker image: $target"
  elif try_tag_from_compose_service "$target" kfe-service-wvo kfe-service-iw5 kfe-service-ltv; then
    info "Rebuilding Compose-tagged Docker image: $target"
  fi

  if [[ ! -f "$dockerfile" ]]; then
    fail "KFE Dockerfile not found: $dockerfile"
  fi

  info "Building $target from $dockerfile"
  docker build -t "$target" -f "$dockerfile" "$context"
}

build_mpc_sidecar_image() {
  local target="kerosene/mpc-sidecar:local"
  local dockerfile="$REPO_ROOT/infra/docker/images/mpc-sidecar/Dockerfile"
  local context="$REPO_ROOT/backend/mpc-sidecar"

  if [[ ! -f "$dockerfile" ]]; then
    fail "MPC sidecar Dockerfile not found: $dockerfile"
  fi
  if [[ ! -d "$context" ]]; then
    fail "MPC sidecar build context not found: $context"
  fi

  if docker image inspect "$target" >/dev/null 2>&1; then
    info "Rebuilding existing Docker image: $target"
  fi

  info "Building $target from $dockerfile"
  docker build -t "$target" -f "$dockerfile" "$context"
}

build_kubernetes_web_bundle() {
  local frontend_dir="$REPO_ROOT/frontend"
  local web_build="$frontend_dir/build/web"
  local flutter_bin

  if ! flutter_bin="$(kerosene_resolve_flutter_bin "$frontend_dir")"; then
    fail "Flutter CLI not found for the Kubernetes web-page build user."
  fi

  info "Building Flutter web bundle for Kubernetes web-page same-origin routing."
  (
    cd "$frontend_dir"
    FLUTTER_BUILD_ARGS=(web --release --csp --no-web-resources-cdn --target lib/web_main.dart)
    if [[ "${FLUTTER_BUILD_NO_PUB:-0}" == "1" ]]; then
      FLUTTER_BUILD_ARGS+=(--no-pub)
    fi
    kerosene_run_flutter "$flutter_bin" build "${FLUTTER_BUILD_ARGS[@]}" \
      --dart-define="PASSKEY_RP_ID=${FRONTEND_PASSKEY_RP_ID:-kerosene-device}" \
      --dart-define="PASSKEY_ORIGIN=${FRONTEND_PASSKEY_ORIGIN:-android:apk-key-hash:kerosene}"
  )
  rm -f "$web_build/kerosene-runtime-config.json"
  kerosene_chown_sudo_user "$frontend_dir/.dart_tool" "$frontend_dir/build"
}

build_web_page_image() {
  local target="kerosene/web-page:local"
  local web_build="$REPO_ROOT/frontend/build/web"
  local nginx_conf="$REPO_ROOT/infra/runtime/web/nginx.k8s.conf"
  local dockerfile="$REPO_ROOT/infra/docker/images/web-page/Dockerfile"

  if [[ "$SKIP_WEB_PAGE_BUILD" -eq 1 ]]; then
    info "Skipping web-page image build by request."
    return 0
  fi

  build_kubernetes_web_bundle

  if [[ ! -f "$web_build/index.html" ]]; then
    fail "Flutter Kubernetes web-page build did not produce frontend/build/web/index.html."
  fi
  if [[ ! -f "$nginx_conf" ]]; then
    fail "nginx config not found: $nginx_conf"
  fi
  if [[ ! -f "$dockerfile" ]]; then
    fail "web-page Dockerfile not found: $dockerfile"
  fi

  info "Building $target from frontend/build/web with Kubernetes Nginx routing"
  docker build -t "$target" -f "$dockerfile" "$REPO_ROOT"
}

build_tor_image() {
  local target="kerosene/tor:local"
  local dockerfile="$REPO_ROOT/infra/docker/images/tor/Dockerfile"
  local context="$REPO_ROOT/infra/runtime/tor"

  if [[ ! -f "$dockerfile" ]]; then
    fail "Tor Dockerfile not found: $dockerfile"
  fi
  if [[ ! -d "$context" ]]; then
    fail "Tor runtime context not found: $context"
  fi

  if docker image inspect "$target" >/dev/null 2>&1; then
    info "Docker image already exists: $target"
    return 0
  fi

  info "Building $target from $dockerfile"
  docker build -t "$target" -f "$dockerfile" "$context"
}

ensure_local_registry_alias() {
  local source="$1"
  local alias="$2"

  if docker image inspect "$alias" >/dev/null 2>&1; then
    info "Docker image already exists: $alias"
    return 0
  fi

  info "Tagging $source as $alias"
  docker tag "$source" "$alias"
}

import_image() {
  local image="$1"

  if [[ "$IMPORT_MODE" == "kind" ]]; then
    info "Loading $image into kind cluster $KIND_CLUSTER_NAME"
    "$KIND_BIN" load docker-image "$image" --name "$KIND_CLUSTER_NAME" >/dev/null
    return 0
  fi

  info "Importing $image into containerd namespace k8s.io"
  docker save "$image" | "${CTR_CMD[@]}" -n k8s.io images import - >/dev/null
}

build_server_image
build_kfe_service_image
build_mpc_sidecar_image
build_web_page_image
build_tor_image

# local-full kustomization rewrites app images to localhost:5000/*:local
ensure_local_registry_alias "kerosene/server:local" "localhost:5000/kerosene/server:local"
ensure_local_registry_alias "kerosene/kfe-service:local" "localhost:5000/kerosene/kfe-service:local"
if docker image inspect "kerosene/web-page:local" >/dev/null 2>&1; then
  ensure_local_registry_alias "kerosene/web-page:local" "localhost:5000/kerosene/web-page:local"
fi

import_image "kerosene/server:local"
import_image "localhost:5000/kerosene/server:local"
import_image "kerosene/kfe-service:local"
import_image "localhost:5000/kerosene/kfe-service:local"
import_image "kerosene/mpc-sidecar:local"
import_image "kerosene/tor:local"
if docker image inspect "kerosene/web-page:local" >/dev/null 2>&1; then
  import_image "kerosene/web-page:local"
  import_image "localhost:5000/kerosene/web-page:local"
fi

if [[ "$IMPORT_MODE" == "containerd" ]]; then
  info "Imported images visible to Kubernetes:"
  "${CTR_CMD[@]}" -n k8s.io images ls | grep -E 'kerosene/(server|kfe-service|mpc-sidecar|tor|web-page)' || true
else
  info "Images loaded into kind cluster $KIND_CLUSTER_NAME"
fi

info "Done. You can now run: bash infra/start.sh --skip-image-import"
