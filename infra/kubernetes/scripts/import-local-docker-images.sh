#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/scripts/import-local-docker-images.sh [--skip-web-page-build]

Copies local Docker/Compose-built Kerosene images into the Kubernetes containerd
namespace used by kubelet. This is needed when Kubernetes uses containerd and the
local development stack was built with Docker Compose.

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BACKEND_COMMON="$REPO_ROOT/scripts/backend-common.sh"
FLUTTER_COMMON="$REPO_ROOT/scripts/flutter-common.sh"
SKIP_WEB_PAGE_BUILD=0
SKIP_KFE_SERVICE_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-kfe-service-build) SKIP_KFE_SERVICE_BUILD=1 ;;
    --skip-web-page-build) SKIP_WEB_PAGE_BUILD=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unsupported option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# shellcheck source=scripts/backend-common.sh
source "$BACKEND_COMMON"
# shellcheck source=scripts/flutter-common.sh
source "$FLUTTER_COMMON"

require_docker

if ! command -v sudo >/dev/null 2>&1; then
  fail "sudo is required to import images into containerd namespace k8s.io."
fi
if ! command -v ctr >/dev/null 2>&1; then
  fail "ctr not found. Install containerd tools first."
fi

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

import_to_k8s_containerd() {
  local image="$1"
  info "Importing $image into containerd namespace k8s.io"
  docker save "$image" | sudo ctr -n k8s.io images import - >/dev/null
}

ensure_tag_from_compose_service \
  "kerosene/server:local" \
  server-wvo server-iw5 server-ltv kerosene-app-is kerosene-app-ch kerosene-app-sg

build_kfe_service_image

ensure_tag_from_compose_service \
  "kerosene/mpc-sidecar:local" \
  mpc-sidecar-wvo mpc-sidecar-iw5 mpc-sidecar-ltv mpc-sidecar-is mpc-sidecar-ch mpc-sidecar-sg

build_web_page_image
build_tor_image

ensure_local_registry_alias "kerosene/kfe-service:local" "localhost:5000/kerosene/kfe-service:local"

import_to_k8s_containerd "kerosene/server:local"
import_to_k8s_containerd "kerosene/kfe-service:local"
import_to_k8s_containerd "localhost:5000/kerosene/kfe-service:local"
import_to_k8s_containerd "kerosene/mpc-sidecar:local"
import_to_k8s_containerd "kerosene/tor:local"
if docker image inspect "kerosene/web-page:local" >/dev/null 2>&1; then
  import_to_k8s_containerd "kerosene/web-page:local"
fi

info "Imported images visible to Kubernetes:"
sudo ctr -n k8s.io images ls | grep -E 'kerosene/(server|kfe-service|mpc-sidecar|tor|web-page)' || true

info "Done. You can now run: infra/kubernetes/scripts/deploy.sh local"
