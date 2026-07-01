#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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

mkdir -p "$TMP_DIR/infra/scripts" "$TMP_DIR/infra/kubernetes/scripts" "$TMP_DIR/infra/kubernetes/tests" "$TMP_DIR/infra/tests"
cp "$REPO_ROOT/infra/scripts/quorum.sh" "$TMP_DIR/infra/scripts/quorum.sh"
chmod +x "$TMP_DIR/infra/scripts/quorum.sh"

for script in apply status logs validate-local-full; do
  cat > "$TMP_DIR/infra/kubernetes/scripts/$script.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
echo "$script:\$*" >> "\$CALL_LOG"
EOF
  chmod +x "$TMP_DIR/infra/kubernetes/scripts/$script.sh"
done

cat > "$TMP_DIR/bin-kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "kubectl:$*" >> "$CALL_LOG"
if [[ "${1:-}" == "-n" && "${3:-}" == "get" ]]; then
  exit 0
fi
exit 0
EOF
chmod +x "$TMP_DIR/bin-kubectl"

: > "$LOG_FILE"

(
  cd "$TMP_DIR"
  CALL_LOG="$LOG_FILE" KUBECTL="$TMP_DIR/bin-kubectl" bash infra/scripts/quorum.sh start --skip-image-import
  CALL_LOG="$LOG_FILE" KUBECTL="$TMP_DIR/bin-kubectl" bash infra/scripts/quorum.sh status
  CALL_LOG="$LOG_FILE" KUBECTL="$TMP_DIR/bin-kubectl" bash infra/scripts/quorum.sh logs server --tail 10
  CALL_LOG="$LOG_FILE" KUBECTL="$TMP_DIR/bin-kubectl" bash infra/scripts/quorum.sh stop >/dev/null
  CALL_LOG="$LOG_FILE" KUBECTL="$TMP_DIR/bin-kubectl" bash infra/scripts/quorum.sh recreate --no-build >/dev/null
)

grep -qxF "apply:--wait --skip-image-import" "$LOG_FILE" || fail "start did not call apply --wait"
grep -qxF "status:" "$LOG_FILE" || fail "status did not call status script"
grep -qxF "logs:server --tail 10" "$LOG_FILE" || fail "logs did not call logs script"
grep -qF "kubectl:-n kerosene-local scale deployment/server --replicas=0" "$LOG_FILE" || fail "stop did not scale server down"
grep -qxF "apply:--wait --skip-image-import" "$LOG_FILE" || fail "recreate --no-build did not skip image import"

echo "[PASS] infra quorum dispatcher"
