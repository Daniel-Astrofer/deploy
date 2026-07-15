#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SUBJECT="$REPO_ROOT/infra/kubernetes/deploy.sh"
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

mkdir -p "$TMP_DIR/infra/kubernetes/scripts"
cp "$SUBJECT" "$TMP_DIR/infra/kubernetes/deploy.sh"
chmod +x "$TMP_DIR/infra/kubernetes/deploy.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/deploy.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "generic deploy help: local staging production"
  exit 0
fi
echo "generic:$*" >> "$CALL_LOG"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/deploy.sh"

cat > "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "local-full:$*" >> "$CALL_LOG"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/scripts/deploy-local-full.sh"

: > "$LOG_FILE"

help_output="$(CALL_LOG="$LOG_FILE" "$TMP_DIR/infra/kubernetes/deploy.sh" --help)"
assert_contains "$help_output" "local-full"
assert_contains "$help_output" "bash infra/start.sh"
assert_contains "$help_output" "infra/kubernetes/deploy.sh local-full --wait"

CALL_LOG="$LOG_FILE" "$TMP_DIR/infra/kubernetes/deploy.sh"
grep -qxF "local-full:--wait" "$LOG_FILE" || fail "default invocation did not delegate to deploy-local-full.sh --wait"

CALL_LOG="$LOG_FILE" "$TMP_DIR/infra/kubernetes/deploy.sh" --dry-run
grep -qxF "local-full:--dry-run" "$LOG_FILE" || fail "options-only invocation did not default to local-full"

CALL_LOG="$LOG_FILE" "$TMP_DIR/infra/kubernetes/deploy.sh" local-full --dry-run
grep -qxF "local-full:--dry-run" "$LOG_FILE" || fail "local-full did not delegate to deploy-local-full.sh"

CALL_LOG="$LOG_FILE" "$TMP_DIR/infra/kubernetes/deploy.sh" local --dry-run
grep -qxF "generic:local --dry-run" "$LOG_FILE" || fail "local did not delegate to generic deploy.sh"

echo "[PASS] infra/kubernetes/deploy.sh entrypoint routing"
