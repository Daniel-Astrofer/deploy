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

for script in infra/mcp/kerosene-mcp-wrapper infra/mcp/kerosene-readonly-mcp-wrapper; do
  [[ -x "$REPO_ROOT/$script" ]] || fail "missing executable compatibility wrapper: $script"
done

mkdir -p "$TMP_DIR/infra/mcp"
cp "$REPO_ROOT/infra/mcp/kerosene-mcp-wrapper" "$TMP_DIR/infra/mcp/kerosene-mcp-wrapper"
cp "$REPO_ROOT/infra/mcp/kerosene-readonly-mcp-wrapper" "$TMP_DIR/infra/mcp/kerosene-readonly-mcp-wrapper"
chmod +x "$TMP_DIR/infra/mcp/kerosene-mcp-wrapper" "$TMP_DIR/infra/mcp/kerosene-readonly-mcp-wrapper"

cat > "$TMP_DIR/infra/mcp/kerosene-mcp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "infra-mcp:$*" >> "$CALL_LOG"
EOF
chmod +x "$TMP_DIR/infra/mcp/kerosene-mcp"

: > "$LOG_FILE"
(
  cd "$TMP_DIR"
  CALL_LOG="$LOG_FILE" sh infra/mcp/kerosene-mcp-wrapper --help
  CALL_LOG="$LOG_FILE" sh infra/mcp/kerosene-readonly-mcp-wrapper --readonly
)

grep -qxF "infra-mcp:--help" "$LOG_FILE" || fail "infra/mcp/kerosene-mcp-wrapper should delegate to infra/mcp/kerosene-mcp"
grep -qxF "infra-mcp:--readonly" "$LOG_FILE" || fail "infra/mcp/kerosene-readonly-mcp-wrapper should delegate to infra/mcp/kerosene-mcp"

echo "[PASS] MCP compatibility wrappers"
