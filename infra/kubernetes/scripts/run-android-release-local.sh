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
if [[ -z "${KUBECONFIG:-}" && -f "$HOST_HOME/.kube/config" ]]; then
  export KUBECONFIG="$HOST_HOME/.kube/config"
fi
PASSKEY_RP_ID="${PASSKEY_RP_ID:-${FRONTEND_PASSKEY_RP_ID:-kerosene-device}}"
PASSKEY_ORIGIN="${PASSKEY_ORIGIN:-${FRONTEND_PASSKEY_ORIGIN:-android:apk-key-hash:kerosene}}"
KUBECTL="${KUBECTL:-kubectl}"
ADB="${ADB:-adb}"
KEROSENE_NAMESPACE="${KEROSENE_NAMESPACE:-kerosene-local}"
KERO_ANDROID_USE_HOST_TOR="${KERO_ANDROID_USE_HOST_TOR:-1}"
KERO_HOST_TOR_SOCKS_HOST="${KERO_HOST_TOR_SOCKS_HOST:-127.0.0.1}"
KERO_HOST_TOR_SOCKS_PORT="${KERO_HOST_TOR_SOCKS_PORT:-19050}"
KERO_HOST_TOR_STATE_DIR="${KERO_HOST_TOR_STATE_DIR:-$HOST_HOME/.local/state/kerosene/tor/client-check}"
KERO_ANDROID_SKIP_HOST_TOR_PROBE="${KERO_ANDROID_SKIP_HOST_TOR_PROBE:-0}"

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

host_port_is_listening() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | grep -q "${KERO_HOST_TOR_SOCKS_HOST}:${KERO_HOST_TOR_SOCKS_PORT}"
    return
  fi
  (echo >"/dev/tcp/${KERO_HOST_TOR_SOCKS_HOST}/${KERO_HOST_TOR_SOCKS_PORT}") >/dev/null 2>&1
}

wait_for_host_tor_socks() {
  local deadline=$((SECONDS + 60))
  while (( SECONDS < deadline )); do
    if host_port_is_listening; then
      return 0
    fi
    sleep 1
  done
  return 1
}

ensure_host_tor_socks() {
  if host_port_is_listening; then
    echo "Host Tor SOCKS5 already listening: ${KERO_HOST_TOR_SOCKS_HOST}:${KERO_HOST_TOR_SOCKS_PORT}"
    return 0
  fi

  if ! command -v tor >/dev/null 2>&1; then
    echo "tor is not installed; set KERO_ANDROID_USE_HOST_TOR=0 to use embedded mobile Tor." >&2
    return 1
  fi

  mkdir -p "$KERO_HOST_TOR_STATE_DIR"
  touch "$KERO_HOST_TOR_STATE_DIR/torrc.empty"
  echo "Starting host Tor SOCKS5 on ${KERO_HOST_TOR_SOCKS_HOST}:${KERO_HOST_TOR_SOCKS_PORT}"
  tor -f "$KERO_HOST_TOR_STATE_DIR/torrc.empty" \
    --RunAsDaemon 1 \
    --SocksPort "${KERO_HOST_TOR_SOCKS_HOST}:${KERO_HOST_TOR_SOCKS_PORT}" \
    --DataDirectory "$KERO_HOST_TOR_STATE_DIR" \
    --PidFile "$KERO_HOST_TOR_STATE_DIR/tor.pid" \
    --Log "notice file $KERO_HOST_TOR_STATE_DIR/tor.log" >/dev/null 2>&1

  if ! wait_for_host_tor_socks; then
    echo "Host Tor SOCKS5 did not start. Check: $KERO_HOST_TOR_STATE_DIR/tor.log" >&2
    return 1
  fi
}

configure_android_host_tor_reverse() {
  if ! command -v "$ADB" >/dev/null 2>&1; then
    echo "adb is not available; set KERO_ANDROID_USE_HOST_TOR=0 to use embedded mobile Tor." >&2
    return 1
  fi

  "$ADB" reverse \
    "tcp:${KERO_HOST_TOR_SOCKS_PORT}" \
    "tcp:${KERO_HOST_TOR_SOCKS_PORT}" >/dev/null
  echo "ADB reverse active: device 127.0.0.1:${KERO_HOST_TOR_SOCKS_PORT} -> host ${KERO_HOST_TOR_SOCKS_HOST}:${KERO_HOST_TOR_SOCKS_PORT}"
}

probe_host_tor_onion() {
  if [[ "$KERO_ANDROID_SKIP_HOST_TOR_PROBE" == "1" ]]; then
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is not available; skipping host Tor onion probe." >&2
    return 0
  fi

  local health_url="${KERO_NODE_IS_URL%/}/health/ready"
  local status
  status="$(curl --socks5-hostname "${KERO_HOST_TOR_SOCKS_HOST}:${KERO_HOST_TOR_SOCKS_PORT}" \
    --connect-timeout 20 \
    --max-time 60 \
    -sS \
    -o /dev/null \
    -w '%{http_code}' \
    "$health_url")"
  if [[ "$status" != "200" && "$status" != "503" ]]; then
    echo "Host Tor could not reach $health_url through SOCKS5; HTTP status: $status" >&2
    return 1
  fi
  echo "Host Tor onion probe OK: $health_url -> $status"
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
  if [[ "$KERO_ANDROID_USE_HOST_TOR" == "1" ]]; then
    echo "Tor SOCKS5 via adb reverse: 127.0.0.1:${KERO_HOST_TOR_SOCKS_PORT}"
  fi
  exit 0
fi

TOR_DART_DEFINES=()
if [[ "$KERO_ANDROID_USE_HOST_TOR" == "1" ]]; then
  ensure_host_tor_socks
  probe_host_tor_onion
  configure_android_host_tor_reverse
  TOR_DART_DEFINES+=(
    --dart-define="KERO_TOR_SOCKS_HOST=127.0.0.1"
    --dart-define="KERO_TOR_SOCKS_PORT=${KERO_HOST_TOR_SOCKS_PORT}"
  )
fi

exec flutter run --release \
  "${TOR_DART_DEFINES[@]}" \
  --dart-define="KERO_NODE_IS_URL=${KERO_NODE_IS_URL}" \
  --dart-define="KERO_NODE_CH_URL=${KERO_NODE_CH_URL}" \
  --dart-define="KERO_NODE_SG_URL=${KERO_NODE_SG_URL}" \
  --dart-define="PASSKEY_RP_ID=${PASSKEY_RP_ID}" \
  --dart-define="PASSKEY_ORIGIN=${PASSKEY_ORIGIN}" \
  "$@"
