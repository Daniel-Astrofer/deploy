#!/usr/bin/env bash
# Shared host path / kubeconfig resolution for local Kubernetes scripts.
# Source this file; do not execute it directly.
#
# Template manifests still contain the historical workstation paths under
# /home/omega. At render/apply time those markers are rewritten to the active
# host home and repository root (see render-local-full-overlay.sh).

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "local-host-env.sh must be sourced" >&2
  exit 2
fi

kerosene_resolve_repo_root() {
  # Prefer callers that already know the repo root.
  if [[ -n "${KEROSENE_REPO_ROOT:-}" && -d "${KEROSENE_REPO_ROOT}" ]]; then
    cd "${KEROSENE_REPO_ROOT}" && pwd
    return 0
  fi
  local script_dir
  # This file lives at infra/kubernetes/scripts/local-host-env.sh
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/../../.." && pwd
}

kerosene_load_local_host_env() {
  local repo_root="${1:-}"

  if [[ -z "$repo_root" ]]; then
    repo_root="$(kerosene_resolve_repo_root)"
  fi

  export KEROSENE_REPO_ROOT="$repo_root"
  export KEROSENE_HOST_HOME="${KEROSENE_HOST_HOME:-${HOME:-/home/$(id -un)}}"
  export KEROSENE_LEGACY_HOST_HOME="${KEROSENE_LEGACY_HOST_HOME:-/home/omega}"
  export KEROSENE_LEGACY_REPO_ROOT="${KEROSENE_LEGACY_REPO_ROOT:-/home/omega/Kerosene}"

  export KEROSENE_LOCAL_ONION_KEYS_PATH="${KEROSENE_LOCAL_ONION_KEYS_PATH:-$KEROSENE_HOST_HOME/.local/state/kerosene/tor/keys/local-full}"
  export KEROSENE_LOCAL_POSTGRES_DATA="${KEROSENE_LOCAL_POSTGRES_DATA:-$KEROSENE_REPO_ROOT/.local/postgres-data}"
  export KEROSENE_LOCAL_BITCOIN_DATA="${KEROSENE_LOCAL_BITCOIN_DATA:-$KEROSENE_REPO_ROOT/.local/bitcoin-data}"

  export KEROSENE_KIND_CLUSTER_NAME="${KEROSENE_KIND_CLUSTER_NAME:-kerosene-local}"
  export KEROSENE_KIND_KUBECONFIG="${KEROSENE_KIND_KUBECONFIG:-$KEROSENE_HOST_HOME/.kube/kind-config-${KEROSENE_KIND_CLUSTER_NAME}}"
  export KEROSENE_AUTO_CREATE_CLUSTER="${KEROSENE_AUTO_CREATE_CLUSTER:-1}"

  if [[ -z "${KUBECONFIG:-}" ]]; then
    local candidate
    for candidate in \
      "${KEROSENE_DEFAULT_KUBECONFIG:-}" \
      "$KEROSENE_HOST_HOME/.kube/config" \
      "${HOME:-}/.kube/config" \
      "$KEROSENE_KIND_KUBECONFIG"
    do
      if [[ -n "$candidate" && -f "$candidate" ]]; then
        export KUBECONFIG="$candidate"
        break
      fi
    done
  fi
}

kerosene_prepare_local_host_paths() {
  mkdir -p \
    "$KEROSENE_LOCAL_ONION_KEYS_PATH" \
    "$KEROSENE_LOCAL_POSTGRES_DATA" \
    "$KEROSENE_LOCAL_BITCOIN_DATA" \
    "$(dirname "$KEROSENE_KIND_KUBECONFIG")"
}

kerosene_rewrite_legacy_host_paths() {
  # Rewrite historical /home/omega markers inside a text file in-place.
  local file="$1"
  local content
  content="$(<"$file")"
  content="${content//$KEROSENE_LEGACY_REPO_ROOT/$KEROSENE_REPO_ROOT}"
  content="${content//$KEROSENE_LEGACY_HOST_HOME/$KEROSENE_HOST_HOME}"
  printf '%s' "$content" >"$file"
}
