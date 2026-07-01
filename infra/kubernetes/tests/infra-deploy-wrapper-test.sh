#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SUBJECT="$REPO_ROOT/infra/deploy.sh"
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

[[ -f "$SUBJECT" ]] || fail "infra/deploy.sh is missing"

mkdir -p "$TMP_DIR/infra/kubernetes"
cp "$SUBJECT" "$TMP_DIR/infra/deploy.sh"
chmod +x "$TMP_DIR/infra/deploy.sh"

cat > "$TMP_DIR/infra/kubernetes/deploy.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "kubernetes:$*" >> "$CALL_LOG"
EOF
chmod +x "$TMP_DIR/infra/kubernetes/deploy.sh"

: > "$LOG_FILE"

(
  cd "$TMP_DIR/infra"
  CALL_LOG="$LOG_FILE" ./deploy.sh
)
grep -qxF "kubernetes:" "$LOG_FILE" || fail "infra/deploy.sh did not delegate default invocation"

(
  cd "$TMP_DIR/infra"
  CALL_LOG="$LOG_FILE" ./deploy.sh --dry-run
)
grep -qxF "kubernetes:--dry-run" "$LOG_FILE" || fail "infra/deploy.sh did not forward options"

(
  cd "$TMP_DIR"
  CALL_LOG="$LOG_FILE" bash infra/deploy.sh local --dry-run
)
grep -qxF "kubernetes:local --dry-run" "$LOG_FILE" || fail "infra/deploy.sh did not forward environment arguments"

echo "[PASS] infra/deploy.sh wrapper"
