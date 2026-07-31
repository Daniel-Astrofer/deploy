#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=infra/scripts/polyrepo-env.sh
source "${REPO_ROOT}/infra/scripts/polyrepo-env.sh"
FRONTEND_DIR="${CLIENTS_DIR}"

cd "${FRONTEND_DIR}"

export KEROSENE_ALLOW_DEBUG_RELEASE_SIGNING="${KEROSENE_ALLOW_DEBUG_RELEASE_SIGNING:-true}"
# shellcheck source=infra/kubernetes/scripts/local-host-env.sh
source "${REPO_ROOT}/infra/kubernetes/scripts/local-host-env.sh"
kerosene_load_local_host_env "$REPO_ROOT"
HOST_HOME="$KEROSENE_HOST_HOME"
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
KERO_ANDROID_USE_EXTERNAL_TOR="${KERO_ANDROID_USE_EXTERNAL_TOR:-0}"
KERO_EXTERNAL_TOR_SOCKS_HOST="${KERO_EXTERNAL_TOR_SOCKS_HOST:-127.0.0.1}"
KERO_EXTERNAL_TOR_SOCKS_PORT="${KERO_EXTERNAL_TOR_SOCKS_PORT:-19050}"

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
    echo "Run: bash infra/deploy.sh --wait" >&2
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
  if [[ "$KERO_ANDROID_USE_EXTERNAL_TOR" == "1" ]]; then
    echo "External Tor SOCKS5: ${KERO_EXTERNAL_TOR_SOCKS_HOST}:${KERO_EXTERNAL_TOR_SOCKS_PORT}"
  fi
  exit 0
fi

TOR_DART_DEFINES=()
if [[ "$KERO_ANDROID_USE_EXTERNAL_TOR" == "1" ]]; then
  TOR_DART_DEFINES+=(
    --dart-define="KERO_TOR_SOCKS_HOST=${KERO_EXTERNAL_TOR_SOCKS_HOST}"
    --dart-define="KERO_TOR_SOCKS_PORT=${KERO_EXTERNAL_TOR_SOCKS_PORT}"
  )
fi

KERO_ANDROID_RUN_MODE="${KERO_ANDROID_RUN_MODE:-profile}" # debug|profile|release
KERO_ANDROID_TRACE_SKIA="${KERO_ANDROID_TRACE_SKIA:-0}" # 1 enables --trace-skia (can slow raster a lot)
KERO_ANDROID_VERBOSE="${KERO_ANDROID_VERBOSE:-0}" # 1 enables --verbose
KERO_ANDROID_HWUI_BARS="${KERO_ANDROID_HWUI_BARS:-1}" # 1 enables Android HWUI bars
KERO_ANDROID_SCREEN_CAPTURE_UI="${KERO_ANDROID_SCREEN_CAPTURE_UI:-0}" # 1 shows "DADOS" debug overlay
KERO_ANDROID_PURGE_CACHE="${KERO_ANDROID_PURGE_CACHE:-0}" # 1 enables --purge-persistent-cache
KERO_ANDROID_ENABLE_IMPELLER="${KERO_ANDROID_ENABLE_IMPELLER:-0}" # 1 enables Impeller, 0 forces Skia on Android
# Avoid clashing with primary Linux DDS (9101) when both sessions are up.
KERO_VM_SERVICE_PORT="${KERO_VM_SERVICE_PORT:-9999}"
KERO_DDS_PORT="${KERO_DDS_PORT:-9102}"

if ss -ltn "sport = :${KERO_DDS_PORT}" 2>/dev/null | grep -q LISTEN; then
  echo "[!] DDS port ${KERO_DDS_PORT} already in use." >&2
  echo "[!] Kill the old Android flutter run, or: KERO_DDS_PORT=9103 $0 ..." >&2
  exit 1
fi

flutter_args=(
  "--host-vmservice-port=${KERO_VM_SERVICE_PORT}"
  "--dds-port=${KERO_DDS_PORT}"
  "--disable-service-auth-codes"
)
case "${KERO_ANDROID_RUN_MODE}" in
  debug) flutter_args+=(--debug) ;;
  release) flutter_args+=(--release) ;;
  profile) flutter_args+=(--profile) ;;
  *)
    echo "Invalid KERO_ANDROID_RUN_MODE=${KERO_ANDROID_RUN_MODE} (expected: debug|profile|release)" >&2
    exit 2
    ;;
esac

if [[ "${KERO_ANDROID_TRACE_SKIA}" == "1" && "${KERO_ANDROID_RUN_MODE}" == "profile" ]]; then
  flutter_args+=(--trace-skia)
fi
if [[ "${KERO_ANDROID_VERBOSE}" == "1" ]]; then
  flutter_args+=(--verbose)
fi
if [[ "${KERO_ANDROID_PURGE_CACHE}" == "1" ]]; then
  flutter_args+=(--purge-persistent-cache)
fi

if [[ "${KERO_ANDROID_ENABLE_IMPELLER}" == "1" ]]; then
  flutter_args+=(--enable-impeller)
else
  flutter_args+=(--no-enable-impeller)
fi

extra_defines=()
if [[ "${KERO_ANDROID_SCREEN_CAPTURE_UI}" == "1" ]]; then
  extra_defines+=(--dart-define="SCREEN_CAPTURE_UI=true")
else
  # In debug, the app defaults to showing a "DADOS" overlay for goldens export.
  # Force it off unless explicitly requested so debug UI matches profile/release.
  extra_defines+=(--dart-define="SCREEN_CAPTURE_UI=false")
fi

if [[ "${KERO_ANDROID_HWUI_BARS}" == "1" && -n "${ANDROID_SDK_ROOT:-}" ]]; then
  if command -v adb >/dev/null 2>&1; then
    adb shell setprop debug.hwui.profile visual_bars >/dev/null 2>&1 || true
  fi
fi

echo "Flutter mode: ${KERO_ANDROID_RUN_MODE}"
echo "DevTools/DDS: vm=${KERO_VM_SERVICE_PORT} dds=${KERO_DDS_PORT} (override with KERO_VM_SERVICE_PORT / KERO_DDS_PORT)"
if [[ "${KERO_ANDROID_RUN_MODE}" == "debug" ]]; then
  echo "Hot reload: press 'r' (reload), 'R' (restart) in this terminal."
  echo "Telemetry: DevTools enabled (debug timings can be noisier than profile)."
fi
if [[ "${KERO_ANDROID_RUN_MODE}" == "profile" ]]; then
  echo "Telemetry: DevTools + service protocol enabled (profile mode)."
  if [[ "${KERO_ANDROID_TRACE_SKIA}" == "1" ]]; then
    echo "Telemetry: --trace-skia enabled."
  fi
  if [[ "${KERO_ANDROID_PURGE_CACHE}" == "1" ]]; then
    echo "Telemetry: --purge-persistent-cache enabled."
  fi
  if [[ "${KERO_ANDROID_ENABLE_IMPELLER}" == "1" ]]; then
    echo "Renderer: Impeller enabled."
  else
    echo "Renderer: Impeller disabled (Skia)."
  fi
fi
if [[ "${KERO_ANDROID_HWUI_BARS}" == "1" ]]; then
  echo "Telemetry: Android HWUI visual bars enabled (if supported)."
fi

exec flutter run "${flutter_args[@]}" \
  "${TOR_DART_DEFINES[@]}" \
  "${extra_defines[@]}" \
  --dart-define="KERO_NODE_IS_URL=${KERO_NODE_IS_URL}" \
  --dart-define="KERO_NODE_CH_URL=${KERO_NODE_CH_URL}" \
  --dart-define="KERO_NODE_SG_URL=${KERO_NODE_SG_URL}" \
  --dart-define="PASSKEY_RP_ID=${PASSKEY_RP_ID}" \
  --dart-define="PASSKEY_ORIGIN=${PASSKEY_ORIGIN}" \
  "$@"
