#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_COMPOSE="$REPO_ROOT/infra/docker/compose/local.compose.yaml"
LIMITS_COMPOSE="$REPO_ROOT/infra/docker/compose/local.limits.compose.yaml"
KFE_COMPOSE="$REPO_ROOT/infra/docker/compose/local.kfe.compose.yaml"
ENV_FILE="$REPO_ROOT/backend/kerosene/.env.example"
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

command -v docker >/dev/null 2>&1 || fail "docker CLI is required"

umask 077
rendered="$(mktemp "${TMPDIR:-/tmp}/kerosene-compose-kfe.XXXXXX.yaml")"
trap 'rm -f "$rendered"' EXIT

docker compose \
  --env-file "$ENV_FILE" \
  -f "$LOCAL_COMPOSE" \
  -f "$LIMITS_COMPOSE" \
  -f "$KFE_COMPOSE" \
  config --no-env-resolution --output "$rendered"

for shard in wvo iw5 ltv; do
  require_text "$rendered" "api-gateway-$shard:"
  require_text "$rendered" "kfe-service-$shard:"
  require_text "$rendered" "AUTH_REMOTE_BASE_URL: http://server-$shard:8080"
  require_text "$rendered" "KFE_REMOTE_BASE_URL: http://kfe-service-$shard:8080"
  require_text "$rendered" "KEROSENE_KFE_UPSTREAM: kfe-service-$shard:8080"
done

require_text "$rendered" "KFE_INTERNAL_SHARED_SECRET:"
require_text "$rendered" "torrc-kfe-is"
require_text "$rendered" "torrc-kfe-ch"
require_text "$rendered" "torrc-kfe-sg"

if grep -Fq 'kfe-split' "$rendered"; then
  fail "KFE services must start whenever the KFE overlay is included"
fi

require_text "$NGINX_TEMPLATE" 'location ~ ^/(kfe|api/public/kfe|api/admin/kfe)(/|$)'
require_text "$NGINX_TEMPLATE" 'proxy_pass http://kerosene_kfe;'
require_text "$NGINX_TEMPLATE" 'location ~ ^/(auth|notifications|api|sovereignty|actuator)(/|$)'
require_text "$NGINX_TEMPLATE" 'proxy_pass http://kerosene_core;'

require_text "$REPO_ROOT/infra/runtime/tor/torrc-kfe-is" 'HiddenServicePort 80 10.241.0.20:8080'
require_text "$REPO_ROOT/infra/runtime/tor/torrc-kfe-ch" 'HiddenServicePort 80 10.241.0.21:8080'
require_text "$REPO_ROOT/infra/runtime/tor/torrc-kfe-sg" 'HiddenServicePort 80 10.241.0.22:8080'

echo "[PASS] Compose KFE split routing"
