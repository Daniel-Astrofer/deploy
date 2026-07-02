#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OVERLAY="$K8S_DIR/overlays/local-full"
REPO_ROOT="$(cd "$K8S_DIR/../.." && pwd)"
WEB_NGINX_CONF="$REPO_ROOT/infra/runtime/web/nginx.k8s.conf"
KUSTOMIZE="${KUSTOMIZE:-}"

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
trap 'rm -f "$rendered"' EXIT

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
require 'path: /home/omega/.local/state/kerosene/tor/keys/local-full'
require 'claimName: tor-onion-keys'
require 'local-full-allow-tor-egress'
require '^  name: web-page-runtime-config$'
require 'kerosene-runtime-config.json'
require '"access":"tor-hidden-service-only"'
require 'mountPath: /usr/share/nginx/html/kerosene-runtime-config.json'
require '^kind: StatefulSet$'
require '^  name: mpc-sidecar$'
require '^  name: local-postgres$'
require '^  name: local-redis$'
require '^  name: local-vault$'
require '^  name: local-bitcoin$'
require_literal 'wallet="${BITCOIN_RPC_WALLET:-kerosene}"'
require_literal 'loadwallet "$wallet"'
require_literal 'createwallet "$wallet" false false'
require_literal '-rpcwallet="$wallet" getwalletinfo'
require '^  name: local-lnd-placeholder$'
require 'image: localhost:5000/kerosene/kfe-service:local'
require 'APP_CORS_ALLOWED_ORIGINS: http://placeholder.onion'
require 'WEBAUTHN_ORIGINS: android:apk-key-hash:kerosene,http://placeholder.onion'
require 'SPRING_PROFILES_ACTIVE: docker,kfe'
require 'KEROSENE_RUNTIME_ROLE: kfe-service'
require 'BITCOIN_NETWORK: testnet4'
require 'BITCOIN_RPC_REQUIRED: "false"'
require 'LIGHTNING_LND_ENABLED: "false"'
require 'kfe-internal-shared-secret: local-kfe-internal-secret-not-for-production'

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

echo "[+] local-full overlay renders successfully."
echo "[+] Expected access: Tor hidden service only."
echo "[+] web-page routes /kfe, /api/public/kfe and /api/admin/kfe to kfe-service."
echo "[+] tor-onion publishes the web-page gateway as a Tor hidden service."
