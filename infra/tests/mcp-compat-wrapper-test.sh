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

for script in scripts/kerosene-mcp scripts/kerosene-readonly-mcp; do
  [[ -x "$REPO_ROOT/$script" ]] || fail "missing executable compatibility wrapper: $script"
done

mkdir -p "$TMP_DIR/scripts" "$TMP_DIR/infra/mcp"
cp "$REPO_ROOT/scripts/kerosene-mcp" "$TMP_DIR/scripts/kerosene-mcp"
cp "$REPO_ROOT/scripts/kerosene-readonly-mcp" "$TMP_DIR/scripts/kerosene-readonly-mcp"
chmod +x "$TMP_DIR/scripts/kerosene-mcp" "$TMP_DIR/scripts/kerosene-readonly-mcp"

cat > "$TMP_DIR/infra/mcp/kerosene-mcp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "infra-mcp:$*" >> "$CALL_LOG"
EOF
chmod +x "$TMP_DIR/infra/mcp/kerosene-mcp"

: > "$LOG_FILE"
(
  cd "$TMP_DIR"
  CALL_LOG="$LOG_FILE" sh scripts/kerosene-mcp --help
  CALL_LOG="$LOG_FILE" sh scripts/kerosene-readonly-mcp --readonly
)

grep -qxF "infra-mcp:--help" "$LOG_FILE" || fail "scripts/kerosene-mcp should delegate to infra/mcp/kerosene-mcp"
grep -qxF "infra-mcp:--readonly" "$LOG_FILE" || fail "scripts/kerosene-readonly-mcp should delegate to infra/mcp/kerosene-mcp"

echo "[PASS] MCP compatibility wrappers"
