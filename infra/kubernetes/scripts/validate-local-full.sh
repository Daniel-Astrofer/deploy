#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$K8S_DIR/../.." && pwd)"
WEB_NGINX_CONF="$REPO_ROOT/infra/runtime/web/nginx.k8s.conf"
KUSTOMIZE="${KUSTOMIZE:-}"
RENDER_ROOT=""
OVERLAY=""

# shellcheck source=infra/kubernetes/scripts/local-host-env.sh
source "$SCRIPT_DIR/local-host-env.sh"
kerosene_load_local_host_env "$REPO_ROOT"

cleanup_validate() {
  if [[ -n "$RENDER_ROOT" && -d "$RENDER_ROOT" ]]; then
    rm -rf "$RENDER_ROOT"
  fi
  if [[ -n "${rendered:-}" && -f "$rendered" ]]; then
    rm -f "$rendered"
  fi
}
trap cleanup_validate EXIT

RENDER_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/kerosene-local-full-validate-XXXXXX")"
OVERLAY="$(
  KEROSENE_RENDER_ROOT="$RENDER_ROOT" \
    KEROSENE_HOST_HOME="$KEROSENE_HOST_HOME" \
    KEROSENE_REPO_ROOT="$KEROSENE_REPO_ROOT" \
    bash "$SCRIPT_DIR/render-local-full-overlay.sh"
)"

if [[ ! -d "$OVERLAY" ]]; then
  echo "[!] local-full overlay not found: $OVERLAY" >&2
  exit 1
fi

build_overlay() {
  if [[ -n "$KUSTOMIZE" ]]; then
    "$KUSTOMIZE" build "$OVERLAY"
  elif command -v kustomize >/dev/null 2>&1; then
    kustomize build "$OVERLAY"
  else
    kubectl kustomize "$OVERLAY"
  fi
}

rendered="$(mktemp "${TMPDIR:-/tmp}/kerosene-local-full.XXXXXX.yaml")"

build_overlay > "$rendered"

require() {
  local pattern="$1"
  if ! grep -qE "$pattern" "$rendered"; then
    echo "[!] Render does not contain required pattern: $pattern" >&2
    exit 1
  fi
}

require_literal() {
  local needle="$1"
  if ! grep -Fq -- "$needle" "$rendered"; then
    echo "[!] Render does not contain required text: $needle" >&2
    exit 1
  fi
}

require '^kind: Namespace$'
require '^  name: kerosene-local$'
require '^kind: Deployment$'
require '^  name: server$'
require '^  name: kfe-service$'
require '^  name: web-page$'
require '^  name: tor-onion$'
require '^  name: tor-onion-entrypoint$'
require '^  name: tor-onion-config$'
require 'HiddenServicePort 80 web-page:8080'
require 'image: kerosene/tor:local'
require 'runAsUser: 1000'
require 'runAsGroup: 1000'
require 'name: prepare-tor-data-dir'
require 'chown -R 1000:1000 /var/lib/tor'
require '^kind: PersistentVolume$'
require '^  name: kerosene-local-tor-onion-keys$'
require '^kind: PersistentVolumeClaim$'
require '^  name: tor-onion-keys$'
require 'mountPath: /var/lib/tor'
require 'mountPath: /var/lib/tor/kerosene_service'
require 'name: tor-data'
require_literal "path: $KEROSENE_LOCAL_ONION_KEYS_PATH"
require_literal "path: $KEROSENE_LOCAL_POSTGRES_DATA"
require_literal "path: $KEROSENE_LOCAL_BITCOIN_DATA"
require 'claimName: tor-onion-keys'
require 'local-full-allow-tor-egress'
require '^  name: web-page-runtime-config$'
require 'kerosene-runtime-config.json'
require '"access":"tor-hidden-service-only"'
require 'mountPath: /usr/share/nginx/html/kerosene-runtime-config.json'
require '^  name: vault-1$'
require '^  name: vault-mesh-lab$'
require '^  name: local-postgres$'
require '^  name: local-redis$'
require '^  name: local-bitcoin$'
require_literal 'wallet="${BITCOIN_RPC_WALLET:-kerosene}"'
require_literal 'loadwallet "$wallet"'
require_literal 'createwallet "$wallet" false false'
require_literal '-rpcwallet="$wallet" getwalletinfo'
require '^  name: local-lnd$'
require '^  name: local-lnd-peer$'
require 'image: lightninglabs/lnd:v0.20.1-beta'
require 'image: localhost:5000/kerosene/kfe-service:local'
require 'APP_CORS_ALLOWED_ORIGINS: http://placeholder.onion'
require 'WEBAUTHN_ORIGINS: android:apk-key-hash:kerosene,http://placeholder.onion'
require 'SPRING_PROFILES_ACTIVE: docker,kfe'
require 'KEROSENE_RUNTIME_ROLE: kfe-service'
require 'BITCOIN_NETWORK: testnet3'
require 'KFE_VAULTMESH_ENABLED: "true"'
require 'KFE_VAULTMESH_MESH_ONLY: "true"'
require 'KFE_VAULTMESH_SUBMIT_ON_OUTBOUND: "true"'
require 'KFE_VAULTMESH_BASE_URL: http://vault-1:7701'
require 'KFE_VAULTMESH_API_TOKEN: kerosene-vault-lab-only'
require 'KFE_VAULTMESH_DAY_ROTATION_ENABLED: "true"'
require 'KFE_MPC_SIGNING_ENABLED: "false"'
require 'API_SECRET_AES_SECRET: YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE='
# Beta testnet: Bitcoin Core is required so financial rails fail closed when down.
require 'BITCOIN_RPC_REQUIRED: "true"'
require 'KFE_NETWORK_MONITOR_ENABLED: "true"'
require 'LIGHTNING_LND_ENABLED: "true"'
require 'LIGHTNING_LND_REST_ENABLED: "true"'
require 'LIGHTNING_LND_BASE_URL: https://kerosene-lnd-headless:8080'
require 'kfe-internal-shared-secret: local-kfe-internal-secret-not-for-production'

# Legacy signing path must not appear in local-full / deploy.
if grep -qE '^  name: mpc-sidecar$' "$rendered"; then
  echo "[!] local-full must not include mpc-sidecar (vault mesh cutover)." >&2
  exit 1
fi
if grep -qE '^  name: local-vault$' "$rendered"; then
  echo "[!] local-full must not include HashiCorp local-vault (wallet-arming removed)." >&2
  exit 1
fi

hidden_service_ports="$(grep -E '^[[:space:]]*HiddenServicePort[[:space:]]+' "$rendered" || true)"
hidden_service_port_count="$(printf '%s\n' "$hidden_service_ports" | sed '/^$/d' | wc -l | tr -d '[:space:]')"
if [[ "$hidden_service_port_count" != "1" ]]; then
  echo "[!] local-full must expose exactly one Tor hidden service port." >&2
  printf '%s\n' "$hidden_service_ports" >&2
  exit 1
fi
if ! printf '%s\n' "$hidden_service_ports" | grep -qxE '[[:space:]]*HiddenServicePort 80 web-page:8080'; then
  echo "[!] local-full Tor hidden service must publish only web-page on onion port 80." >&2
  printf '%s\n' "$hidden_service_ports" >&2
  exit 1
fi
if grep -qE 'HiddenServicePort[[:space:]]+[0-9]+[[:space:]]+server:8080' "$rendered"; then
  echo "[!] local-full must not publish server directly through Tor; route through web-page." >&2
  exit 1
fi

if grep -qE '^  type: (NodePort|LoadBalancer)$' "$rendered"; then
  echo "[!] local-full must not expose services via clear net." >&2
  exit 1
fi
if grep -q 'nodePort:' "$rendered"; then
  echo "[!] local-full render contains nodePort." >&2
  exit 1
fi

if grep -q 'kerosene-app' "$rendered"; then
  echo "[!] Render still contains old workload name kerosene-app" >&2
  exit 1
fi
if grep -q 'web-admin' "$rendered"; then
  echo "[!] Render still contains old workload name web-admin" >&2
  exit 1
fi
if grep -q 'local-full-allow-nodeport-ingress' "$rendered"; then
  echo "[!] Render still contains old local-full NodePort policy name." >&2
  exit 1
fi

echo "[+] local-full overlay renders successfully."
echo "[+] Expected access: Tor hidden service only."
echo "[+] web-page routes all public KFE API namespaces through the Auth gateway."
echo "[+] tor-onion publishes the web-page gateway as a Tor hidden service."
