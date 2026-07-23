#!/usr/bin/env bash
set -euo pipefail

# Legacy local.compose / local.kfe overlays (Java vault arm + mpc-sidecar shards) are gone.
# Keep gateway/Tor routing contracts that still apply to the public edge.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NGINX_TEMPLATE="$REPO_ROOT/infra/runtime/web/nginx.compose.conf.template"

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

require_text() {
  local file="$1"
  local text="$2"
  grep -Fq -- "$text" "$file" || fail "$file does not contain: $text"
}

require_text "$NGINX_TEMPLATE" 'location ~ ^/(kfe|api/public/kfe|api/admin/kfe)(/|$)'
require_text "$NGINX_TEMPLATE" 'proxy_pass http://kerosene_kfe;'
require_text "$NGINX_TEMPLATE" 'location ~ ^/(auth|notifications|api|sovereignty|actuator)(/|$)'
require_text "$NGINX_TEMPLATE" 'proxy_pass http://kerosene_core;'

require_text "$REPO_ROOT/infra/runtime/tor/torrc-kfe-is" 'HiddenServicePort 80 10.241.0.20:8080'
require_text "$REPO_ROOT/infra/runtime/tor/torrc-kfe-ch" 'HiddenServicePort 80 10.241.0.21:8080'
require_text "$REPO_ROOT/infra/runtime/tor/torrc-kfe-sg" 'HiddenServicePort 80 10.241.0.22:8080'

echo "[PASS] Gateway KFE/Core routing contracts"
