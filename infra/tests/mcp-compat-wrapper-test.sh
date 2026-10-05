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
cp "$REPO_ROOT/infra/mcp/kerosene-readonly-mcp" "$TMP_DIR/infra/mcp/kerosene-readonly-mcp"
cp "$REPO_ROOT/infra/mcp/kerosene-readonly-mcp-wrapper" "$TMP_DIR/infra/mcp/kerosene-readonly-mcp-wrapper"
chmod +x "$TMP_DIR/infra/mcp/kerosene-mcp-wrapper" "$TMP_DIR/infra/mcp/kerosene-readonly-mcp-wrapper"
chmod +x "$TMP_DIR/infra/mcp/kerosene-readonly-mcp"

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
  CALL_LOG="$LOG_FILE" sh infra/mcp/kerosene-readonly-mcp-wrapper --root workspace
)

grep -qxF "infra-mcp:--help" "$LOG_FILE" || fail "infra/mcp/kerosene-mcp-wrapper should delegate to infra/mcp/kerosene-mcp"
grep -qxF "infra-mcp:--server --readonly --root workspace" "$LOG_FILE" || fail "read-only wrapper should enforce server and read-only modes"

mkdir -p "$TMP_DIR/read-only-root"
cat > "$TMP_DIR/read-only-requests.jsonl" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"Kerosene.Command","arguments":{"command":"touch readonly-escape"}}}
EOF

"$REPO_ROOT/infra/mcp/kerosene-readonly-mcp" \
  --root "$TMP_DIR/read-only-root" \
  < "$TMP_DIR/read-only-requests.jsonl" \
  > "$TMP_DIR/read-only-responses.jsonl"

python3 - "$TMP_DIR/read-only-responses.jsonl" <<'PY'
import json
import sys

responses = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
tool_names = {tool["name"] for tool in responses[0]["result"]["tools"]}
expected = {
    "Kerosene.Project",
    "Kerosene.ReadCode",
    "Kerosene.Search",
    "Kerosene.System",
}
if tool_names != expected:
    raise SystemExit(f"unexpected read-only tools: {sorted(tool_names)}")
blocked = responses[1]["result"]
if not blocked.get("isError") or "unavailable in read-only mode" not in blocked["content"][0]["text"]:
    raise SystemExit("mutating tool was not rejected in read-only mode")
PY

[[ ! -e "$TMP_DIR/read-only-root/readonly-escape" ]] || fail "read-only MCP executed a command"

echo "[PASS] MCP compatibility wrappers"
