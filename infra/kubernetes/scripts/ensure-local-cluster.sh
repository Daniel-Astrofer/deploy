#!/usr/bin/env bash
set -euo pipefail

# Ensure a local Kubernetes API is reachable for Kerosene local-full deploys.
# Prefer an already configured kubectl context. If none is available and
# KEROSENE_AUTO_CREATE_CLUSTER=1, bootstrap a kind cluster with hostPath mounts
# for local-full persistent data.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$K8S_DIR/../.." && pwd)"

# shellcheck source=infra/kubernetes/scripts/local-host-env.sh
source "$SCRIPT_DIR/local-host-env.sh"
kerosene_load_local_host_env "$REPO_ROOT"
kerosene_prepare_local_host_paths

KUBECTL="${KUBECTL:-kubectl}"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi

KUBERNETES_READY_TIMEOUT="${KEROSENE_KUBERNETES_READY_TIMEOUT:-60}"
KUBERNETES_READY_INTERVAL="${KEROSENE_KUBERNETES_READY_INTERVAL:-2}"

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

cluster_ready() {
  local context
  if ! context="$(kubectl_cmd config current-context 2>/dev/null)"; then
    return 1
  fi
  if ! kubectl_cmd get --raw=/readyz >/dev/null 2>&1; then
    return 1
  fi
  echo "[*] Kubernetes context: $context"
  return 0
}

wait_for_cluster() {
  local deadline now
  deadline=$(( $(date +%s) + KUBERNETES_READY_TIMEOUT ))
  while ! kubectl_cmd get --raw=/readyz >/dev/null 2>&1; do
    now=$(date +%s)
    if (( now >= deadline )); then
      return 1
    fi
    echo "[*] Waiting for Kubernetes API..."
    sleep "$KUBERNETES_READY_INTERVAL"
  done
  return 0
}

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

install_kind_bin() {
  local dest="${KEROSENE_HOST_HOME}/.local/bin/kind"
  local url="${KEROSENE_KIND_DOWNLOAD_URL:-https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64}"

  mkdir -p "$(dirname "$dest")"
  echo "[*] kind not found; downloading $url"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$dest" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$dest" "$url"
  else
    echo "[!] Neither curl nor wget is available to install kind." >&2
    return 1
  fi
  chmod +x "$dest"
  printf '%s\n' "$dest"
}

write_kind_config() {
  local config_path="$1"
  cat >"$config_path" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: ${KEROSENE_KIND_CLUSTER_NAME}
nodes:
  - role: control-plane
    extraMounts:
      - hostPath: ${KEROSENE_REPO_ROOT}/.local
        containerPath: ${KEROSENE_REPO_ROOT}/.local
      - hostPath: ${KEROSENE_HOST_HOME}/.local/state/kerosene
        containerPath: ${KEROSENE_HOST_HOME}/.local/state/kerosene
EOF
}

create_kind_cluster() {
  local kind_bin config_path

  if ! command -v docker >/dev/null 2>&1; then
    echo "[!] Docker is required to create a local kind cluster." >&2
    return 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "[!] Docker daemon is not reachable. Start docker and retry." >&2
    return 1
  fi

  if ! kind_bin="$(resolve_kind_bin)"; then
    kind_bin="$(install_kind_bin)"
  fi

  config_path="$(mktemp "${TMPDIR:-/tmp}/kerosene-kind-XXXXXX.yaml")"
  write_kind_config "$config_path"

  mkdir -p "$(dirname "$KEROSENE_KIND_KUBECONFIG")"
  export KUBECONFIG="$KEROSENE_KIND_KUBECONFIG"
  KUBECTL_ARGS=(--kubeconfig "$KUBECONFIG")

  if "$kind_bin" get clusters 2>/dev/null | grep -qx "$KEROSENE_KIND_CLUSTER_NAME"; then
    echo "[*] kind cluster already exists: $KEROSENE_KIND_CLUSTER_NAME"
    "$kind_bin" export kubeconfig --name "$KEROSENE_KIND_CLUSTER_NAME" --kubeconfig "$KUBECONFIG"
  else
    echo "[*] Creating kind cluster: $KEROSENE_KIND_CLUSTER_NAME"
    echo "[*] Mounting repo data: $KEROSENE_REPO_ROOT/.local"
    echo "[*] Mounting onion keys: $KEROSENE_HOST_HOME/.local/state/kerosene"
    "$kind_bin" create cluster \
      --name "$KEROSENE_KIND_CLUSTER_NAME" \
      --config "$config_path" \
      --kubeconfig "$KUBECONFIG"
  fi
  rm -f "$config_path"

  if ! wait_for_cluster; then
    echo "[!] kind cluster was created but the API did not become ready." >&2
    return 1
  fi

  echo "[+] Local kind cluster ready (kubeconfig: $KUBECONFIG)"
}

if cluster_ready; then
  exit 0
fi

if [[ "${KEROSENE_AUTO_CREATE_CLUSTER}" != "1" ]]; then
  cat >&2 <<'EOF'
[!] Kubernetes API is not reachable or no kubectl context is selected.
    Check the active context:
      kubectl config current-context
    Start or select your local cluster, then retry:
      bash infra/start.sh
    Or allow automatic kind bootstrap:
      KEROSENE_AUTO_CREATE_CLUSTER=1 bash infra/deploy.sh
EOF
  exit 1
fi

echo "[*] No reachable Kubernetes API; bootstrapping local kind cluster"
if ! create_kind_cluster; then
  cat >&2 <<'EOF'
[!] Failed to bootstrap a local Kubernetes cluster with kind.
    Install/start Docker, ensure `kind` is available, or point KUBECONFIG
    at an existing cluster and re-run:
      bash infra/deploy.sh
EOF
  exit 1
fi

if ! cluster_ready; then
  exit 1
fi
