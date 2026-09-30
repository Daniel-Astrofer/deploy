#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEPLOY="$REPO_ROOT/infra/kubernetes/scripts/deploy.sh"
TMP_DIR="$(mktemp -d)"
FAKE_KUBECTL_LOG="$TMP_DIR/kubectl.log"
FAKE_KUSTOMIZE_LOG="$TMP_DIR/kustomize.log"

SERVER_IMAGE="registry.example/kerosene/server@sha256:1111111111111111111111111111111111111111111111111111111111111111"
KFE_SERVICE_IMAGE="registry.example/kerosene/kfe-service@sha256:2222222222222222222222222222222222222222222222222222222222222222"
WEB_PAGE_IMAGE="registry.example/kerosene/web-page@sha256:3333333333333333333333333333333333333333333333333333333333333333"
NODE_IMAGE="registry.example/kerosene/node@sha256:4444444444444444444444444444444444444444444444444444444444444444"
TOR_IMAGE="registry.example/kerosene/tor@sha256:5555555555555555555555555555555555555555555555555555555555555555"
POSTGRES_IMAGE="registry.example/postgres@sha256:6666666666666666666666666666666666666666666666666666666666666666"
REDIS_IMAGE="registry.example/redis@sha256:7777777777777777777777777777777777777777777777777777777777777777"
BITCOIN_IMAGE="registry.example/bitcoin@sha256:8888888888888888888888888888888888888888888888888888888888888888"
LND_IMAGE="registry.example/lnd@sha256:9999999999999999999999999999999999999999999999999999999999999999"
MISMATCHED_SERVER_IMAGE="registry.example/kerosene/server@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  [[ "$haystack" == *"$needle"* ]] || fail "Expected output to contain: $needle"
}

mkdir -p "$TMP_DIR/bin"

cat > "$TMP_DIR/bin/kustomize" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "edit" && "${2:-}" == "set" && "${3:-}" == "image" && -n "${4:-}" ]]; then
  printf '%s\n' "$*" >> "$FAKE_KUSTOMIZE_LOG"
  exit 0
fi

echo "unexpected kustomize call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kustomize"

cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >> "$FAKE_KUBECTL_LOG"

emit_deployment() {
  local name="$1"
  local image="$2"
  cat <<YAML
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${name}
spec:
  template:
    spec:
      containers:
      - name: ${name}
        image: ${image}
YAML
}

emit_statefulset() {
  local name="$1"
  shift
  cat <<YAML
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${name}
spec:
  volumeClaimTemplates:
  - metadata:
      name: data
  template:
    spec:
      containers:
YAML
  local image
  for image in "$@"; do
    cat <<YAML
      - name: runtime
        image: ${image}
YAML
  done
}

emit_manifest() {
  cat <<'YAML'
---
apiVersion: v1
kind: Namespace
metadata:
  name: kerosene-staging
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: staging-runtime-gates
data:
  torrc: |
    HiddenServicePort 80 web-page:8080
    HiddenServicePort 8800 127.0.0.1:8800
  BITCOIN_NETWORK: testnet3
YAML
  emit_deployment server "$SERVER_IMAGE"
  emit_deployment kfe-service "$KFE_SERVICE_IMAGE"
  emit_deployment web-page "$WEB_PAGE_IMAGE"
  emit_statefulset staging-postgres "$POSTGRES_IMAGE"
  emit_statefulset staging-redis "$REDIS_IMAGE"
  emit_statefulset staging-bitcoin "$BITCOIN_IMAGE"
  emit_statefulset staging-lnd "$LND_IMAGE"
  emit_statefulset staging-tor "$TOR_IMAGE" "$NODE_IMAGE"
  cat <<YAML
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: staged-node-settings
spec:
  template:
    spec:
      containers:
      - name: settings
        image: ${NODE_IMAGE}
        env:
        - name: KEROSENE_DISCOVERY_PLANE
          value: bank
        - name: KEROSENE_NODE_ONION_HOSTNAME_PATH
          value: /var/run/tor/node/hostname
        - name: KFE_VAULTMESH_TRANSPORT
          value: tor
        - name: KFE_KEROSENE_NODE_TRANSPORT
          value: tor
YAML
}

secret_keys() {
  case "$1" in
    server-secrets)
      printf '%s\n' jwt-secret password-pepper aes-secret kfe-column-crypto-key shard-attestation-secret kfe-internal-shared-secret
      ;;
    kerosene-db-secrets)
      printf '%s\n' jdbc-url application-user application-password
      ;;
    kerosene-redis-secrets) printf '%s\n' redis-password ;;
    kerosene-bitcoin-secrets) printf '%s\n' rpc-user rpc-password ;;
    kerosene-lnd-secrets) printf '%s\n' LIGHTNING_LND_MACAROON ;;
    staging-smoke-credentials) printf '%s\n' username password ;;
    kerosene-node-genesis) printf '%s\n' genesis-trust-bundle.json ;;
    kerosene-node-mtls) printf '%s\n' ca.crt server.crt server.key client-identity.pem client.crt client.pkcs8.key ;;
    kerosene-node-identity) printf '%s\n' identity.key member-id ;;
    *) echo "unexpected secret: $1" >&2; exit 42 ;;
  esac
}

live_images() {
  local resource="$1"
  case "$resource" in
    deployment/server)
      if [[ "${LIVE_IMAGE_MODE:-success}" == "mismatch" ]]; then
        printf '%s\n' "$MISMATCHED_SERVER_IMAGE"
      else
        printf '%s\n' "$SERVER_IMAGE"
      fi
      ;;
    deployment/kfe-service) printf '%s\n' "$KFE_SERVICE_IMAGE" ;;
    deployment/web-page) printf '%s\n' "$WEB_PAGE_IMAGE" ;;
    statefulset/staging-postgres) printf '%s\n' "$POSTGRES_IMAGE" ;;
    statefulset/staging-redis) printf '%s\n' "$REDIS_IMAGE" ;;
    statefulset/staging-bitcoin) printf '%s\n' "$BITCOIN_IMAGE" ;;
    statefulset/staging-lnd) printf '%s\n' "$LND_IMAGE" ;;
    statefulset/staging-tor) printf '%s\n%s\n' "$TOR_IMAGE" "$NODE_IMAGE" ;;
    *) echo "unexpected live resource: $resource" >&2; exit 42 ;;
  esac
}

if [[ "${1:-}" == "kustomize" ]]; then
  emit_manifest
  exit 0
fi

if [[ "${1:-}" == "create" && "${2:-}" == "namespace" ]]; then
  printf '%s\n' 'apiVersion: v1'
  exit 0
fi

if [[ "${1:-}" == "apply" ]]; then
  if [[ " $* " == *" -f - "* ]]; then
    cat >/dev/null
  fi
  exit 0
fi

if [[ "${1:-}" == "-n" && "${3:-}" == "rollout" && "${4:-}" == "status" ]]; then
  exit 0
fi

if [[ "${1:-}" == "-n" && "${3:-}" == "get" && "${4:-}" == "secret" ]]; then
  secret="${5:-}"
  if [[ "$*" == *"jsonpath="*"jdbc-url"* ]]; then
    printf '%s' 'jdbc:postgresql://kerosene-db-headless:5432/kerosene' | base64
  else
    secret_keys "$secret"
  fi
  exit 0
fi

if [[ "${1:-}" == "-n" && "${3:-}" == "get" ]]; then
  live_images "${4:-}"
  exit 0
fi

echo "unexpected kubectl call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

run_staging_deploy() {
  local mode="$1"
  LIVE_IMAGE_MODE="$mode" \
    FAKE_KUBECTL_LOG="$FAKE_KUBECTL_LOG" \
    FAKE_KUSTOMIZE_LOG="$FAKE_KUSTOMIZE_LOG" \
    KUBECTL="$TMP_DIR/bin/kubectl" \
    KUSTOMIZE="$TMP_DIR/bin/kustomize" \
    KEROSENE_SKIP_STAGING_SMOKES=1 \
    SERVER_IMAGE="$SERVER_IMAGE" \
    KFE_SERVICE_IMAGE="$KFE_SERVICE_IMAGE" \
    WEB_PAGE_IMAGE="$WEB_PAGE_IMAGE" \
    NODE_IMAGE="$NODE_IMAGE" \
    TOR_IMAGE="$TOR_IMAGE" \
    POSTGRES_IMAGE="$POSTGRES_IMAGE" \
    REDIS_IMAGE="$REDIS_IMAGE" \
    BITCOIN_IMAGE="$BITCOIN_IMAGE" \
    LND_IMAGE="$LND_IMAGE" \
    MISMATCHED_SERVER_IMAGE="$MISMATCHED_SERVER_IMAGE" \
    bash "$DEPLOY" staging
}

: > "$FAKE_KUBECTL_LOG"
: > "$FAKE_KUSTOMIZE_LOG"
success_output="$(run_staging_deploy success 2>&1)"
assert_contains "$success_output" '[+] staging deploy completed.'

for resource in \
  deployment/server \
  deployment/kfe-service \
  deployment/web-page \
  statefulset/staging-postgres \
  statefulset/staging-redis \
  statefulset/staging-bitcoin \
  statefulset/staging-lnd \
  statefulset/staging-tor; do
  grep -Fq "get ${resource} -o jsonpath=" "$FAKE_KUBECTL_LOG" \
    || fail "live image verification did not query ${resource}"
done

for override in \
  "kerosene/server=${SERVER_IMAGE}" \
  "kerosene/kfe-service=${KFE_SERVICE_IMAGE}" \
  "kerosene/web-page=${WEB_PAGE_IMAGE}" \
  "kerosene/node=${NODE_IMAGE}" \
  "kerosene/tor=${TOR_IMAGE}" \
  "postgres=${POSTGRES_IMAGE}" \
  "redis=${REDIS_IMAGE}" \
  "bitcoin/bitcoin=${BITCOIN_IMAGE}" \
  "lightninglabs/lnd=${LND_IMAGE}"; do
  grep -Fxq "edit set image ${override}" "$FAKE_KUSTOMIZE_LOG" \
    || fail "missing Kustomize override ${override}"
done

: > "$FAKE_KUBECTL_LOG"
set +e
mismatch_output="$(run_staging_deploy mismatch 2>&1)"
mismatch_status=$?
set -e

[[ "$mismatch_status" -eq 2 ]] \
  || fail "mismatched live image returned ${mismatch_status}, expected 2. Output: ${mismatch_output}"
assert_contains "$mismatch_output" 'Live deployment/server does not expose the requested SERVER_IMAGE digest.'
assert_contains "$mismatch_output" "$MISMATCHED_SERVER_IMAGE"
[[ "$mismatch_output" != *'[+] staging deploy completed.'* ]] \
  || fail "mismatched live image was reported as a completed deploy"
grep -Fq 'get deployment/server -o jsonpath=' "$FAKE_KUBECTL_LOG" \
  || fail "mismatch scenario did not query deployment/server"
if grep -Fq 'get deployment/kfe-service -o jsonpath=' "$FAKE_KUBECTL_LOG"; then
  fail "mismatch scenario continued after the first unexpected live image"
fi

echo "[PASS] staged deploy live image digest verification"
