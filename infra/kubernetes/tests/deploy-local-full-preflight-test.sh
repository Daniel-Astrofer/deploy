#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SUBJECT="$REPO_ROOT/infra/kubernetes/scripts/deploy-local-full.sh"
TMP_DIR="$(mktemp -d)"

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

mkdir -p "$TMP_DIR/bin" "$TMP_DIR/infra/scripts" "$TMP_DIR/infra/kubernetes/scripts" "$TMP_DIR/infra/kubernetes/overlays/local-full"
cp "$SUBJECT" "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh"
cp "$REPO_ROOT/infra/kubernetes/scripts/local-host-env.sh" "$TMP_DIR/infra/kubernetes/scripts/local-host-env.sh"
chmod +x "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh"

cat > "$TMP_DIR/infra/scripts/host-services.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ensure_local_host_services() {
  echo "host services checked"
}
EOF

cat > "$TMP_DIR/infra/kubernetes/scripts/validate-local-full.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "validated"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/validate-local-full.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/render-local-full-overlay.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [[ -n "\${RENDER_ENV_LOG:-}" ]]; then
  printf '%s\n' \
    "\$KEROSENE_LOCAL_POSTGRES_DATA" \
    "\$KEROSENE_LOCAL_BITCOIN_DATA" \
    "\$KEROSENE_LOCAL_LND_DATA" \
    "\$KEROSENE_LOCAL_LND_PEER_DATA" \
    "\$KEROSENE_LOCAL_ONION_KEYS_PATH" > "\$RENDER_ENV_LOG"
fi
echo "$TMP_DIR/infra/kubernetes/overlays/local-full"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/render-local-full-overlay.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/ensure-vault-mesh-lab.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "127.0.0.1"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/ensure-vault-mesh-lab.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/ensure-local-monitoring.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/ensure-local-monitoring.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/ensure-local-cluster.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cat >&2 <<'MSG'
[!] Kubernetes API is not reachable or no kubectl context is selected.
    Check the active context:
      kubectl config current-context
    Start or select your local cluster, then retry:
      bash infra/start.sh
MSG
exit 1
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/ensure-local-cluster.sh"

cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "unexpected kubectl call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

set +e
output="$(
  KEROSENE_AUTO_CREATE_CLUSTER=0 \
  KEROSENE_KUBERNETES_READY_TIMEOUT=0 \
  KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" \
  PATH="$TMP_DIR/bin:$PATH" \
  KUBECTL=kubectl \
  "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" --dry-run 2>&1
)"
status=$?
set -e

[[ "$status" -ne 0 ]] || fail "deploy-local-full.sh unexpectedly succeeded without Kubernetes context"
assert_contains "$output" "Kubernetes API is not reachable"
assert_contains "$output" "kubectl config current-context"
assert_contains "$output" "bash infra/start.sh"
assert_contains "$output" "host services checked"

mkdir -p "$TMP_DIR/with-kube-home/.kube"
touch "$TMP_DIR/with-kube-home/.kube/config"

cat > "$TMP_DIR/infra/kubernetes/scripts/ensure-local-cluster.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "[*] Kubernetes context: fake-context"
exit 0
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/ensure-local-cluster.sh"

cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$CALL_LOG"

# Strip leading --kubeconfig <path> so later parsing is uniform.
if [[ "${1:-}" == "--kubeconfig" ]]; then
  shift 2
fi

case "${1:-}" in
  config)
    if [[ "${2:-}" == "current-context" ]]; then
      echo "fake-context"
      exit 0
    fi
    ;;
  get)
    if [[ "${2:-}" == "--raw=/readyz" ]]; then
      exit 0
    fi
    if [[ "${2:-}" == "namespace" || "${2:-}" == "ns" ]]; then
      # Namespace not yet present on first dry-run.
      exit 1
    fi
    if [[ "${2:-}" == "pv" ]]; then
      case "${3:-}" in
        local-postgres-data) printf /legacy/postgres-data ;;
        local-bitcoin-data) printf /legacy/bitcoin-data ;;
        local-lnd-data) printf /legacy/lnd-data ;;
        local-lnd-peer-data) printf /legacy/lnd-peer-data ;;
        kerosene-local-tor-onion-keys) printf /current/tor/keys ;;
        *) exit 1 ;;
      esac
      exit 0
    fi
    ;;
  create)
    if [[ "${2:-}" == "namespace" ]]; then
      echo "namespace created"
      exit 0
    fi
    ;;
  apply)
    exit 0
    ;;
esac

echo "unexpected kubectl call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

CALL_LOG="$TMP_DIR/kubectl-default.log"
RENDER_ENV_LOG="$TMP_DIR/render-env.log"
: > "$CALL_LOG"
output="$(
  KEROSENE_HOST_HOME="$TMP_DIR/with-kube-home" \
  PATH="$TMP_DIR/bin:$PATH" \
  CALL_LOG="$CALL_LOG" \
  RENDER_ENV_LOG="$RENDER_ENV_LOG" \
  KUBECTL=kubectl \
  "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" --dry-run 2>&1
)" || fail "deploy-local-full.sh should use default host kubeconfig"
assert_contains "$output" "Kubernetes context: fake-context"
grep -qF -- "--kubeconfig $TMP_DIR/with-kube-home/.kube/config" "$CALL_LOG" \
  || fail "deploy should pass the default host kubeconfig to kubectl"
cat > "$TMP_DIR/expected-render-env.log" <<'EOF'
/legacy/postgres-data
/legacy/bitcoin-data
/legacy/lnd-data
/legacy/lnd-peer-data
/current/tor/keys
EOF
cmp "$TMP_DIR/expected-render-env.log" "$RENDER_ENV_LOG" \
  || fail "deploy should preserve immutable host paths from existing PVs"

echo "[PASS] deploy-local-full.sh cluster preflight"
