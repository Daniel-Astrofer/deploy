#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SUBJECT="$REPO_ROOT/infra/kubernetes/scripts/deploy-local-full.sh"
TMP_DIR="$(mktemp -d)"
LOG_FILE="$TMP_DIR/calls.log"

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
ensure_local_host_services() { :; }
EOF

cat > "$TMP_DIR/infra/kubernetes/scripts/validate-local-full.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "validated"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/validate-local-full.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "import attempted"
exit 1
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/import-local-docker-images.sh"

cat > "$TMP_DIR/bin/kubectl" <<EOF
#!/usr/bin/env bash
set -euo pipefail
echo "\$*" >> "$LOG_FILE"
if [[ "\${1:-}" == "config" && "\${2:-}" == "current-context" ]]; then
  echo "test-context"
  exit 0
fi
if [[ "\${1:-}" == "cluster-info" ]]; then
  echo "cluster ok"
  exit 0
fi
if [[ "\${1:-}" == "apply" ]]; then
  echo "applied"
  exit 0
fi
if [[ "\${1:-}" == "-n" && "\${2:-}" == "kerosene-local" && "\${3:-}" == "get" ]]; then
  if [[ "\${4:-}" == "deploy/tor-onion" ]]; then
    exit 1
  fi
  echo "objects"
  exit 0
fi
echo "unexpected kubectl call: \$*" >&2
exit 42
EOF
chmod +x "$TMP_DIR/bin/kubectl"

set +e
output="$(PATH="$TMP_DIR/bin:$PATH" KUBECTL=kubectl "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" 2>&1)"
status=$?
set -e

[[ "$status" -eq 0 ]] || fail "deploy-local-full.sh exited $status instead of continuing after import failure. Output: $output"

assert_contains "$output" "Image import failed."
assert_contains "$output" "Continuing with images already available to the cluster."
grep -qxF "apply -k $TMP_DIR/infra/kubernetes/overlays/local-full" "$LOG_FILE" || fail "overlay was not applied after import fallback"

echo "[PASS] deploy-local-full.sh image import fallback"
