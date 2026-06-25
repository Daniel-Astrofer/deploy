#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OVERLAY="$K8S_DIR/overlays/local-full"
REPO_ROOT="$(cd "$K8S_DIR/../.." && pwd)"
WEB_NGINX_CONF="$REPO_ROOT/infra/runtime/web/nginx.k8s.conf"
KUSTOMIZE="${KUSTOMIZE:-}"
LOCAL_BROWSER_ORIGINS_CSV="http://localhost:3000,http://localhost:3001,http://localhost:8080,http://localhost:8081,http://localhost:8082,http://localhost:30080,http://localhost:30082,http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:8080,http://127.0.0.1:8081,http://127.0.0.1:8082,http://127.0.0.1:30080,http://127.0.0.1:30082"

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

require '^kind: Namespace$'
require '^  name: kerosene-local$'
require '^kind: Deployment$'
require '^  name: server$'
require '^  name: kfe-service$'
require '^  name: web-page$'
require '^  name: web-page-runtime-config$'
require 'kerosene-runtime-config.json'
require '"apiUrl":"http://127.0.0.1:30082"'
require 'mountPath: /usr/share/nginx/html/kerosene-runtime-config.json'
require '^kind: StatefulSet$'
require '^  name: mpc-sidecar$'
require '^  name: local-postgres$'
require '^  name: local-redis$'
require '^  name: local-vault$'
require '^  name: local-bitcoin$'
require '^  name: local-lnd-placeholder$'
require 'image: localhost:5000/kerosene/kfe-service:local'
require "APP_CORS_ALLOWED_ORIGINS: $LOCAL_BROWSER_ORIGINS_CSV"
require "WEBAUTHN_ORIGINS: android:apk-key-hash:kerosene,$LOCAL_BROWSER_ORIGINS_CSV"
require 'SPRING_PROFILES_ACTIVE: docker,kfe'
require 'KEROSENE_RUNTIME_ROLE: kfe-service'
require 'BITCOIN_NETWORK: regtest'
require 'BITCOIN_RPC_REQUIRED: "false"'
require 'LIGHTNING_LND_ENABLED: "false"'
require 'kfe-internal-shared-secret: local-kfe-internal-secret-not-for-production'
require 'nodePort: 30080'
require 'nodePort: 30081'
require 'nodePort: 30082'

if [[ ! -f "$WEB_NGINX_CONF" ]]; then
  echo "[!] Kubernetes web-page Nginx config not found: $WEB_NGINX_CONF" >&2
  exit 1
fi
for origin in \
  'http://localhost:3000' \
  'http://127.0.0.1:3000' \
  'http://localhost:3001' \
  'http://127.0.0.1:3001' \
  'http://localhost:8080' \
  'http://127.0.0.1:8080' \
  'http://localhost:8081' \
  'http://127.0.0.1:8081' \
  'http://localhost:8082' \
  'http://127.0.0.1:8082' \
  'http://localhost:30080' \
  'http://127.0.0.1:30080' \
  'http://localhost:30082' \
  'http://127.0.0.1:30082'; do
  if ! grep -Fq "$origin" "$WEB_NGINX_CONF"; then
    echo "[!] web-page CSP connect-src does not allow local origin: $origin" >&2
    exit 1
  fi
done

if grep -q 'kerosene-app' "$rendered"; then
  echo "[!] Render still contains old workload name kerosene-app" >&2
  exit 1
fi
if grep -q 'web-admin' "$rendered"; then
  echo "[!] Render still contains old workload name web-admin" >&2
  exit 1
fi

echo "[+] local-full overlay renders successfully."
echo "[+] Expected access: server NodePort 30080, mpc health NodePort 30081, web-page NodePort 30082."
echo "[+] web-page routes /kfe, /api/public/kfe and /api/admin/kfe to kfe-service."
