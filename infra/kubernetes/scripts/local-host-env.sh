#!/usr/bin/env bash
# Shared host path / kubeconfig resolution for local Kubernetes scripts.
# Source this file; do not execute it directly.
#
# Template manifests may contain explicit host/repository markers. At
# render/apply time those markers are rewritten to the active workspace.

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
  export KEROSENE_LEGACY_HOST_HOME="${KEROSENE_LEGACY_HOST_HOME:-__KEROSENE_HOST_HOME__}"
  export KEROSENE_LEGACY_REPO_ROOT="${KEROSENE_LEGACY_REPO_ROOT:-__KEROSENE_REPO_ROOT__}"

  export KEROSENE_LOCAL_STATE_ROOT="${KEROSENE_LOCAL_STATE_ROOT:-$KEROSENE_HOST_HOME/.local/state/kerosene}"
  export KEROSENE_LOCAL_ONION_KEYS_PATH="${KEROSENE_LOCAL_ONION_KEYS_PATH:-$KEROSENE_LOCAL_STATE_ROOT/tor/keys/local-full}"
  export KEROSENE_LOCAL_POSTGRES_DATA="${KEROSENE_LOCAL_POSTGRES_DATA:-$KEROSENE_LOCAL_STATE_ROOT/postgres-data}"
  # Prefer the mounted 500 GB HDD for the chain when it is present. An
  # explicit KEROSENE_LOCAL_BITCOIN_DATA always wins, which keeps this portable
  # across hosts with a different mount layout.
  if [[ -z "${KEROSENE_LOCAL_BITCOIN_DATA:-}" ]]; then
    local bitcoin_hdd_mount="${KEROSENE_BITCOIN_HDD_MOUNT:-/mnt/hd_500gb_1}"
    if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "$bitcoin_hdd_mount"; then
      export KEROSENE_LOCAL_BITCOIN_DATA="$bitcoin_hdd_mount/kerosene/bitcoin-data"
    else
      export KEROSENE_LOCAL_BITCOIN_DATA="$KEROSENE_LOCAL_STATE_ROOT/bitcoin-data"
    fi
  fi
  export KEROSENE_LOCAL_LND_DATA="${KEROSENE_LOCAL_LND_DATA:-$KEROSENE_LOCAL_STATE_ROOT/lnd-data}"
  export KEROSENE_LOCAL_LND_PEER_DATA="${KEROSENE_LOCAL_LND_PEER_DATA:-$KEROSENE_LOCAL_STATE_ROOT/lnd-peer-data}"

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
    "$KEROSENE_LOCAL_LND_DATA" \
    "$KEROSENE_LOCAL_LND_PEER_DATA" \
    "$(dirname "$KEROSENE_KIND_KUBECONFIG")"
}

kerosene_rewrite_legacy_host_paths() {
  # Rewrite explicit workspace markers inside a text file in-place.
  local file="$1"
  local content
  content="$(<"$file")"
  content="${content//$KEROSENE_LEGACY_REPO_ROOT/$KEROSENE_REPO_ROOT}"
  content="${content//$KEROSENE_LEGACY_HOST_HOME/$KEROSENE_HOST_HOME}"
  content="${content//__KEROSENE_LOCAL_ONION_KEYS_PATH__/$KEROSENE_LOCAL_ONION_KEYS_PATH}"
  content="${content//__KEROSENE_LOCAL_POSTGRES_DATA__/$KEROSENE_LOCAL_POSTGRES_DATA}"
  content="${content//__KEROSENE_LOCAL_BITCOIN_DATA__/$KEROSENE_LOCAL_BITCOIN_DATA}"
  content="${content//__KEROSENE_LOCAL_LND_DATA__/$KEROSENE_LOCAL_LND_DATA}"
  content="${content//__KEROSENE_LOCAL_LND_PEER_DATA__/$KEROSENE_LOCAL_LND_PEER_DATA}"
  content="${content//__KEROSENE_LOCAL_STATE_ROOT__/$KEROSENE_LOCAL_STATE_ROOT}"
  printf '%s' "$content" >"$file"
}
