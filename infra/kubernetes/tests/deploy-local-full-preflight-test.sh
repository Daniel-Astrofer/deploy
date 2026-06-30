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

cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "config" && "${2:-}" == "current-context" ]]; then
  echo "error: current-context is not set" >&2
  exit 1
fi
echo "unexpected kubectl call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

set +e
output="$(KEROSENE_KUBERNETES_READY_TIMEOUT=0 KEROSENE_HOST_HOME="$TMP_DIR/no-kube-home" PATH="$TMP_DIR/bin:$PATH" KUBECTL=kubectl "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" --dry-run 2>&1)"
status=$?
set -e

[[ "$status" -ne 0 ]] || fail "deploy-local-full.sh unexpectedly succeeded without Kubernetes context"
assert_contains "$output" "Kubernetes API is not reachable"
assert_contains "$output" "kubectl config current-context"
assert_contains "$output" "bash infra/start.sh"
assert_contains "$output" "host services checked"

mkdir -p "$TMP_DIR/with-kube-home/.kube"
touch "$TMP_DIR/with-kube-home/.kube/config"
cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$CALL_LOG"
if [[ "${1:-}" == "--kubeconfig" && "${3:-}" == "config" && "${4:-}" == "current-context" ]]; then
  echo "fake-context"
  exit 0
fi
if [[ "${1:-}" == "--kubeconfig" && "${3:-}" == "get" && "${4:-}" == "--raw=/readyz" ]]; then
  exit 0
fi
if [[ "${1:-}" == "--kubeconfig" && "${3:-}" == "apply" ]]; then
  exit 0
fi
echo "unexpected kubectl call: $*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

CALL_LOG="$TMP_DIR/kubectl-default.log"
: > "$CALL_LOG"
output="$(KEROSENE_HOST_HOME="$TMP_DIR/with-kube-home" PATH="$TMP_DIR/bin:$PATH" CALL_LOG="$CALL_LOG" KUBECTL=kubectl "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" --dry-run 2>&1)" || fail "deploy-local-full.sh should use default host kubeconfig"
assert_contains "$output" "Kubernetes context: fake-context"
grep -qF -- "--kubeconfig $TMP_DIR/with-kube-home/.kube/config config current-context" "$CALL_LOG" || fail "deploy should pass the default host kubeconfig to kubectl"

echo "[PASS] deploy-local-full.sh cluster preflight"
