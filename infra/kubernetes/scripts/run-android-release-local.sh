#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/frontend"

cd "${FRONTEND_DIR}"

export KEROSENE_ALLOW_DEBUG_RELEASE_SIGNING="${KEROSENE_ALLOW_DEBUG_RELEASE_SIGNING:-true}"
HOST_HOME="${KEROSENE_HOST_HOME:-/home/omega}"
if [[ -z "${ANDROID_HOME:-}" && -d "$HOST_HOME/Android/Sdk" ]]; then
  export ANDROID_HOME="$HOST_HOME/Android/Sdk"
fi
if [[ -z "${ANDROID_SDK_ROOT:-}" && -n "${ANDROID_HOME:-}" ]]; then
  export ANDROID_SDK_ROOT="$ANDROID_HOME"
fi
if [[ -z "${CARGO_HOME:-}" && -d "$HOST_HOME/.cargo" ]]; then
  export CARGO_HOME="$HOST_HOME/.cargo"
fi
if [[ -z "${RUSTUP_HOME:-}" && -d "$HOST_HOME/.rustup" ]]; then
  export RUSTUP_HOME="$HOST_HOME/.rustup"
fi
if [[ -n "${CARGO_HOME:-}" ]]; then
  export PATH="$CARGO_HOME/bin:$PATH"
fi
if [[ -z "${ADB_VENDOR_KEYS:-}" && -f "$HOST_HOME/.android/adbkey" ]]; then
  export ADB_VENDOR_KEYS="$HOST_HOME/.android/adbkey"
fi
PASSKEY_RP_ID="${PASSKEY_RP_ID:-${FRONTEND_PASSKEY_RP_ID:-kerosene-device}}"
PASSKEY_ORIGIN="${PASSKEY_ORIGIN:-${FRONTEND_PASSKEY_ORIGIN:-android:apk-key-hash:kerosene}}"
KUBECTL="${KUBECTL:-kubectl}"
KEROSENE_NAMESPACE="${KEROSENE_NAMESPACE:-kerosene-local}"

kubectl_args=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  kubectl_args+=(--kubeconfig "$KUBECONFIG")
fi

normalize_onion_url() {
  local raw="$1"
  raw="${raw//$'\r'/}"
  raw="${raw//$'\n'/}"
  raw="${raw#"${raw%%[![:space:]]*}"}"
  raw="${raw%"${raw##*[![:space:]]}"}"
  if [[ -z "$raw" ]]; then
    echo "empty onion URL" >&2
    return 1
  fi
  if [[ "$raw" != http://* && "$raw" != https://* ]]; then
    raw="http://$raw"
  fi
  raw="${raw%/}"

  local host_port="${raw#http://}"
  host_port="${host_port#https://}"
  host_port="${host_port%%/*}"
  local host="${host_port%%:*}"
  if [[ ! "$host" =~ ^[a-z2-7]{56}\.onion$ ]]; then
    echo "expected a Tor v3 .onion URL, got: $raw" >&2
    return 1
  fi
  printf '%s\n' "$raw"
}

discover_kubernetes_onion_url() {
  local hostname
  hostname="$("$KUBECTL" "${kubectl_args[@]}" -n "$KEROSENE_NAMESPACE" \
    exec deploy/tor-onion -- sh -c 'cat /var/lib/tor/kerosene_service/hostname' 2>/dev/null || true)"
  if [[ -z "$hostname" ]]; then
    echo "Could not read Kubernetes Tor hostname from deployment/tor-onion in namespace $KEROSENE_NAMESPACE." >&2
    echo "Run: KUBECONFIG=/home/omega/.kube/config infra/kubernetes/scripts/deploy-local-full.sh --wait" >&2
    return 1
  fi
  normalize_onion_url "$hostname"
}

resolve_node_url() {
  local explicit="$1"
  if [[ -n "$explicit" ]]; then
    normalize_onion_url "$explicit"
    return
  fi
  if [[ -n "${KERO_NODE_ONION_URL:-}" ]]; then
    normalize_onion_url "$KERO_NODE_ONION_URL"
    return
  fi
  discover_kubernetes_onion_url
}

KERO_NODE_IS_URL="$(resolve_node_url "${KERO_NODE_IS_URL:-}")"
KERO_NODE_CH_URL="$(resolve_node_url "${KERO_NODE_CH_URL:-$KERO_NODE_IS_URL}")"
KERO_NODE_SG_URL="$(resolve_node_url "${KERO_NODE_SG_URL:-$KERO_NODE_IS_URL}")"

echo "Running local Android release with debug signing enabled."
echo "This is for local non-production device testing only."
echo "Passkey RP ID: ${PASSKEY_RP_ID}"
echo "Tor Node IS URL: ${KERO_NODE_IS_URL}"

if [[ "${KERO_ANDROID_PRINT_CONFIG_ONLY:-0}" == "1" ]]; then
  echo "Tor Node CH URL: ${KERO_NODE_CH_URL}"
  echo "Tor Node SG URL: ${KERO_NODE_SG_URL}"
  exit 0
fi

exec flutter run --release \
  --dart-define="KERO_NODE_IS_URL=${KERO_NODE_IS_URL}" \
  --dart-define="KERO_NODE_CH_URL=${KERO_NODE_CH_URL}" \
  --dart-define="KERO_NODE_SG_URL=${KERO_NODE_SG_URL}" \
  --dart-define="PASSKEY_RP_ID=${PASSKEY_RP_ID}" \
  --dart-define="PASSKEY_ORIGIN=${PASSKEY_ORIGIN}" \
  "$@"
