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

mkdir -p "$TMP_DIR/infra/scripts"
for script in start stop recreate status logs test; do
  subject="$REPO_ROOT/infra/$script.sh"
  [[ -f "$subject" ]] || fail "missing public entrypoint: infra/$script.sh"
  cp "$subject" "$TMP_DIR/infra/$script.sh"
  chmod +x "$TMP_DIR/infra/$script.sh"
done

cat > "$TMP_DIR/infra/scripts/quorum.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "quorum:$*" >> "$CALL_LOG"
EOF
chmod +x "$TMP_DIR/infra/scripts/quorum.sh"

: > "$LOG_FILE"

(
  cd "$TMP_DIR"
  CALL_LOG="$LOG_FILE" bash infra/start.sh --skip-image-import
  CALL_LOG="$LOG_FILE" bash infra/stop.sh
  CALL_LOG="$LOG_FILE" bash infra/recreate.sh --no-build
  CALL_LOG="$LOG_FILE" bash infra/status.sh
  CALL_LOG="$LOG_FILE" bash infra/logs.sh server --tail 20
  CALL_LOG="$LOG_FILE" bash infra/test.sh
)

grep -qxF "quorum:start --skip-image-import" "$LOG_FILE" || fail "start.sh did not route to quorum start"
grep -qxF "quorum:stop" "$LOG_FILE" || fail "stop.sh did not route to quorum stop"
grep -qxF "quorum:recreate --no-build" "$LOG_FILE" || fail "recreate.sh did not route to quorum recreate"
grep -qxF "quorum:status" "$LOG_FILE" || fail "status.sh did not route to quorum status"
grep -qxF "quorum:logs server --tail 20" "$LOG_FILE" || fail "logs.sh did not route to quorum logs"
grep -qxF "quorum:test" "$LOG_FILE" || fail "test.sh did not route to quorum test"

echo "[PASS] infra public entrypoints"
